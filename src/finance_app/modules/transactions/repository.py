"""Persistence helpers for the transactions feature."""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from sqlalchemy import and_, case, exists, func, select, update

from finance_app.core.category_sql import transaction_category_label_expression
from finance_app.core.constants import (
    CATEGORY_RULE_SOURCE_MANUAL,
    CATEGORY_SOURCE_UNKNOWN,
    TRANSACTION_KIND_EXPENSE,
    TRANSACTION_KIND_INCOME,
    TRANSACTION_KIND_REFUND,
    TRANSACTION_KIND_TRANSFER,
    TRANSFER_CATEGORY,
)
from finance_app.core.money import MoneyValue, money_to_float, optional_money_to_decimal
from finance_app.database.tables import accounts as accounts_table
from finance_app.database.tables import transaction_tags as transaction_tags_table
from finance_app.database.tables import transactions as transactions_table
from finance_app.modules.categories.repository import resolve_category_id
from finance_app.modules.categories.service import save_category_rule
from finance_app.modules.categories.sources import category_metadata_json, manual_category_assignment, utc_timestamp
from finance_app.modules.categories.taxonomy import get_transaction_tag_names, set_transaction_tags


@dataclass(frozen=True)
class ManualCategoryAssignment:
    """Represent manual category assignment."""

    updated: bool
    saved_rule_id: int | None = None
    transaction_changed: bool = False


def get_transaction_for_category_update(conn: Any, transaction_id: int) -> dict[str, Any] | None:
    """Return transaction for category update."""
    row = (
        conn.execute(
            select(
                transactions_table.c.id,
                transactions_table.c.merchant_id,
                transactions_table.c.description,
                transactions_table.c.amount,
            ).where(transactions_table.c.id == transaction_id)
        )
        .mappings()
        .fetchone()
    )
    if row is None:
        return None

    transaction = dict(row)
    transaction["amount"] = money_to_float(transaction["amount"])
    return transaction


def get_transaction_for_ai_categorization(conn: Any, transaction_id: int) -> dict[str, Any] | None:
    """Return a transaction row with category fields needed for a one-off AI rerun."""
    row = (
        conn.execute(
            select(
                transactions_table.c.id,
                transactions_table.c.statement_id,
                transactions_table.c.account_id,
                transactions_table.c.merchant_id,
                transactions_table.c.tx_date,
                transactions_table.c.description,
                transactions_table.c.amount,
                transactions_table.c.category.label("stored_category"),
                transaction_category_label_expression(None).label("category"),
                transactions_table.c.category_id,
                transactions_table.c.needs_review,
                transactions_table.c.category_source,
                transactions_table.c.category_confidence,
                transactions_table.c.category_rule_id,
                transactions_table.c.category_metadata,
                transactions_table.c.categorized_at,
                transactions_table.c.reviewed_at,
                transactions_table.c.ignored,
                transactions_table.c.transaction_kind,
                accounts_table.c.name.label("account_name"),
                accounts_table.c.account_type.label("account_type"),
            )
            .select_from(
                transactions_table.outerjoin(
                    accounts_table,
                    accounts_table.c.id == transactions_table.c.account_id,
                )
            )
            .where(transactions_table.c.id == transaction_id)
        )
        .mappings()
        .fetchone()
    )
    if row is None:
        return None

    transaction = dict(row)
    transaction["amount"] = money_to_float(transaction["amount"])
    return transaction


