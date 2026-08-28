"""Background workflow helpers for the review feature."""

from collections.abc import Iterable, Mapping, MutableMapping
from typing import Any

from sqlalchemy import delete, update

from finance_app.core.constants import (
    CATEGORY_RULE_SOURCE_MANUAL,
    TRANSACTION_KIND_EXPENSE,
    TRANSACTION_KIND_INCOME,
    TRANSACTION_KIND_REFUND,
    TRANSACTION_KIND_TRANSFER,
    TRANSFER_CATEGORY,
)
from finance_app.core.money import MoneyValue, optional_money_to_decimal
from finance_app.database.engine import db_core_transaction
from finance_app.database.tables import category_rules as category_rules_table
from finance_app.database.tables import transactions as transactions_table
from finance_app.modules.categories.repository import resolve_category_id
from finance_app.modules.categories.service import save_category_rule
from finance_app.modules.categories.sources import (
    TransactionCategoryChange,
    TransactionCategorySnapshot,
    manual_category_assignment,
)
from finance_app.modules.categories.taxonomy import (
    get_transaction_tag_names,
    set_rule_tags,
    set_transaction_tags,
)
from finance_app.modules.review.queries import find_review_rule, rule_snapshot
from finance_app.modules.review.repository import review_group_rows
from finance_app.modules.rules.forms import amount_bounds_label
from finance_app.modules.settings.runtime import get_unknown_category


def apply_review_group_job(
    undo_state: MutableMapping[str, Any],
    merchant_key: str,
    category: str,
    tags: Iterable[str],
    create_rule: bool,
    rule_keyword: str,
    amount_min: MoneyValue | None = None,
    amount_max: MoneyValue | None = None,
    transaction_id: int | None = None,
    selected_transaction_ids: Iterable[int] | None = None,
) -> str:
    """Apply review group job."""
    with db_core_transaction() as conn:
        unknown_category = get_unknown_category(conn)
        changes = apply_review_group_transactions(
            conn,
            merchant_key,
            category,
            tags,
            unknown_category,
            transaction_id=transaction_id,
            transaction_ids=selected_transaction_ids,
        )
        rule_change = (
            save_review_rule(conn, rule_keyword, category, amount_min, amount_max, tags)
            if create_rule and changes
            else None
        )
        undo_state["changes"] = changes
        undo_state["rule_change"] = rule_change
        undo_state["category"] = category
        undo_state["merchant_key"] = merchant_key
        undo_state["transaction_id"] = transaction_id
        undo_state["selected_transaction_ids"] = list(selected_transaction_ids or [])

    message = f"Categorized {len(changes)} transaction" f"{'' if len(changes) == 1 else 's'} as {category}."
    if rule_change:
        message += f" Rule saved for {rule_keyword}{amount_bounds_label(amount_min, amount_max)}."
    return message


def undo_review_group_job(undo_state: Mapping[str, Any]) -> str:
    """Undo review group job."""
    changes = undo_state.get("changes") or []
    rule_change = undo_state.get("rule_change")
    restored_count = 0
    skipped_count = 0

    with db_core_transaction() as conn:
        for change in changes:
            cursor = restore_review_transaction(conn, change)

            if cursor.rowcount:
                set_transaction_tags(
                    conn,
                    change["transaction_id"],
                    change.get("old_tags", []),
                    source=change["old_category_source"],
                    rule_id=change["old_category_rule_id"],
                )
                restored_count += 1
            else:
                skipped_count += 1

        rule_result = undo_review_rule(conn, rule_change)

    message = f"Restored {restored_count} reviewed transaction"
    message += "" if restored_count == 1 else "s"
    message += "."
    if skipped_count:
        message += f" Skipped {skipped_count} transaction" f"{'' if skipped_count == 1 else 's'} changed after the job."
    if rule_result:
        message += f" {rule_result}"
    return message


def apply_review_group_transactions(
    conn: Any,
    merchant_key: str,
    category: str,
    tags: Iterable[str],
    unknown_category: str,
    transaction_id: int | None = None,
    transaction_ids: Iterable[int] | None = None,
) -> list[dict[str, Any]]:
    """Apply review group transactions."""
    tags = list(tags)
    changes: list[dict[str, Any]] = []
    rows = review_group_rows(conn, merchant_key, unknown_category)
    selected_ids = review_transaction_id_filter(transaction_id, transaction_ids)
    if selected_ids is not None:
        selected_id_set = set(selected_ids)
        rows = [row for row in rows if row["id"] in selected_id_set]

    for row in rows:
        old_tags = get_transaction_tag_names(conn, row["id"])
        if row["category"] == category and row["needs_review"] == 0 and old_tags == tags:
            continue

        metadata = manual_category_assignment()
        transaction_kind = reviewed_transaction_kind(category, row["amount"], row["transaction_kind"])
        old_state = TransactionCategorySnapshot.from_row(row, old_tags)
        new_state = TransactionCategorySnapshot(
            category=category,
            needs_review=0,
            assignment=metadata,
            transaction_kind=transaction_kind,
            tags=tuple(tags),
            category_id=resolve_category_id(conn, category),
        )
        changes.append(TransactionCategoryChange(row["id"], old_state, new_state).to_undo_record())
        update_review_transaction(conn, row["id"], category, metadata, transaction_kind)
        set_transaction_tags(
            conn,
            row["id"],
            tags,
            source=metadata.category_source,
            rule_id=None,
        )

    return changes


def update_review_transaction(
    conn: Any, transaction_id: int, category: str, metadata: Any, transaction_kind: str
) -> None:
    """Persist the reviewed category state for one transaction."""
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


