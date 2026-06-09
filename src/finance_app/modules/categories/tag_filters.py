"""SQLAlchemy Core helpers for taxonomy tag filters.

Provides reusable EXISTS filters for transaction and category-rule tag link
tables. Callers keep ownership of the query being built.
"""

from collections.abc import Iterable
from typing import Any

from sqlalchemy import exists, or_, select

from finance_app.database.tables import (
    category_rule_tags as category_rule_tags_table,
)
from finance_app.database.tables import (
    tags as tags_table,
)
from finance_app.database.tables import (
    transaction_tags as transaction_tags_table,
)
from finance_app.database.tables import (
    transactions as transactions_table,
)

UNTAGGED_TAG_FILTER = "__untagged__"


def split_tag_filter_values(selected_tags: Iterable[object]) -> tuple[list[str], bool]:
    """Return concrete tag names and whether the virtual untagged option is selected."""
    tags: list[str] = []
    include_untagged = False
    for tag in selected_tags:
        text = str(tag or "").strip()
        if not text:
            continue
        if text == UNTAGGED_TAG_FILTER:
            include_untagged = True
        else:
            tags.append(text)
    return tags, include_untagged


def has_concrete_tag_filter(selected_tags: Iterable[object]) -> bool:
    """Return whether selected filter values contain at least one real tag name."""
    tags, _ = split_tag_filter_values(selected_tags)
    return bool(tags)


def transaction_tag_condition(
    selected_tags: Iterable[object],
    transaction_id_column: Any | None = None,
    include: bool = True,
) -> Any | None:
    """Return a Core EXISTS condition for transaction tag filtering."""
    tags, include_untagged = split_tag_filter_values(selected_tags)
    if not tags and not include_untagged:
        return None

    if transaction_id_column is None:
        transaction_id_column = transactions_table.c.id
    has_selected_tag = exists(
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
    has_any_tag = exists(
        select(1).where(
            transaction_tags_table.c.transaction_id == transaction_id_column,
        )
    )
    selected_conditions: list[Any] = []
    if tags:
        selected_conditions.append(has_selected_tag)
    if include_untagged:
        selected_conditions.append(~has_any_tag)

    selected = or_(*selected_conditions) if len(selected_conditions) > 1 else selected_conditions[0]
    return selected if include else ~selected


def rule_tag_condition(selected_tags: Iterable[object], rule_id_column: Any, include: bool = True) -> Any | None:
    """Return a Core EXISTS condition for category-rule tag filtering."""
    tags, include_untagged = split_tag_filter_values(selected_tags)
    if not tags and not include_untagged:
        return None

    has_selected_tag = exists(
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
    has_any_tag = exists(select(1).where(category_rule_tags_table.c.rule_id == rule_id_column))
    selected_conditions: list[Any] = []
    if tags:
        selected_conditions.append(has_selected_tag)
    if include_untagged:
        selected_conditions.append(~has_any_tag)

    selected = or_(*selected_conditions) if len(selected_conditions) > 1 else selected_conditions[0]
    return selected if include else ~selected
