"""Persistence helpers for the transactions feature."""

from dataclasses import dataclass

from sqlalchemy import case, select, update

from finance_app.core.constants import (
    CATEGORY_RULE_SOURCE_MANUAL,
    TRANSACTION_KIND_EXPENSE,
    TRANSACTION_KIND_INCOME,
    TRANSACTION_KIND_REFUND,
    TRANSACTION_KIND_TRANSFER,
    TRANSFER_CATEGORY,
)
from finance_app.core.money import money_to_float
from finance_app.database.tables import transactions as transactions_table
from finance_app.modules.categories.repository import resolve_category_id
from finance_app.modules.categories.sources import manual_category_assignment, utc_timestamp
from finance_app.modules.categories.taxonomy import get_transaction_tag_names, set_transaction_tags
from finance_app.modules.categories.service import save_category_rule


@dataclass(frozen=True)
class ManualCategoryAssignment:
    """Represent manual category assignment."""
    updated: bool
    saved_rule_id: int | None = None
    transaction_changed: bool = False


def get_transaction_for_category_update(conn, transaction_id):
    """Return transaction for category update."""
    row = conn.execute(
        select(
            transactions_table.c.id,
            transactions_table.c.merchant_id,
            transactions_table.c.description,
            transactions_table.c.amount,
        ).where(transactions_table.c.id == transaction_id)
    ).mappings().fetchone()
    if row is None:
        return None

    transaction = dict(row)
    transaction["amount"] = money_to_float(transaction["amount"])
    return transaction


def assign_manual_category(
    conn,
    transaction_id,
    category,
    tag_names=None,
    rule_keyword=None,
    amount_min=None,
    amount_max=None,
    rule_merchant_id=None,
):
    """Handle a transaction category form submission.

    The transaction is marked manually verified when the submitted category or
    tags differ from the current row, or when the user saves a rule from the
    modal. Rule-only saves approve the current row without changing its existing
    category provenance.
    """
    current = conn.execute(
        select(
            transactions_table.c.id,
            transactions_table.c.category,
        ).where(transactions_table.c.id == transaction_id)
    ).mappings().fetchone()
    if current is None:
        return ManualCategoryAssignment(updated=False)

    submitted_tags = tag_names or []
    current_tags = get_transaction_tag_names(conn, transaction_id)
    transaction_changed = (
        current["category"] != category
        or set(current_tags) != set(submitted_tags)
    )

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


def mark_transaction_verified(conn, transaction_id, reviewed_at=None):
    """Mark transaction verified."""
    reviewed_at = reviewed_at or utc_timestamp()
    cursor = conn.execute(
        update(transactions_table)
        .where(transactions_table.c.id == transaction_id)
        .values(needs_review=0, reviewed_at=reviewed_at)
    )
    return cursor.rowcount > 0


def set_transaction_ignored(conn, transaction_id, ignored):
    """Set transaction ignored."""
    ignored = 1 if ignored else 0
    values = {"ignored": ignored}
    if ignored:
        values["needs_review"] = 0
    cursor = conn.execute(
        update(transactions_table)
        .where(transactions_table.c.id == transaction_id)
        .values(**values)
    )
    return cursor.rowcount > 0