def restore_review_transaction(conn: Any, change: Mapping[str, Any]) -> Any:
    """Restore a reviewed transaction if its current state still matches the undo snapshot."""
    old_category_id = change.get("old_category_id")
    if old_category_id is None:
        old_category_id = resolve_category_id(conn, change["old_category"])

    category_condition = (
        transactions_table.c.category.is_(None)
        if change["new_category"] is None
        else transactions_table.c.category == change["new_category"]
    )
    reviewed_at_condition = (
        transactions_table.c.reviewed_at.is_(None)
        if change["new_reviewed_at"] is None
        else transactions_table.c.reviewed_at == change["new_reviewed_at"]
    )
    category_metadata_condition = (
        transactions_table.c.category_metadata.is_(None)
        if change["new_category_metadata"] is None
        else transactions_table.c.category_metadata == change["new_category_metadata"]
    )
    return conn.execute(
        update(transactions_table)
        .where(
            transactions_table.c.id == change["transaction_id"],
            category_condition,
            transactions_table.c.needs_review == change["new_needs_review"],
            transactions_table.c.category_source == change["new_category_source"],
            category_metadata_condition,
            reviewed_at_condition,
        )
        .values(
            category=change["old_category"],
            category_id=old_category_id,
            needs_review=change["old_needs_review"],
            category_source=change["old_category_source"],
            category_confidence=change["old_category_confidence"],
            category_rule_id=change["old_category_rule_id"],
            category_metadata=change["old_category_metadata"],
            categorized_at=change["old_categorized_at"],
            reviewed_at=change["old_reviewed_at"],
            transaction_kind=change.get("old_transaction_kind", TRANSACTION_KIND_EXPENSE),
        ),
    )


def review_transaction_id_filter(
    transaction_id: int | None = None,
    transaction_ids: Iterable[object] | None = None,
) -> list[int] | None:
    """Return the optional transaction-id filter for a review group action."""
    if transaction_id is not None:
        return [transaction_id]

    if not transaction_ids:
        return None

    selected_ids: list[int] = []
    seen: set[int] = set()
    for tx_id in transaction_ids:
        if tx_id is None:
            continue
        try:
            tx_id = int(str(tx_id))
        except (TypeError, ValueError):
            continue
        if tx_id <= 0 or tx_id in seen:
            continue
        selected_ids.append(tx_id)
        seen.add(tx_id)
    return selected_ids


def reviewed_transaction_kind(category: object, amount: MoneyValue | None, current_kind: object | None = None) -> str:
    """Return transaction kind implied by reviewed category and amount."""
    if category == TRANSFER_CATEGORY:
        return TRANSACTION_KIND_TRANSFER
    if current_kind == TRANSACTION_KIND_REFUND:
        return TRANSACTION_KIND_REFUND
    amount_value = optional_money_to_decimal(amount)
    return TRANSACTION_KIND_INCOME if amount_value is not None and amount_value < 0 else TRANSACTION_KIND_EXPENSE


def save_review_rule(
    conn: Any,
    merchant_key: str,
    category: str,
    amount_min: MoneyValue | None = None,
    amount_max: MoneyValue | None = None,
    tags: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Save review rule."""
    previous_rule = find_review_rule(conn, merchant_key, amount_min, amount_max)
    rule_id = save_category_rule(
        conn,
        merchant_key,
        category,
        source=CATEGORY_RULE_SOURCE_MANUAL,
        amount_min=amount_min,
        amount_max=amount_max,
        tags=tags or [],
    )
    if rule_id is None:
        raise ValueError("Rule could not be saved.")
    new_rule = rule_snapshot(conn, rule_id)
    return {
        "rule_id": rule_id,
        "previous_rule": previous_rule,
        "new_rule": new_rule,
    }


def undo_review_rule(conn: Any, rule_change: Mapping[str, Any] | None) -> str:
    """Undo review rule."""
    if not rule_change:
        return ""

    rule_id = rule_change["rule_id"]
    current_rule = rule_snapshot(conn, rule_id)
    new_rule = rule_change["new_rule"]
    previous_rule = rule_change["previous_rule"]

    if current_rule is None:
        return "Rule already removed."

    if not rule_snapshots_match(current_rule, new_rule):
        return "Rule changed after the job; left it in place."

    if previous_rule is None:
        conn.execute(delete(category_rules_table).where(category_rules_table.c.id == rule_id))
        return "Removed created rule."

    category_id = previous_rule.get("category_id")
    if category_id is None:
        category_id = resolve_category_id(conn, previous_rule["category"])

    conn.execute(
        update(category_rules_table)
        .where(category_rules_table.c.id == rule_id)
        .values(
            merchant_id=previous_rule["merchant_id"],
            account_id=previous_rule.get("account_id"),
            keyword=previous_rule["keyword"],
            category=previous_rule["category"],
            category_id=category_id,
            amount_min=previous_rule["amount_min"],
            amount_max=previous_rule["amount_max"],
            direction=previous_rule.get("direction"),
            source=previous_rule["source"],
            ai_approved=previous_rule.get("ai_approved", 0),
        )
    )
    set_rule_tags(conn, rule_id, previous_rule.get("tags", []))
    return "Restored previous rule."


def rule_snapshots_match(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    """Build snapshots match."""
    keys = (
        "id",
        "merchant_id",
        "account_id",
        "keyword",
        "category",
        "category_id",
        "amount_min",
        "amount_max",
        "direction",
        "source",
        "ai_approved",
    )
    return all(left.get(key) == right.get(key) for key in keys) and left.get("tags", []) == right.get("tags", [])