def assign_manual_category(
    conn: Any,
    transaction_id: int,
    category: str,
    tag_names: Iterable[str] | None = None,
    rule_keyword: str | None = None,
    amount_min: MoneyValue | None = None,
    amount_max: MoneyValue | None = None,
    rule_merchant_id: object | None = None,
) -> ManualCategoryAssignment:
    """Handle a transaction category form submission.

    The transaction is marked manually verified when the submitted category or
    tags differ from the current row, or when the user saves a rule from the
    modal. Rule-only saves approve the current row without changing its existing
    category provenance.
    """
    current = (
        conn.execute(
            select(
                transactions_table.c.id,
                transactions_table.c.category,
            ).where(transactions_table.c.id == transaction_id)
        )
        .mappings()
        .fetchone()
    )
    if current is None:
        return ManualCategoryAssignment(updated=False)

    submitted_tags = tag_names or []
    current_tags = get_transaction_tag_names(conn, transaction_id)
    transaction_changed = current["category"] != category or set(current_tags) != set(submitted_tags)

    if transaction_changed:
        metadata = manual_category_assignment()
        transaction_kind = (
            TRANSACTION_KIND_TRANSFER
            if category == TRANSFER_CATEGORY
            else case(
                (transactions_table.c.transaction_kind == TRANSACTION_KIND_REFUND, TRANSACTION_KIND_REFUND),
                (transactions_table.c.amount < 0, TRANSACTION_KIND_INCOME),
                else_=TRANSACTION_KIND_EXPENSE,
            )
        )
        conn.execute(
            update(transactions_table)
            .where(transactions_table.c.id == transaction_id)
            .values(
                category=category,
                category_id=resolve_category_id(conn, category),
                needs_review=0,
                category_source=metadata.category_source,
                category_confidence=metadata.category_confidence,
                category_rule_id=metadata.category_rule_id,
                category_metadata=metadata.category_metadata,
                categorized_at=metadata.categorized_at,
                reviewed_at=metadata.reviewed_at,
                transaction_kind=transaction_kind,
            )
        )
        set_transaction_tags(
            conn,
            transaction_id,
            submitted_tags,
            source=metadata.category_source,
            rule_id=None,
        )
    elif rule_keyword:
        conn.execute(
            update(transactions_table)
            .where(transactions_table.c.id == transaction_id)
            .values(needs_review=0, reviewed_at=utc_timestamp())
        )

    saved_rule_id = None
    if rule_keyword:
        saved_rule_id = save_category_rule(
            conn,
            rule_keyword,
            category,
            source=CATEGORY_RULE_SOURCE_MANUAL,
            amount_min=amount_min,
            amount_max=amount_max,
            tags=submitted_tags,
            merchant_id=rule_merchant_id,
        )
    return ManualCategoryAssignment(
        updated=True,
        saved_rule_id=saved_rule_id,
        transaction_changed=transaction_changed,
    )


def mark_transaction_verified(conn: Any, transaction_id: int, reviewed_at: str | None = None) -> bool:
    """Mark transaction verified."""
    reviewed_at = reviewed_at or utc_timestamp()
    cursor = conn.execute(
        update(transactions_table)
        .where(transactions_table.c.id == transaction_id)
        .values(needs_review=0, reviewed_at=reviewed_at)
    )
    return cursor.rowcount > 0


def mark_transactions_verified(conn: Any, transaction_ids: Iterable[object], reviewed_at: str | None = None) -> int:
    """Mark selected transactions verified and return the updated row count."""
    ids = normalized_transaction_ids(transaction_ids)
    if not ids:
        return 0

    reviewed_at = reviewed_at or utc_timestamp()
    cursor = conn.execute(
        update(transactions_table)
        .where(transactions_table.c.id.in_(ids))
        .values(needs_review=0, reviewed_at=reviewed_at)
    )
    return cursor.rowcount


def set_transaction_ignored(conn: Any, transaction_id: int, ignored: object) -> bool:
    """Set transaction ignored."""
    ignored = 1 if ignored else 0
    values = {"ignored": ignored}
    if ignored:
        values["needs_review"] = 0
    cursor = conn.execute(update(transactions_table).where(transactions_table.c.id == transaction_id).values(**values))
    return cursor.rowcount > 0


def set_transactions_ignored(conn: Any, transaction_ids: Iterable[object], ignored: object) -> int:
    """Set the ignored flag for selected transactions and return updated count."""
    ids = normalized_transaction_ids(transaction_ids)
    if not ids:
        return 0

    ignored = 1 if ignored else 0
    values = {"ignored": ignored}
    if ignored:
        values["needs_review"] = 0
    cursor = conn.execute(update(transactions_table).where(transactions_table.c.id.in_(ids)).values(**values))
    return cursor.rowcount


def apply_ai_category_update(
    conn: Any,
    transaction_id: int,
    transaction: Mapping[str, Any],
    unknown_category: str,
    expected_original_state: Mapping[str, Any],
) -> bool:
    """Persist one AI categorization result to a single transaction row.

    The update intentionally does not create or modify category rules. Callers
    pass the already-classified transaction payload produced by the LLM adapter
    and the original transaction state captured when the suggestion was made.
    """
    category = transaction.get("category") or unknown_category
    source = transaction.get("category_source") or CATEGORY_SOURCE_UNKNOWN
    rule_id = transaction.get("category_rule_id")
    cursor = conn.execute(
        update(transactions_table)
        .where(
            transactions_table.c.id == transaction_id,
            *transaction_ai_original_state_conditions(expected_original_state),
        )
        .values(
            category=category,
            category_id=resolve_category_id(conn, category),
            needs_review=1 if transaction.get("needs_review") else 0,
            category_source=source,
            category_confidence=transaction.get("category_confidence"),
            category_rule_id=rule_id,
            category_metadata=category_metadata_json(transaction.get("category_metadata")),
            categorized_at=transaction.get("categorized_at"),
            reviewed_at=transaction.get("reviewed_at"),
            transaction_kind=ai_transaction_kind(
                category,
                transaction.get("amount"),
                transaction.get("transaction_kind"),
            ),
        )
    )
    if cursor.rowcount <= 0:
        return False

    set_transaction_tags(
        conn,
        transaction_id,
        transaction.get("tags") or [],
        source=source,
        rule_id=rule_id,
    )
    return True


