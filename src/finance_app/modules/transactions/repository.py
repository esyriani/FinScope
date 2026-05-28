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
    CATEGORY_SOURCE_UNKNOWN,
)
from finance_app.core.money import money_to_float, optional_money_to_float
from finance_app.database.tables import accounts as accounts_table, transactions as transactions_table
from finance_app.modules.categories.repository import resolve_category_id
from finance_app.modules.categories.sources import category_metadata_json, manual_category_assignment, utc_timestamp
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


def get_transaction_for_ai_categorization(conn, transaction_id):
    """Return a transaction row with category fields needed for a one-off AI rerun."""
    row = conn.execute(
        select(
            transactions_table.c.id,
            transactions_table.c.statement_id,
            transactions_table.c.account_id,
            transactions_table.c.merchant_id,
            transactions_table.c.tx_date,
            transactions_table.c.description,
            transactions_table.c.amount,
            transactions_table.c.category,
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


def apply_ai_category_update(conn, transaction_id, transaction, unknown_category):
    """Persist one AI categorization result to a single transaction row.

    The update intentionally does not create or modify category rules. Callers
    pass the already-classified transaction payload produced by the LLM adapter.
    """
    category = transaction.get("category") or unknown_category
    source = transaction.get("category_source") or CATEGORY_SOURCE_UNKNOWN
    rule_id = transaction.get("category_rule_id")
    cursor = conn.execute(
        update(transactions_table)
        .where(transactions_table.c.id == transaction_id)
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


def ai_transaction_kind(category, amount, current_kind=None):
    """Return the transaction kind implied by a one-off AI category update."""
    if category == TRANSFER_CATEGORY:
        return TRANSACTION_KIND_TRANSFER
    if current_kind == TRANSACTION_KIND_REFUND:
        return TRANSACTION_KIND_REFUND
    amount_value = optional_money_to_float(amount)
    if amount_value is not None and amount_value < 0:
        return TRANSACTION_KIND_INCOME
    return TRANSACTION_KIND_EXPENSE
