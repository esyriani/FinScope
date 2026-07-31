"""SQLAlchemy helpers for canonical transaction category lookups.

Category foreign keys are the canonical assignment when present. The legacy
text category columns remain as display/import caches and as a fallback for
rows that do not have a category_id yet.
"""

from typing import Any

from sqlalchemy import and_, func, or_, select

from finance_app.database.tables import categories as categories_table
from finance_app.database.tables import normalize_name_key
from finance_app.database.tables import transactions as transactions_table


def transaction_category_label_expression(
    unknown_category: object,
    transaction_table: Any = transactions_table,
    joined_category_name: Any | None = None,
) -> Any:
    """Return a category label expression where category_id takes precedence."""
    return category_label_expression(transaction_table, unknown_category, joined_category_name)


def category_label_expression(
    table: Any,
    unknown_category: object,
    joined_category_name: Any | None = None,
) -> Any:
    """Return a category label expression where category_id takes precedence."""
    category_name = joined_category_name if joined_category_name is not None else category_name_scalar(table)
    return func.coalesce(category_name, table.c.category, unknown_category)


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
    """Return a join condition that prefers category_id over cached labels."""
    return or_(
        category_table.c.id == transaction_table.c.category_id,
        and_(
            transaction_table.c.category_id.is_(None),
            category_table.c.name_key == func.lower(func.trim(transaction_table.c.category)),
        ),
    )


def category_assignment_condition(table: Any, category_id: Any, category_name: object) -> Any:
    """Return a predicate for canonical category usage with legacy fallback."""
    return or_(
        table.c.category_id == category_id,
        and_(
            table.c.category_id.is_(None),
            func.lower(func.trim(table.c.category)) == normalize_name_key(category_name),
        ),
    )


def category_assignment_to_row_condition(
    table: Any,
    category_table: Any = categories_table,
) -> Any:
    """Return a predicate matching a row to a category, with ID precedence."""
    return or_(
        table.c.category_id == category_table.c.id,
        and_(
            table.c.category_id.is_(None),
            func.lower(func.trim(table.c.category)) == category_table.c.name_key,
        ),
    )