def get_transaction_tag_ids(conn: Any, transaction_id: object) -> list[int]:
    """Return sorted tag IDs currently assigned to a transaction."""
    return [
        int(tag_id)
        for tag_id in conn.execute(
            select(transaction_tags_table.c.tag_id)
            .where(transaction_tags_table.c.transaction_id == transaction_id)
            .order_by(transaction_tags_table.c.tag_id)
        ).scalars()
    ]


def get_transaction_tag_ids_by_id(conn: Any, transaction_ids: Iterable[object]) -> dict[int, list[int]]:
    """Return sorted transaction tag IDs keyed by transaction ID."""
    ids = normalized_transaction_ids(transaction_ids)
    if not ids:
        return {}

    result: dict[int, list[int]] = {transaction_id: [] for transaction_id in ids}
    rows = (
        conn.execute(
            select(
                transaction_tags_table.c.transaction_id,
                transaction_tags_table.c.tag_id,
            )
            .where(transaction_tags_table.c.transaction_id.in_(ids))
            .order_by(
                transaction_tags_table.c.transaction_id,
                transaction_tags_table.c.tag_id,
            )
        )
        .mappings()
        .fetchall()
    )
    for row in rows:
        result.setdefault(int(row["transaction_id"]), []).append(int(row["tag_id"]))
    return result


def transaction_ai_original_state(row: Mapping[str, Any], tag_ids: Iterable[object]) -> dict[str, Any]:
    """Return the compact row state used to validate a later AI suggestion apply."""
    return {
        "category": state_text(row.get("category")),
        "stored_category": state_text(row.get("stored_category")),
        "category_id": state_int_or_none(row.get("category_id")),
        "needs_review": state_bool_int(row.get("needs_review")),
        "category_source": state_text(row.get("category_source")),
        "category_confidence": row.get("category_confidence"),
        "category_rule_id": state_int_or_none(row.get("category_rule_id")),
        "categorized_at": state_text(row.get("categorized_at")),
        "reviewed_at": state_text(row.get("reviewed_at")),
        "ignored": state_bool_int(row.get("ignored")),
        "transaction_kind": state_text(row.get("transaction_kind")),
        "tag_ids": state_int_list(tag_ids),
    }


def transaction_ai_original_state_conditions(expected: Mapping[str, Any]) -> list[Any]:
    """Return SQL predicates proving a transaction still matches an AI state token."""
    return [
        column_matches(transactions_table.c.category, expected.get("stored_category")),
        column_matches(transactions_table.c.category_id, state_int_or_none(expected.get("category_id"))),
        column_matches(transactions_table.c.needs_review, state_bool_int(expected.get("needs_review"))),
        column_matches(transactions_table.c.category_source, expected.get("category_source")),
        column_matches(transactions_table.c.category_confidence, expected.get("category_confidence")),
        column_matches(transactions_table.c.category_rule_id, state_int_or_none(expected.get("category_rule_id"))),
        column_matches(transactions_table.c.categorized_at, expected.get("categorized_at")),
        column_matches(transactions_table.c.reviewed_at, expected.get("reviewed_at")),
        column_matches(transactions_table.c.ignored, state_bool_int(expected.get("ignored"))),
        column_matches(transactions_table.c.transaction_kind, expected.get("transaction_kind")),
        exact_transaction_tag_ids_condition(transactions_table.c.id, expected.get("tag_ids") or []),
    ]


def exact_transaction_tag_ids_condition(transaction_id_column: Any, tag_ids: Iterable[object]) -> Any:
    """Return a predicate requiring the transaction to have exactly these tags."""
    expected_tag_ids = state_int_list(tag_ids)
    tag_count = (
        select(func.count())
        .select_from(transaction_tags_table)
        .where(transaction_tags_table.c.transaction_id == transaction_id_column)
        .scalar_subquery()
    )
    no_extra_tags = ~exists(
        select(1).where(
            transaction_tags_table.c.transaction_id == transaction_id_column,
            ~transaction_tags_table.c.tag_id.in_(expected_tag_ids),
        )
    )
    if expected_tag_ids:
        return and_(tag_count == len(expected_tag_ids), no_extra_tags)
    return and_(
        tag_count == 0,
        ~exists(select(1).where(transaction_tags_table.c.transaction_id == transaction_id_column)),
    )


def column_matches(column: Any, value: object) -> Any:
    """Return a nullable equality predicate for an optimistic state field."""
    return column.is_(None) if value is None else column == value


def state_text(value: object) -> str | None:
    """Return a stable JSON-safe text value for optimistic state fields."""
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return str(value.isoformat())
    return str(value)


def state_int_or_none(value: object) -> int | None:
    """Return an integer state value or ``None``."""
    if value in (None, ""):
        return None
    return int(str(value))


