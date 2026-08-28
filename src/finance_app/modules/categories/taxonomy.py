"""Category and tag taxonomy helpers."""

from collections.abc import Iterable, Mapping
from typing import Any

from sqlalchemy import case, delete, func, insert, select

from finance_app.core.constants import CATEGORY_SOURCE_UNKNOWN, TRANSACTION_TAG_SOURCES
from finance_app.core.taxonomy import clean_color, clean_label, tag_color_for_name
from finance_app.database.tables import (
    categories as categories_table,
)
from finance_app.database.tables import (
    category_rule_tags as category_rule_tags_table,
)
from finance_app.database.tables import normalize_name_key
from finance_app.database.tables import (
    tags as tags_table,
)
from finance_app.database.tables import (
    transaction_tags as transaction_tags_table,
)
from finance_app.database.taxonomy import builtin_tag_order_expression


def get_category_rows(conn: Any) -> list[Mapping[str, Any]]:
    """Return category rows."""
    return (
        conn.execute(
            select(
                categories_table.c.id,
                categories_table.c.name,
                categories_table.c.description,
                categories_table.c.instruction,
            ).order_by(
                case((categories_table.c.builtin_key.is_not(None), 1), else_=0),
                func.lower(categories_table.c.name),
                categories_table.c.name,
            )
        )
        .mappings()
        .fetchall()
    )


def get_category_description_map(conn: Any) -> dict[str, str]:
    """Return category descriptions keyed by category name."""
    return {row["name"]: row["description"] or "" for row in get_category_rows(conn)}


def get_tag_rows(conn: Any) -> list[Mapping[str, Any]]:
    """Return tag rows."""
    return (
        conn.execute(
            select(
                tags_table.c.id,
                tags_table.c.name,
                tags_table.c.builtin_key,
                tags_table.c.description,
                tags_table.c.instruction,
                tags_table.c.color,
            ).order_by(
                builtin_tag_order_expression(),
                func.lower(tags_table.c.name),
                tags_table.c.name,
            )
        )
        .mappings()
        .fetchall()
    )


def get_tag_options(conn: Any) -> list[str]:
    """Return tag options."""
    return [row["name"] for row in get_tag_rows(conn)]


def get_tag_option_rows(conn: Any) -> list[dict[str, str]]:
    """Return tag option rows."""
    return [
        {
            "name": row["name"],
            "description": row["description"] or "",
            "instruction": row["instruction"] or "",
            "color": clean_color(row["color"]) or tag_color_for_name(row["name"]),
        }
        for row in get_tag_rows(conn)
    ]


def get_tag_color_map(conn: Any) -> dict[str, str]:
    """Return tag color map."""
    return {row["name"]: clean_color(row["color"]) or tag_color_for_name(row["name"]) for row in get_tag_rows(conn)}


def normalize_tag_names(values: Iterable[object] | str | None, allowed_tags: Iterable[str] | None = None) -> list[str]:
    """Normalize tag names."""
    if values is None:
        values = []
    if isinstance(values, str):
        values = split_tag_text(values)

    allowed_by_fold = {str(tag).casefold(): tag for tag in (allowed_tags or [])}
    normalized: list[str] = []
    seen: set[str] = set()

    for value in values:
        text = clean_label(value)
        if not text:
            continue
        tag = allowed_by_fold.get(text.casefold(), text if allowed_tags is None else None)
        if not tag or tag in seen:
            continue
        normalized.append(tag)
        seen.add(tag)

    return normalized


def split_tag_text(value: object) -> list[str]:
    """Split tag text."""
    return [chunk.strip() for chunk in str(value or "").replace("|", ",").replace(";", ",").split(",") if chunk.strip()]


def tag_ids_by_name(conn: Any, tag_names: Iterable[object] | str | None) -> dict[str, int]:
    """Build ids by name."""
    normalized = normalize_tag_names(tag_names, get_tag_options(conn))
    if not normalized:
        return {}

    keys_by_name = {name: normalize_name_key(name) for name in normalized}
    rows = (
        conn.execute(
            select(tags_table.c.id, tags_table.c.name_key).where(tags_table.c.name_key.in_(keys_by_name.values()))
        )
        .mappings()
        .fetchall()
    )
    ids_by_key = {row["name_key"]: row["id"] for row in rows}
    return {name: ids_by_key[key] for name, key in keys_by_name.items() if key in ids_by_key}


