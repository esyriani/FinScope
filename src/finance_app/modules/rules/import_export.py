"""Import and export workflows for the rules feature."""

import csv
import io
import re
from collections.abc import Mapping, MutableMapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import case, delete, func, select, update

from finance_app.core.category_sql import category_label_expression
from finance_app.core.constants import (
    CATEGORY_RULE_DIRECTION_ANY,
    CATEGORY_RULE_DIRECTIONS,
    CATEGORY_RULE_SOURCE_AUTOMATIC,
    CATEGORY_RULE_SOURCE_MANUAL,
    IMPORTABLE_CATEGORY_RULE_SOURCES,
    UNKNOWN_CATEGORY,
)
from finance_app.core.csv_export import csv_export_value
from finance_app.core.money import MoneyValue, money_to_float, parse_money_text
from finance_app.database.engine import db_core_transaction
from finance_app.database.tables import (
    accounts as accounts_table,
)
from finance_app.database.tables import (
    category_rules as category_rules_table,
)
from finance_app.database.tables import (
    merchants as merchants_table,
)
from finance_app.database.tables import (
    transactions as transactions_table,
)
from finance_app.modules.categories.service import clean_category_name, normalize_merchant_description
from finance_app.modules.categories.taxonomy import get_rule_tags_by_rule_id, normalize_tag_names
from finance_app.modules.rules.forms import validate_rule_tags_for_direction

from .repository import (
    category_rule_exists,
    ensure_import_category,
    existing_category_names,
    insert_imported_rule,
    remove_imported_categories,
    resolve_rule_account_id,
    resolve_rule_merchant_id,
    restore_category_rules,
    rule_reference_count,
    rule_snapshots_equal,
    snapshot_category_rules,
    snapshot_rule_by_id,
    snapshot_transaction_rule_refs,
)

RULE_IMPORT_MODE_ADD = "add"
RULE_IMPORT_MODE_OVERRIDE = "override"
RULE_IMPORT_MODES = {RULE_IMPORT_MODE_ADD, RULE_IMPORT_MODE_OVERRIDE}
RULE_EXPORT_COLUMNS = (
    "keyword",
    "account_name",
    "merchant_name",
    "category",
    "tags",
    "amount_min",
    "amount_max",
    "direction",
    "source",
    "created_at",
)
RULE_SOURCE_VALUES = set(IMPORTABLE_CATEGORY_RULE_SOURCES)
IMPORT_RULE_LINE_NUMBER = "_line_number"


@dataclass(frozen=True)
class RuleCsvParseResult:
    """Represent CSV parsing output with recoverable row errors."""

    total_rows: int
    rules: tuple[dict[str, Any], ...]
    row_errors: tuple[str, ...]


@dataclass(frozen=True)
class RuleImportPreview:
    """Represent a read-only rule import plan.

    Attributes:
        mode: Import mode that will be used if the preview is confirmed.
        total_rows: Parsed non-empty CSV rows.
        proposed_rules: Rules that would be inserted by the import.
        skipped_existing: Add-mode rows skipped because an equivalent rule
            already exists.
        skipped_duplicate: Rows skipped because the same import key appears
            earlier in the file.
        row_errors: Row-level errors skipped during parsing, preview, or import.
        replaced_rules: Override-mode count of current rules that would be
            removed before import.
        cleared_transaction_rule_refs: Override-mode transaction rule
            references that would be cleared.
    """

    mode: str
    total_rows: int
    proposed_rules: tuple[dict[str, Any], ...]
    skipped_existing: int = 0
    skipped_duplicate: int = 0
    row_errors: tuple[str, ...] = ()
    replaced_rules: int = 0
    cleared_transaction_rule_refs: int = 0


