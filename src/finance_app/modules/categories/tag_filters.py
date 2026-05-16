"""SQLAlchemy Core helpers for taxonomy tag filters.

Provides reusable EXISTS filters for transaction and category-rule tag link
tables. Callers keep ownership of the query being built.
"""

from sqlalchemy import exists, select

from finance_app.database.tables import (
    category_rule_tags as category_rule_tags_table,
    tags as tags_table,
    transaction_tags as transaction_tags_table,
    transactions as transactions_table,
)


def transaction_tag_condition(selected_tags, transaction_id_column=None, include=True):
    """Return a Core EXISTS condition for transaction tag filtering."""
    tags = [tag for tag in selected_tags if tag not in (None, "")]
    if not tags:
        return None

    if transaction_id_column is None:
        transaction_id_column = transactions_table.c.id
    condition = exists(
        select(1)
        .select_from(
            transaction_tags_table.join(
                tags_table,
                tags_table.c.id == transaction_tags_table.c.tag_id,
            )
        )
        .where(
            transaction_tags_table.c.transaction_id == transaction_id_column,
            tags_table.c.name.in_(tags),
        )
    )
    return condition if include else ~condition


def rule_tag_condition(selected_tags, rule_id_column, include=True):
    """Return a Core EXISTS condition for category-rule tag filtering."""
    tags = [tag for tag in selected_tags if tag not in (None, "")]
    if not tags:
        return None

    condition = exists(
        select(1)
        .select_from(
            category_rule_tags_table.join(
                tags_table,
                tags_table.c.id == category_rule_tags_table.c.tag_id,
            )
        )
        .where(
            category_rule_tags_table.c.rule_id == rule_id_column,
            tags_table.c.name.in_(tags),
        )
    )
    return condition if include else ~condition
