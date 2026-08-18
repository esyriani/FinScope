"""SQLAlchemy helpers for canonical transaction category lookups.

Category foreign keys are the canonical assignment. Text category columns are
display/import caches and are not used as query identity.
"""

from typing import Any

from sqlalchemy import func, select

from finance_app.database.tables import categories as categories_table
from finance_app.database.tables import transactions as transactions_table


def transaction_category_label_expression(
    unknown_category: object,
    transaction_table: Any = transactions_table,
    joined_category_name: Any | None = None,
) -> Any:
    """Return a category label expression selected through category_id."""
    return category_label_expression(transaction_table, unknown_category, joined_category_name)


def category_label_expression(
    table: Any,
    unknown_category: object,
    joined_category_name: Any | None = None,
) -> Any:
    """Return a category label expression selected through category_id."""
    category_name = joined_category_name if joined_category_name is not None else category_name_scalar(table)
    return func.coalesce(category_name, unknown_category)


def transaction_category_name_scalar(transaction_table: Any = transactions_table) -> Any:
    """Return the category name selected through the transaction category_id."""
    return category_name_scalar(transaction_table)


def category_name_scalar(table: Any) -> Any:
    """Return the category name selected through a row's category_id."""
    category_lookup = categories_table.alias()
    return select(category_lookup.c.name).where(category_lookup.c.id == table.c.category_id).scalar_subquery()


def transaction_category_join_condition(
    transaction_table: Any = transactions_table,
    category_table: Any = categories_table,
) -> Any:
    """Return the category join condition for transaction rows."""
    return category_table.c.id == transaction_table.c.category_id


def category_assignment_condition(table: Any, category_id: Any, category_name: object) -> Any:
    """Return a predicate for canonical category usage."""
    del category_name
    return table.c.category_id == category_id


def category_assignment_to_row_condition(
    table: Any,
    category_table: Any = categories_table,
) -> Any:
    """Return a predicate matching a row to a category."""
    return table.c.category_id == category_table.c.id