def export_rules_csv(conn: Any = None) -> str:
    """Export rules CSV."""
    if conn is None:
        with db_core_transaction() as conn:
            return export_rules_csv(conn)

    rows = export_rule_rows(conn)
    tags_by_rule_id = get_rule_tags_by_rule_id(conn, [row["id"] for row in rows])
    for row in rows:
        row["tags"] = tags_by_rule_id.get(row["id"], [])

    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=RULE_EXPORT_COLUMNS)
    writer.writeheader()
    for row in rows:
        export_row = {
            "keyword": row["keyword"],
            "account_name": row["account_name"] or "",
            "merchant_name": row["merchant_name"] or "",
            "category": row["category"],
            "tags": "; ".join(row["tags"]),
            "amount_min": format_export_amount(row["amount_min"]),
            "amount_max": format_export_amount(row["amount_max"]),
            "direction": row["direction"] or CATEGORY_RULE_DIRECTION_ANY,
            "source": row["source"],
            "created_at": row["created_at"],
        }
        writer.writerow({column: csv_export_value(export_row[column]) for column in RULE_EXPORT_COLUMNS})
    return output.getvalue()


def format_export_amount(value: MoneyValue | None) -> str:
    """Return the CSV amount text for optional rule bounds."""
    return "" if value is None else str(money_to_float(value))


def export_rule_rows(conn: Any) -> list[dict[str, Any]]:
    """Return rule rows formatted for CSV export."""
    rows = (
        conn.execute(
            select(
                category_rules_table.c.id,
                category_rules_table.c.keyword,
                category_rules_table.c.direction,
                accounts_table.c.name.label("account_name"),
                merchants_table.c.merchant_key.label("merchant_name"),
                category_label_expression(category_rules_table, UNKNOWN_CATEGORY).label("category"),
                category_rules_table.c.amount_min,
                category_rules_table.c.amount_max,
                category_rules_table.c.source,
                category_rules_table.c.created_at,
            )
            .select_from(
                category_rules_table.outerjoin(
                    accounts_table,
                    accounts_table.c.id == category_rules_table.c.account_id,
                ).outerjoin(
                    merchants_table,
                    merchants_table.c.id == category_rules_table.c.merchant_id,
                )
            )
            .order_by(
                case(
                    (category_rules_table.c.source == CATEGORY_RULE_SOURCE_MANUAL, 0),
                    (category_rules_table.c.source == CATEGORY_RULE_SOURCE_AUTOMATIC, 1),
                    else_=2,
                ),
                func.lower(category_label_expression(category_rules_table, UNKNOWN_CATEGORY)),
                category_label_expression(category_rules_table, UNKNOWN_CATEGORY),
                func.lower(category_rules_table.c.keyword),
                category_rules_table.c.keyword,
                category_rules_table.c.amount_min,
                category_rules_table.c.amount_max,
            )
        )
        .mappings()
        .fetchall()
    )
    return [dict(row) for row in rows]


def import_rules_job(raw_text: str, mode: str, undo_state: MutableMapping[str, Any]) -> str:
    """Import rules job."""
    parsed = parse_rules_csv_with_errors(raw_text)
    if not parsed.rules:
        raise_no_importable_rules(parsed.row_errors)

    with db_core_transaction() as conn:
        if mode == RULE_IMPORT_MODE_OVERRIDE:
            return import_rules_override(
                conn,
                parsed.rules,
                undo_state,
                row_errors=parsed.row_errors,
                skip_invalid_rows=True,
            )

        return import_rules_add(
            conn,
            parsed.rules,
            undo_state,
            row_errors=parsed.row_errors,
            skip_invalid_rows=True,
        )


def preview_rules_import(conn: Any, raw_text: str, mode: str) -> RuleImportPreview:
    """Return a read-only import plan using the normal import parser.

    Args:
        conn: Open SQLAlchemy Core connection used for current-rule checks and
            account or merchant resolution.
        raw_text: Uploaded CSV text.
        mode: Import mode, either add or override.

    Returns:
        A RuleImportPreview describing rows that would be imported, skipped, or
        replaced. The function does not write rules, categories, or merchants.

    Raises:
        ValueError: If the mode or CSV content is invalid.
    """
    if mode not in RULE_IMPORT_MODES:
        raise ValueError("Choose whether to add new rules or override existing rules.")

    parsed = parse_rules_csv_with_errors(raw_text)
    if not parsed.rules:
        raise_no_importable_rules(parsed.row_errors)

    if mode == RULE_IMPORT_MODE_OVERRIDE:
        return preview_rules_import_override(
            conn,
            parsed.rules,
            total_rows=parsed.total_rows,
            row_errors=parsed.row_errors,
        )

    return preview_rules_import_add(
        conn,
        parsed.rules,
        total_rows=parsed.total_rows,
        row_errors=parsed.row_errors,
    )


