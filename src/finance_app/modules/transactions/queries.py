"""SQLAlchemy Core query helpers for the transactions feature."""

from sqlalchemy import func, select

from finance_app.database.tables import (
    accounts as accounts_table,
    transactions as transactions_table,
)


def transaction_list_select():
    """Return the shared transaction list projection."""
    return (
        select(
            transactions_table.c.id,
            transactions_table.c.tx_date,
            transactions_table.c.description,
            transactions_table.c.amount,
            transactions_table.c.category,
            transactions_table.c.category_source,
            transactions_table.c.category_confidence,
            transactions_table.c.reviewed_at,
            transactions_table.c.needs_review,
            transactions_table.c.ignored,
            transactions_table.c.transaction_kind,
            transactions_table.c.description.label("full_description"),
            accounts_table.c.name.label("account_name"),
        )
        .select_from(
            transactions_table.outerjoin(
                accounts_table,
                accounts_table.c.id == transactions_table.c.account_id,
            )
        )
    )


def count_transactions(conn, filters):
    """Count transactions."""
    return conn.execute(
        select(func.count())
        .select_from(
            transactions_table.outerjoin(
                accounts_table,
                accounts_table.c.id == transactions_table.c.account_id,
            )
        )
        .where(*filters)
    ).scalar_one()


def fetch_transactions(conn, filters, sort_expression, direction, page_size, offset):
    """Fetch transactions."""
    sort_order = sort_expression.desc() if direction == "desc" else sort_expression.asc()
    return conn.execute(
        transaction_list_select()
        .where(*filters)
        .order_by(sort_order, transactions_table.c.id.desc())
        .limit(page_size)
        .offset(offset)
    ).mappings().fetchall()


def fetch_transaction_ids(conn, filters, sort_expression, direction):
    """Fetch IDs for all transactions matching the current list filters."""
    sort_order = sort_expression.desc() if direction == "desc" else sort_expression.asc()
    return [
        row["id"]
        for row in conn.execute(
            transaction_list_select()
            .where(*filters)
            .order_by(sort_order, transactions_table.c.id.desc())
        ).mappings()
    ]


def fetch_distinct_categories(conn):
    """Fetch distinct categories."""
    return conn.execute(
        select(transactions_table.c.category)
        .where(transactions_table.c.category.is_not(None))
        .distinct()
        .order_by(transactions_table.c.category)
    ).mappings().fetchall()