def state_int_list(values: Iterable[object]) -> list[int]:
    """Return sorted integer state values, skipping invalid entries."""
    result: set[int] = set()
    for value in values:
        try:
            parsed = state_int_or_none(value)
        except (TypeError, ValueError):
            continue
        if parsed is not None:
            result.add(parsed)
    return sorted(result)


def state_bool_int(value: object) -> int:
    """Return a persisted boolean state value as 0 or 1."""
    return 1 if str(value).strip().lower() in {"1", "true", "yes", "on"} else 0


def get_transactions_for_recategorization(conn: Any, transaction_ids: Iterable[object]) -> list[dict[str, Any]]:
    """Return selected transaction rows needed by the categorization workflow."""
    ids = normalized_transaction_ids(transaction_ids)
    if not ids:
        return []

    row_order = case(
        *((transactions_table.c.id == transaction_id, index) for index, transaction_id in enumerate(ids)),
        else_=len(ids),
    )
    rows = (
        conn.execute(
            select(
                transactions_table.c.id,
                transactions_table.c.account_id,
                transactions_table.c.tx_date,
                transactions_table.c.merchant_id,
                transactions_table.c.description,
                transactions_table.c.amount,
                transactions_table.c.category.label("stored_category"),
                transaction_category_label_expression(None).label("category"),
                transactions_table.c.category_id,
                transactions_table.c.needs_review,
                transactions_table.c.category_source,
                transactions_table.c.category_confidence,
                transactions_table.c.category_rule_id,
                transactions_table.c.categorized_at,
                transactions_table.c.reviewed_at,
                transactions_table.c.ignored,
                transactions_table.c.transaction_kind,
            )
            .where(transactions_table.c.id.in_(ids))
            .order_by(row_order)
        )
        .mappings()
        .fetchall()
    )

    tag_ids_by_transaction = get_transaction_tag_ids_by_id(conn, (row["id"] for row in rows))
    transactions = []
    for row in rows:
        transaction = dict(row)
        transaction["original_state"] = transaction_ai_original_state(
            transaction,
            tag_ids_by_transaction.get(int(transaction["id"]), []),
        )
        transaction["amount"] = money_to_float(transaction["amount"])
        transactions.append(transaction)
    return transactions


def update_recategorized_transaction(
    conn: Any,
    transaction: Mapping[str, Any],
    unknown_category: str,
    expected_original_state: Mapping[str, Any],
) -> bool:
    """Persist one complete workflow categorization result to its transaction."""
    transaction_id = transaction["id"]
    category = transaction.get("category") or unknown_category
    source = transaction.get("category_source") or CATEGORY_SOURCE_UNKNOWN
    rule_id = transaction.get("category_rule_id")
    cursor = conn.execute(
        update(transactions_table)
        .where(
            transactions_table.c.id == transaction_id,
            *transaction_ai_original_state_conditions(expected_original_state),
        )
        .values(
            category=category,
            category_id=resolve_category_id(conn, category),
            needs_review=1 if transaction.get("needs_review") else 0,
            category_source=source,
            category_confidence=transaction.get("category_confidence"),
            category_rule_id=rule_id,
            category_metadata=category_metadata_json(transaction.get("category_metadata")),
            categorized_at=transaction.get("categorized_at"),
            reviewed_at=transaction.get("reviewed_at"),
            transaction_kind=ai_transaction_kind(
                category,
                transaction.get("amount"),
                transaction.get("transaction_kind"),
            ),
        )
    )
    if cursor.rowcount <= 0:
        return False

    set_transaction_tags(
        conn,
        transaction_id,
        transaction.get("tags") or [],
        source=source,
        rule_id=rule_id,
    )
    return True


def normalized_transaction_ids(transaction_ids: Iterable[object] | None) -> list[int]:
    """Return de-duplicated positive transaction IDs preserving input order."""
    ids: list[int] = []
    seen: set[int] = set()
    for value in transaction_ids or ():
        try:
            transaction_id = int(str(value))
        except (TypeError, ValueError):
            continue
        if transaction_id <= 0 or transaction_id in seen:
            continue
        seen.add(transaction_id)
        ids.append(transaction_id)
    return ids


def ai_transaction_kind(category: object, amount: MoneyValue | None, current_kind: object | None = None) -> str:
    """Return the transaction kind implied by a one-off AI category update."""
    if category == TRANSFER_CATEGORY:
        return TRANSACTION_KIND_TRANSFER
    if current_kind == TRANSACTION_KIND_REFUND:
        return TRANSACTION_KIND_REFUND
    amount_value = optional_money_to_decimal(amount)
    if amount_value is not None and amount_value < 0:
        return TRANSACTION_KIND_INCOME
    return TRANSACTION_KIND_EXPENSE