def set_rule_tags(conn: Any, rule_id: object, tag_names: Iterable[object] | str | None) -> None:
    """Set rule tags."""
    conn.execute(delete(category_rule_tags_table).where(category_rule_tags_table.c.rule_id == rule_id))
    tag_ids = tag_ids_by_name(conn, tag_names)
    for tag_id in tag_ids.values():
        conn.execute(insert(category_rule_tags_table).values(rule_id=rule_id, tag_id=tag_id))


def get_rule_tags_by_rule_id(conn: Any, rule_ids: Iterable[object]) -> dict[Any, list[str]]:
    """Return rule tags by rule ID."""
    rule_ids = [rule_id for rule_id in rule_ids if rule_id is not None]
    if not rule_ids:
        return {}

    rows = (
        conn.execute(
            select(category_rule_tags_table.c.rule_id, tags_table.c.name)
            .join(tags_table, tags_table.c.id == category_rule_tags_table.c.tag_id)
            .where(category_rule_tags_table.c.rule_id.in_(rule_ids))
            .order_by(func.lower(tags_table.c.name), tags_table.c.name)
        )
        .mappings()
        .fetchall()
    )

    result: dict[Any, list[str]] = {rule_id: [] for rule_id in rule_ids}
    for row in rows:
        result.setdefault(row["rule_id"], []).append(row["name"])
    return result


def set_transaction_tags(
    conn: Any,
    transaction_id: object,
    tag_names: Iterable[object] | str | None,
    source: object = CATEGORY_SOURCE_UNKNOWN,
    rule_id: object | None = None,
) -> None:
    """Set transaction tags."""
    normalized_source = normalize_transaction_tag_source(source)
    conn.execute(delete(transaction_tags_table).where(transaction_tags_table.c.transaction_id == transaction_id))
    tag_ids = tag_ids_by_name(conn, tag_names)
    for tag_id in tag_ids.values():
        conn.execute(
            insert(transaction_tags_table).values(
                transaction_id=transaction_id,
                tag_id=tag_id,
                source=normalized_source,
                rule_id=rule_id,
                assigned_at=func.current_timestamp(),
            )
        )


def normalize_transaction_tag_source(source: object) -> str:
    """Return a valid persisted transaction-tag assignment source."""
    text = str(source or CATEGORY_SOURCE_UNKNOWN).strip().lower()
    return text if text in TRANSACTION_TAG_SOURCES else CATEGORY_SOURCE_UNKNOWN


def get_transaction_tag_names(conn: Any, transaction_id: object) -> list[str]:
    """Return transaction tag names."""
    rows = (
        conn.execute(
            select(tags_table.c.name)
            .join(transaction_tags_table, transaction_tags_table.c.tag_id == tags_table.c.id)
            .where(transaction_tags_table.c.transaction_id == transaction_id)
            .order_by(func.lower(tags_table.c.name), tags_table.c.name)
        )
        .mappings()
        .fetchall()
    )
    return [row["name"] for row in rows]


def get_transaction_tags_by_id(conn: Any, transaction_ids: Iterable[object]) -> dict[Any, list[str]]:
    """Return transaction tags by ID."""
    transaction_ids = [tx_id for tx_id in transaction_ids if tx_id is not None]
    if not transaction_ids:
        return {}

    rows = (
        conn.execute(
            select(transaction_tags_table.c.transaction_id, tags_table.c.name)
            .join(tags_table, tags_table.c.id == transaction_tags_table.c.tag_id)
            .where(transaction_tags_table.c.transaction_id.in_(transaction_ids))
            .order_by(func.lower(tags_table.c.name), tags_table.c.name)
        )
        .mappings()
        .fetchall()
    )

    result: dict[Any, list[str]] = {tx_id: [] for tx_id in transaction_ids}
    for row in rows:
        result.setdefault(row["transaction_id"], []).append(row["name"])
    return result