def preview_rules_import_add(
    conn: Any,
    imported_rules: Sequence[Mapping[str, Any]],
    total_rows: int | None = None,
    row_errors: Sequence[str] = (),
) -> RuleImportPreview:
    """Return a read-only add-mode import plan."""
    proposed_rules: list[dict[str, Any]] = []
    skipped_existing = 0
    skipped_duplicate = 0
    skipped_errors = list(row_errors)
    seen_keys: set[tuple[Any, ...]] = set()

    for index, rule in enumerate(imported_rules, start=1):
        try:
            key = rule_import_key(rule)
            if key in seen_keys:
                skipped_duplicate += 1
                continue
            seen_keys.add(key)

            validate_import_rule_for_write(conn, rule)
            if category_rule_exists(conn, rule):
                skipped_existing += 1
                continue

            proposed_rules.append(preview_imported_rule(conn, rule, -index))
        except ValueError as exc:
            skipped_errors.append(import_rule_error_message(rule, exc))

    if not proposed_rules and skipped_errors and not skipped_existing and not skipped_duplicate:
        raise_no_importable_rules(skipped_errors)

    return RuleImportPreview(
        mode=RULE_IMPORT_MODE_ADD,
        total_rows=total_rows if total_rows is not None else len(imported_rules),
        proposed_rules=tuple(proposed_rules),
        skipped_existing=skipped_existing,
        skipped_duplicate=skipped_duplicate,
        row_errors=tuple(skipped_errors),
    )


def preview_rules_import_override(
    conn: Any,
    imported_rules: Sequence[Mapping[str, Any]],
    total_rows: int | None = None,
    row_errors: Sequence[str] = (),
) -> RuleImportPreview:
    """Return a read-only override-mode import plan."""
    proposed_rules: list[dict[str, Any]] = []
    skipped_duplicate = 0
    skipped_errors = list(row_errors)
    seen_keys: set[tuple[Any, ...]] = set()

    for index, rule in enumerate(imported_rules, start=1):
        try:
            key = rule_import_key(rule)
            if key in seen_keys:
                skipped_duplicate += 1
                continue
            seen_keys.add(key)
            validate_import_rule_for_write(conn, rule)
            proposed_rules.append(preview_imported_rule(conn, rule, -index))
        except ValueError as exc:
            skipped_errors.append(import_rule_error_message(rule, exc))

    if not proposed_rules and skipped_errors and not skipped_duplicate:
        raise_no_importable_rules(skipped_errors)

    replaced_rules = conn.execute(select(func.count()).select_from(category_rules_table)).scalar_one()
    cleared_refs = conn.execute(
        select(func.count()).select_from(transactions_table).where(transactions_table.c.category_rule_id.is_not(None))
    ).scalar_one()
    return RuleImportPreview(
        mode=RULE_IMPORT_MODE_OVERRIDE,
        total_rows=total_rows if total_rows is not None else len(imported_rules),
        proposed_rules=tuple(proposed_rules),
        skipped_duplicate=skipped_duplicate,
        row_errors=tuple(skipped_errors),
        replaced_rules=replaced_rules,
        cleared_transaction_rule_refs=cleared_refs,
    )


def preview_imported_rule(conn: Any, rule: Mapping[str, Any], synthetic_id: int) -> dict[str, Any]:
    """Return an import rule mapping suitable for read-only matching.

    Merchant-bound imports that reference a new merchant receive a synthetic
    negative merchant id so preview matching mirrors the future stored rule: it
    will not keyword-match unrelated existing transactions.
    """
    merchant_id = resolve_rule_merchant_id(conn, rule, create=False)
    if merchant_id is None and str(rule.get("merchant_name") or "").strip():
        merchant_id = synthetic_id

    return {
        "id": synthetic_id,
        "account_id": resolve_rule_account_id(conn, rule, require_existing=True),
        "merchant_id": merchant_id,
        "merchant_name": rule.get("merchant_name") or "",
        "keyword": rule["keyword"],
        "category": rule["category"],
        "category_id": None,
        "amount_min": rule.get("amount_min"),
        "amount_max": rule.get("amount_max"),
        "direction": rule.get("direction") or CATEGORY_RULE_DIRECTION_ANY,
        "source": rule["source"],
        "ai_approved": int(rule.get("ai_approved") or 0),
        "tags": list(rule.get("tags") or []),
    }


