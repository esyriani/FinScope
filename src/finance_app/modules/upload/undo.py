"""Upload undo persistence helpers.

Provides SQLAlchemy Core helpers that remove imported statement data and
restore transactions changed by Interac enrichment. Callers manage transactions.
"""

from sqlalchemy import delete, func, select, update

from finance_app.core.constants import TRANSACTION_KIND_EXPENSE
from finance_app.database.tables import (
    statements as statements_table,
    transactions as transactions_table,
)
from finance_app.modules.categories.repository import resolve_category_id
from finance_app.modules.categories.taxonomy import set_transaction_tags


def statement_filename_row(conn, statement_id):
    """Return the filename for one statement ID.

    Args:
        conn: Open SQLAlchemy Core connection.
        statement_id: Persisted statement primary key.

    Returns:
        A mapping row with the filename, or ``None`` when the statement is gone.
    """
    return conn.execute(
        select(statements_table.c.filename).where(statements_table.c.id == statement_id)
    ).mappings().fetchone()


def statement_transaction_count(conn, statement_id):
    """Return the number of transactions imported by one statement."""
    return conn.execute(
        select(func.count().label("count"))
        .select_from(transactions_table)
        .where(transactions_table.c.statement_id == statement_id)
    ).scalar_one()


def delete_statement_transactions(conn, statement_id):
    """Delete all transaction rows imported by one statement."""
    conn.execute(
        delete(transactions_table).where(transactions_table.c.statement_id == statement_id)
    )


def delete_statement(conn, statement_id):
    """Delete one persisted statement row."""
    conn.execute(delete(statements_table).where(statements_table.c.id == statement_id))


def restore_interac_undo_state(conn, undo_state):
    """Restore transactions changed by Interac enrichment."""
    changes = (undo_state or {}).get("updated_transactions") or []
    restored_count = 0
    for change in changes:
        cursor = restore_enriched_transaction(conn, change)
        if cursor.rowcount:
            set_transaction_tags(
                conn,
                change["id"],
                change.get("tags", []),
                source=change["category_source"],
                rule_id=change["category_rule_id"],
            )
            restored_count += 1
    return restored_count


def restore_enriched_transaction(conn, change):
    """Restore a transaction changed by upload enrichment."""
    values = {
        "merchant_id": change["merchant_id"],
        "description": change["description"],
        "category": change["category"],
        "category_id": (
            change.get("category_id")
            if change.get("category_id") is not None
            else resolve_category_id(conn, change["category"])
        ),
        "needs_review": change["needs_review"],
        "category_source": change["category_source"],
        "category_confidence": change["category_confidence"],
        "category_rule_id": change["category_rule_id"],
        "category_metadata": change.get("category_metadata"),
        "categorized_at": change["categorized_at"],
        "reviewed_at": change["reviewed_at"],
        "transaction_kind": change.get("transaction_kind", TRANSACTION_KIND_EXPENSE),
    }
    return conn.execute(
        update(transactions_table)
        .where(transactions_table.c.id == change["id"])
        .values(**values)
    )
