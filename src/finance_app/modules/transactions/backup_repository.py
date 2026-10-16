"""Persistence for restoring transaction backups with SQLAlchemy Core.

The backup workflow owns validation and one caller-owned write transaction.
These helpers resolve portable identities, insert ledger rows and tag links,
and restore reimbursement relationships without reusing source database IDs.
"""

from collections.abc import Mapping, Sequence
from typing import Any
from uuid import uuid4

from sqlalchemy import func, insert, select, update
from sqlalchemy.engine import Connection

from finance_app.core.builtin_taxonomy import builtin_category_by_name
from finance_app.core.taxonomy import tag_color_for_name
from finance_app.database.tables import (
    accounts,
    categories,
    normalize_name_key,
    reimbursement_allocations,
    reimbursement_expense_completions,
    tags,
    transaction_tags,
    transactions,
)
from finance_app.database.upsert import insert_or_select_unique_row
from finance_app.modules.accounts.repository import find_account_by_name, get_or_create_account
from finance_app.modules.merchants.repository import get_or_create_merchant_for_name
from finance_app.modules.transactions.backup_import import PREVIEW_ACTION_EXISTING, BackupAccountPreview


def restore_backup_accounts(conn: Connection, plans: Sequence[BackupAccountPreview]) -> dict[str, int]:
    """Create accounts first, then apply metadata and funding links in this transaction."""
    ids = {
        normalize_name_key(plan.name): int(get_or_create_account(conn, plan.name, plan.account_type)["id"])
        for plan in plans
    }
    for plan in plans:
        if plan.action == PREVIEW_ACTION_EXISTING:
            continue
        funding_id = ids.get(normalize_name_key(plan.paid_from_account_name))
        if funding_id is None and plan.paid_from_account_name:
            funding = find_account_by_name(conn, plan.paid_from_account_name)
            funding_id = int(funding["id"]) if funding else None
        conn.execute(
            update(accounts)
            .where(accounts.c.id == ids[normalize_name_key(plan.name)])
            .values(
                account_type=plan.account_type,
                paid_from_account_id=funding_id,
            )
        )
    return ids


def resolve_backup_category(conn: Connection, row: Mapping[str, Any]) -> Mapping[str, Any] | None:
    """Match category names, requiring initialized semantics for protected categories."""
    name = row["category"]
    if not name:
        return None
    statement = select(categories).where(categories.c.name_key == normalize_name_key(name))
    found = conn.execute(statement).mappings().first()
    builtin = builtin_category_by_name(name)
    if builtin and (found is None or found["builtin_key"] != builtin.key):
        raise ValueError("A required built-in category is missing or incorrectly configured.")
    if found is None:
        found, _ = insert_or_select_unique_row(conn, insert(categories).values(name=name), statement)
    return dict(found)


def restore_backup_transaction(
    conn: Connection, row: Mapping[str, Any], account_ids: Mapping[str, int], *, allow_duplicate: bool
) -> tuple[int, bool]:
    """Insert a ledger row, preserving its fingerprint unless an explicit copy needs a new one.

    The portable backup contains no statement or rule IDs. Those foreign keys
    and categorization explanations remain unset on the restored transaction.
    Database defaults record a new creation timestamp.
    """
    category = resolve_backup_category(conn, row)
    merchant = get_or_create_merchant_for_name(conn, row["merchant_name"]) if row["merchant_name"] else None
    values = {
        "account_id": account_ids.get(normalize_name_key(row["account_name"])),
        "merchant_id": merchant["id"] if merchant else None,
        "tx_date": row["tx_date"],
        "description": row["description"],
        "amount": row["amount"],
        "category": category["name"] if category else None,
        "category_id": category["id"] if category else None,
        "needs_review": row["needs_review"],
        "category_source": row["category_source"],
        "category_confidence": row["category_confidence"],
        "categorized_at": row["categorized_at"],
        "reviewed_at": row["reviewed_at"],
        "ignored": row["ignored"],
        "transaction_kind": row["transaction_kind"],
        "fingerprint": row["fingerprint"],
    }
    statement = select(transactions.c.id).where(transactions.c.fingerprint == row["fingerprint"])
    found, inserted = insert_or_select_unique_row(conn, insert(transactions).values(**values), statement)
    if inserted or not allow_duplicate:
        return int(found["id"]), inserted

    values["fingerprint"] = f"backup:{uuid4().hex}"
    result = conn.execute(insert(transactions).values(**values))
    assert result.inserted_primary_key is not None
    return int(result.inserted_primary_key[0]), True


def restore_backup_tags(conn: Connection, transaction_id: int, names: Sequence[str]) -> None:
    """Restore tag names as new assignments without fabricating their original audit history."""
    for name in names:
        statement = select(tags.c.id).where(tags.c.name_key == normalize_name_key(name))
        tag = conn.execute(statement).mappings().first()
        if tag is None:
            tag, _ = insert_or_select_unique_row(
                conn,
                insert(tags).values(name=name, color=tag_color_for_name(name)),
                statement,
            )
        conn.execute(insert(transaction_tags).values(transaction_id=transaction_id, tag_id=tag["id"]))


def backup_allocation_totals(conn: Connection, reimbursement_id: int, expense_id: int) -> tuple[Any, Any]:
    """Read all allocation totals after the workflow locks the affected subjects."""
    reimbursement_total = conn.execute(
        select(func.coalesce(func.sum(reimbursement_allocations.c.amount), 0)).where(
            reimbursement_allocations.c.reimbursement_transaction_id == reimbursement_id
        )
    ).scalar_one()
    expense_total = conn.execute(
        select(func.coalesce(func.sum(reimbursement_allocations.c.amount), 0)).where(
            reimbursement_allocations.c.expense_transaction_id == expense_id
        )
    ).scalar_one()
    return reimbursement_total, expense_total


def restore_backup_allocation(
    conn: Connection, reimbursement_id: int, expense_id: int, allocation: Mapping[str, Any]
) -> None:
    """Insert a validated allocation with new audit timestamps in the caller's transaction."""
    values = {
        "reimbursement_transaction_id": reimbursement_id,
        "expense_transaction_id": expense_id,
        "amount": allocation["amount"],
    }
    conn.execute(insert(reimbursement_allocations).values(**values))


def restore_backup_expense_completion(conn: Connection, expense_id: int) -> None:
    """Insert a missing completion marker for a restored and validated expense."""
    statement = select(reimbursement_expense_completions.c.id).where(
        reimbursement_expense_completions.c.expense_transaction_id == expense_id
    )
    if conn.execute(statement).first() is None:
        insert_or_select_unique_row(
            conn, insert(reimbursement_expense_completions).values(expense_transaction_id=expense_id), statement
        )