def import_rules_add(
    conn: Any,
    imported_rules: Sequence[Mapping[str, Any]],
    undo_state: MutableMapping[str, Any],
    row_errors: Sequence[str] = (),
    skip_invalid_rows: bool = False,
) -> str:
    """Import rules add."""
    inserted_rules: list[dict[str, Any] | None] = []
    skipped_existing = 0
    skipped_duplicate = 0
    skipped_errors = list(row_errors)
    seen_keys: set[tuple[Any, ...]] = set()
    existing_categories = existing_category_names(conn)
    created_categories: list[str] = []

    # Track duplicate import keys inside this file separately from rules that
    # already exist in the database so the final job message is actionable.
    for rule in imported_rules:
        try:
            key = rule_import_key(rule)
            if key in seen_keys:
                skipped_duplicate += 1
                continue
            seen_keys.add(key)

            validate_import_rule_for_write(conn, rule)
            if category_rule_exists(conn, rule):
                skipped_existing += 1
                continue

            ensure_import_category(conn, rule["category"], existing_categories, created_categories)
            rule_id = insert_imported_rule(conn, rule)
            inserted_rules.append(snapshot_rule_by_id(conn, rule_id))
        except ValueError as exc:
            if not skip_invalid_rows:
                raise
            skipped_errors.append(import_rule_error_message(rule, exc))

    if not inserted_rules and skipped_errors and not skipped_existing and not skipped_duplicate:
        raise_no_importable_rules(skipped_errors)

    undo_state["mode"] = RULE_IMPORT_MODE_ADD
    undo_state["inserted_rules"] = inserted_rules
    undo_state["created_categories"] = created_categories
    undo_state["row_errors"] = skipped_errors

    message = f"Imported {len(inserted_rules)} new rule"
    message += "" if len(inserted_rules) == 1 else "s"
    message += "."
    if skipped_existing:
        message += f" Skipped {skipped_existing} existing rule{'' if skipped_existing == 1 else 's'}."
    if skipped_duplicate:
        message += f" Skipped {skipped_duplicate} duplicate row{'' if skipped_duplicate == 1 else 's'} in the file."
    message += invalid_row_summary(skipped_errors)

    return message


def import_rules_override(
    conn: Any,
    imported_rules: Sequence[Mapping[str, Any]],
    undo_state: MutableMapping[str, Any],
    row_errors: Sequence[str] = (),
    skip_invalid_rows: bool = False,
) -> str:
    """Import rules override."""
    valid_rules, skipped_errors = import_rules_valid_for_write(
        conn,
        imported_rules,
        row_errors=row_errors,
        skip_invalid_rows=skip_invalid_rows,
    )
    if not valid_rules:
        raise_no_importable_rules(skipped_errors)

    before_rules = snapshot_category_rules(conn)
    before_transaction_refs = snapshot_transaction_rule_refs(conn)
    inserted_count = 0
    skipped_duplicate = 0
    seen_keys: set[tuple[Any, ...]] = set()
    existing_categories = existing_category_names(conn)
    created_categories: list[str] = []

    # Clear references before replacing rules so transaction rows cannot point
    # at deleted rule IDs while the override is in progress.
    conn.execute(
        update(transactions_table)
        .where(transactions_table.c.category_rule_id.is_not(None))
        .values(category_rule_id=None)
    )
    conn.execute(delete(category_rules_table))

    for rule in valid_rules:
        key = rule_import_key(rule)
        if key in seen_keys:
            skipped_duplicate += 1
            continue
        seen_keys.add(key)
        ensure_import_category(conn, rule["category"], existing_categories, created_categories)
        insert_imported_rule(conn, rule)
        inserted_count += 1

    after_rules = snapshot_category_rules(conn)
    undo_state["mode"] = RULE_IMPORT_MODE_OVERRIDE
    undo_state["before_rules"] = before_rules
    undo_state["after_rules"] = after_rules
    undo_state["transaction_rule_refs"] = before_transaction_refs
    undo_state["created_categories"] = created_categories
    undo_state["row_errors"] = skipped_errors

    message = f"Replaced rules with {inserted_count} imported rule"
    message += "" if inserted_count == 1 else "s"
    message += "."
    if before_transaction_refs:
        message += (
            f" Cleared rule references on {len(before_transaction_refs)} transaction"
            f"{'' if len(before_transaction_refs) == 1 else 's'}."
        )
    if skipped_duplicate:
        message += f" Skipped {skipped_duplicate} duplicate row{'' if skipped_duplicate == 1 else 's'} in the file."
    message += invalid_row_summary(skipped_errors)

    return message


def undo_import_rules_job(undo_state: Mapping[str, Any]) -> str:
    """Undo import rules job."""
    mode = undo_state.get("mode")
    if mode == RULE_IMPORT_MODE_OVERRIDE:
        return undo_rules_override_import(undo_state)
    if mode == RULE_IMPORT_MODE_ADD:
        return undo_rules_add_import(undo_state)

    return "No imported rules were recorded for undo."


def undo_rules_add_import(undo_state: Mapping[str, Any]) -> str:
    """Undo rules add import."""
    inserted_rules = undo_state.get("inserted_rules") or []
    if not inserted_rules:
        return "No imported rules needed to be removed."

    deleted_count = 0
    skipped_count = 0
    removed_categories = 0

    with db_core_transaction() as conn:
        for rule in inserted_rules:
            current_rule = snapshot_rule_by_id(conn, rule["id"])
            # Only delete rows that still match the imported snapshot and are
            # not referenced by transactions created after the import.
            if current_rule is None or not rule_snapshots_equal([current_rule], [rule]):
                skipped_count += 1
                continue

            if rule_reference_count(conn, rule["id"]):
                skipped_count += 1
                continue

            conn.execute(delete(category_rules_table).where(category_rules_table.c.id == rule["id"]))
            deleted_count += 1

        removed_categories = remove_imported_categories(
            conn,
            undo_state.get("created_categories") or [],
        )

    message = f"Removed {deleted_count} imported rule{'' if deleted_count == 1 else 's'}."
    if skipped_count:
        message += (
            f" Skipped {skipped_count} rule{'' if skipped_count == 1 else 's'} "
            "that changed or became referenced after import."
        )
    if removed_categories:
        message += f" Removed {removed_categories} imported categor{'y' if removed_categories == 1 else 'ies'}."

    return message


def undo_rules_override_import(undo_state: Mapping[str, Any]) -> str:
    """Undo rules override import."""
    before_rules = undo_state.get("before_rules") or []
    after_rules = undo_state.get("after_rules") or []
    transaction_refs = undo_state.get("transaction_rule_refs") or []

    restored_refs = 0
    skipped_refs = 0
    removed_categories = 0

    with db_core_transaction() as conn:
        current_rules = snapshot_category_rules(conn)
        # Override undo is intentionally strict: if rule state changed after
        # import, restoring the prior snapshot would discard later user edits.
        if not rule_snapshots_equal(current_rules, after_rules):
            raise ValueError("Cannot undo this rules import because rules changed after import processing.")

        imported_rule_ids = [rule["id"] for rule in after_rules]
        if imported_rule_ids and rule_reference_count(conn, imported_rule_ids):
            raise ValueError("Cannot undo this rules import because transactions now reference imported rules.")

        conn.execute(delete(category_rules_table))
        restore_category_rules(conn, before_rules)

        for ref in transaction_refs:
            result = conn.execute(
                update(transactions_table)
                .where(
                    transactions_table.c.id == ref["transaction_id"],
                    transactions_table.c.category_rule_id.is_(None),
                )
                .values(category_rule_id=ref["category_rule_id"])
            )
            if result.rowcount:
                restored_refs += 1
            else:
                skipped_refs += 1

        removed_categories = remove_imported_categories(
            conn,
            undo_state.get("created_categories") or [],
        )

    message = f"Restored {len(before_rules)} rule{'' if len(before_rules) == 1 else 's'} from before import."
    if restored_refs:
        message += f" Restored rule references on {restored_refs} transaction" f"{'' if restored_refs == 1 else 's'}."
    if skipped_refs:
        message += (
            f" Skipped {skipped_refs} transaction reference"
            f"{'' if skipped_refs == 1 else 's'} that changed after import."
        )
    if removed_categories:
        message += f" Removed {removed_categories} imported categor{'y' if removed_categories == 1 else 'ies'}."

    return message


def parse_rules_csv(raw_text: str) -> list[dict[str, Any]]:
    """Parse rules CSV."""
    parsed = parse_rules_csv_with_errors(raw_text)
    if parsed.row_errors:
        raise ValueError(parsed.row_errors[0])
    return [public_import_rule(rule) for rule in parsed.rules]


def parse_rules_csv_with_errors(raw_text: str) -> RuleCsvParseResult:
    """Parse importable CSV rows while collecting row-level errors."""
    reader = csv.DictReader(io.StringIO(raw_text))
    if not reader.fieldnames:
        raise ValueError("The rules CSV needs a header row.")

    rules: list[dict[str, Any]] = []
    row_errors: list[str] = []
    total_rows = 0
    for line_number, row in enumerate(reader, start=2):
        if not any(str(value or "").strip() for value in row.values()):
            continue
        total_rows += 1
        try:
            rule = parse_rules_csv_row(row, line_number)
        except ValueError as exc:
            row_errors.append(csv_row_error_message(line_number, exc))
            continue
        rule[IMPORT_RULE_LINE_NUMBER] = line_number
        rules.append(rule)

    return RuleCsvParseResult(
        total_rows=total_rows,
        rules=tuple(rules),
        row_errors=tuple(row_errors),
    )


def public_import_rule(rule: Mapping[str, Any]) -> dict[str, Any]:
    """Return an import rule without parser-only metadata."""
    return {key: value for key, value in rule.items() if key != IMPORT_RULE_LINE_NUMBER}


def csv_row_error_message(line_number: int, exc: ValueError) -> str:
    """Return a row-numbered CSV error message."""
    message = str(exc)
    prefix = f"Row {line_number}: "
    return message if message.startswith(prefix) else f"{prefix}{message}"


def import_rule_error_message(rule: Mapping[str, Any], exc: ValueError) -> str:
    """Return a row-numbered import validation error message."""
    line_number = rule.get(IMPORT_RULE_LINE_NUMBER)
    if line_number is None:
        return str(exc)
    try:
        parsed_line_number = int(str(line_number))
    except (TypeError, ValueError):
        return str(exc)
    return csv_row_error_message(parsed_line_number, exc)


def invalid_row_summary(row_errors: Sequence[str]) -> str:
    """Return a short processing summary for skipped invalid rows."""
    if not row_errors:
        return ""
    message = f" Skipped {len(row_errors)} invalid row{'' if len(row_errors) == 1 else 's'}."
    return f"{message} {'; '.join(row_errors)}"


def raise_no_importable_rules(row_errors: Sequence[str] = ()) -> None:
    """Raise a useful error when an import has no valid rows."""
    if row_errors:
        raise ValueError(row_errors[0])
    raise ValueError("No importable rules were found.")


def validate_import_rule_for_write(conn: Any, rule: Mapping[str, Any]) -> None:
    """Validate database-backed rule references before preview or import."""
    resolve_rule_account_id(conn, rule, require_existing=True)


def import_rules_valid_for_write(
    conn: Any,
    imported_rules: Sequence[Mapping[str, Any]],
    row_errors: Sequence[str] = (),
    skip_invalid_rows: bool = False,
) -> tuple[list[Mapping[str, Any]], list[str]]:
    """Return rules that can be safely written plus skipped row errors."""
    valid_rules: list[Mapping[str, Any]] = []
    skipped_errors = list(row_errors)
    for rule in imported_rules:
        try:
            validate_import_rule_for_write(conn, rule)
        except ValueError as exc:
            if not skip_invalid_rows:
                raise
            skipped_errors.append(import_rule_error_message(rule, exc))
            continue
        valid_rules.append(rule)
    return valid_rules, skipped_errors


def parse_rules_csv_row(row: Mapping[str, Any], line_number: int) -> dict[str, Any]:
    """Parse rules CSV row."""
    normalized_row = {normalize_import_header(key): value for key, value in row.items() if key is not None}
    keyword = normalize_merchant_description(rule_import_value(normalized_row, "keyword", "merchant", "rule_keyword"))
    merchant_name = str(
        rule_import_value(
            normalized_row,
            "merchant_name",
            "bound_merchant",
            "merchant_display",
            "merchant_display_name",
        )
        or ""
    ).strip()
    account_name = str(
        rule_import_value(
            normalized_row,
            "account_name",
            "account",
            "bound_account",
            "account_display",
        )
        or ""
    ).strip()
    if not keyword and merchant_name:
        keyword = normalize_merchant_description(merchant_name)
    category = clean_category_name(rule_import_value(normalized_row, "category"))
    tag_names = normalize_tag_names(rule_import_value(normalized_row, "tags", "tag", "rule_tags"))

    if not keyword:
        raise ValueError(f"Row {line_number}: keyword or merchant_name is required.")
    if not category:
        raise ValueError(f"Row {line_number}: category is required.")

    amount_min = parse_optional_rule_amount(
        rule_import_value(normalized_row, "amount_min", "min_amount", "minimum_amount")
    )
    amount_max = parse_optional_rule_amount(
        rule_import_value(normalized_row, "amount_max", "max_amount", "maximum_amount")
    )
    amount_text = rule_import_value(normalized_row, "amount")
    if amount_min is None and amount_max is None and str(amount_text or "").strip():
        amount_min, amount_max = parse_rule_amount_range(amount_text, line_number)

    if amount_min is not None and amount_max is not None and amount_min > amount_max:
        raise ValueError(f"Row {line_number}: minimum amount cannot be greater than maximum amount.")

    source = str(rule_import_value(normalized_row, "source") or CATEGORY_RULE_SOURCE_MANUAL).strip().lower()
    if source not in RULE_SOURCE_VALUES:
        allowed_sources = ", ".join(sorted(RULE_SOURCE_VALUES))
        raise ValueError(f"Row {line_number}: source must be one of {allowed_sources}.")

    direction = (
        str(rule_import_value(normalized_row, "direction", "transaction_direction") or CATEGORY_RULE_DIRECTION_ANY)
        .strip()
        .lower()
    )
    if direction not in CATEGORY_RULE_DIRECTIONS:
        allowed_directions = ", ".join(sorted(CATEGORY_RULE_DIRECTIONS))
        raise ValueError(f"Row {line_number}: direction must be one of {allowed_directions}.")
    try:
        validate_rule_tags_for_direction(tag_names, direction)
    except ValueError as exc:
        raise ValueError(f"Row {line_number}: {exc}") from None

    created_at = str(rule_import_value(normalized_row, "created_at", "created") or "").strip() or None

    return {
        "keyword": keyword,
        "account_name": account_name,
        "merchant_name": merchant_name,
        "category": category,
        "tags": tag_names,
        "amount_min": amount_min,
        "amount_max": amount_max,
        "direction": direction,
        "source": source,
        "created_at": created_at,
    }


def normalize_import_header(value: object) -> str:
    """Normalize import header."""
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")


def rule_import_value(row: Mapping[str, Any], *keys: str) -> Any:
    """Build import value."""
    for key in keys:
        if key in row:
            return row[key]
    return ""


def parse_optional_rule_amount(value: object) -> Decimal | None:
    """Parse optional rule amount."""
    text = str(value or "").strip()
    if not text or text.casefold() in {"any", "none", "null"}:
        return None

    try:
        amount = parse_money_text(text)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"Invalid amount: {value}") from exc
    if amount is None:
        raise ValueError(f"Invalid amount: {value}")
    return amount


def parse_rule_amount_range(value: object, line_number: int) -> tuple[Decimal | None, Decimal | None]:
    """Parse rule amount range."""
    text = str(value or "").strip()
    if not text or text.casefold() == "any":
        return None, None

    if " - " in text:
        left, right = text.split(" - ", 1)
        return parse_optional_rule_amount(left), parse_optional_rule_amount(right)

    amount = parse_optional_rule_amount(text)
    return amount, amount


def rule_import_key(rule: Mapping[str, Any]) -> tuple[Any, ...]:
    """Build import key."""
    return (
        str(rule.get("merchant_name") or "").strip().casefold(),
        str(rule.get("account_name") or "").strip().casefold(),
        rule["keyword"],
        rule.get("direction") or CATEGORY_RULE_DIRECTION_ANY,
        rule["amount_min"],
        rule["amount_max"],
    )
