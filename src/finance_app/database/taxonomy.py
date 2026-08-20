"""Low-level taxonomy metadata persistence helpers.

Provides SQLAlchemy Core upserts and seed orchestration for category and tag
metadata. Feature modules use these helpers for taxonomy writes while startup
seeding can call them without importing feature packages.
"""

from typing import Any

from sqlalchemy import case, insert, select, update

from finance_app.core.builtin_taxonomy import (
    BUILTIN_CATEGORIES,
    BUILTIN_TAGS,
    builtin_category_names,
    builtin_tag_names,
)
from finance_app.core.taxonomy import clean_color, clean_label, load_category_seed, tag_color_for_name
from finance_app.database.tables import categories as categories_table
from finance_app.database.tables import normalize_name_key
from finance_app.database.tables import tags as tags_table
from finance_app.database.upsert import insert_or_select_unique_row


def seed_category_taxonomy(conn: Any) -> None:
    """Seed built-in categories plus user-editable taxonomy rows."""
    seed = load_category_seed()
    categories = seed["categories"]
    tags = seed["tags"]
    reserved_category_names = {name.casefold() for name in builtin_category_names()}
    reserved_tag_names = {name.casefold() for name in builtin_tag_names()}

    for category in BUILTIN_CATEGORIES:
        upsert_category_metadata(
            conn,
            category["name"],
            category.get("description"),
            category.get("instruction"),
            builtin_key=category["key"],
        )

    for category in categories:
        if category["name"].casefold() in reserved_category_names:
            continue
        upsert_category_metadata(
            conn,
            category["name"],
            category.get("description"),
            category.get("instruction"),
        )

    for tag in BUILTIN_TAGS:
        upsert_tag_metadata(
            conn,
            tag["name"],
            tag.get("description"),
            tag.get("instruction"),
            tag.get("color"),
            builtin_key=tag["key"],
        )

    for tag in tags:
        if tag["name"].casefold() in reserved_tag_names:
            continue
        upsert_tag_metadata(
            conn,
            tag["name"],
            tag.get("description"),
            tag.get("instruction"),
            tag.get("color"),
        )


def builtin_tag_order_expression() -> Any:
    """Return a Core expression that sorts built-in tags after user tags."""
    return case((tags_table.c.builtin_key.is_not(None), 1), else_=0)


def upsert_category_metadata(
    conn: Any,
    name: object,
    description: object = "",
    instruction: object = "",
    builtin_key: object | None = None,
) -> str | None:
    """Insert or update one category metadata row."""
    category = clean_label(name)
    if not category:
        return None

    category_key = normalize_name_key(category)
    normalized_builtin_key = clean_label(builtin_key).casefold() if builtin_key else None
    category_select = select(
        categories_table.c.id,
        categories_table.c.builtin_key,
    ).where(
        categories_table.c.builtin_key == normalized_builtin_key
        if normalized_builtin_key
        else categories_table.c.name_key == category_key
    )
    existing = conn.execute(category_select).mappings().fetchone()
    if existing is None and normalized_builtin_key:
        category_select = select(
            categories_table.c.id,
            categories_table.c.builtin_key,
        ).where(categories_table.c.name_key == category_key)
        existing = conn.execute(category_select).mappings().fetchone()

    if existing is None:
        existing, inserted = insert_or_select_unique_row(
            conn,
            insert(categories_table).values(
                name=category,
                builtin_key=normalized_builtin_key,
                description=description or "",
                instruction=instruction or "",
            ),
            category_select,
        )
        if inserted:
            return category

    if existing is not None:
        if existing["builtin_key"] and not normalized_builtin_key:
            return category
        conn.execute(
            update(categories_table)
            .where(categories_table.c.id == existing["id"])
            .values(
                name=category,
                builtin_key=normalized_builtin_key or existing["builtin_key"],
                description=description or "",
                instruction=instruction or "",
            )
        )
    return category


def upsert_tag_metadata(
    conn: Any,
    name: object,
    description: object = "",
    instruction: object = "",
    color: object | None = None,
    builtin_key: object | None = None,
) -> str | None:
    """Insert or update one tag metadata row."""
    tag = clean_label(name)
    if not tag:
        return None
    tag_color = clean_color(color) or tag_color_for_name(tag)
    tag_key = normalize_name_key(tag)
    normalized_builtin_key = clean_label(builtin_key).casefold() if builtin_key else None

    tag_select = select(
        tags_table.c.id,
        tags_table.c.builtin_key,
        tags_table.c.color,
    ).where(
        tags_table.c.builtin_key == normalized_builtin_key
        if normalized_builtin_key
        else tags_table.c.name_key == tag_key
    )
    existing = conn.execute(tag_select).mappings().fetchone()
    if existing is None and normalized_builtin_key:
        tag_select = select(
            tags_table.c.id,
            tags_table.c.builtin_key,
            tags_table.c.color,
        ).where(tags_table.c.name_key == tag_key)
        existing = conn.execute(tag_select).mappings().fetchone()

    if existing is None:
        existing, inserted = insert_or_select_unique_row(
            conn,
            insert(tags_table).values(
                name=tag,
                builtin_key=normalized_builtin_key,
                description=description or "",
                instruction=instruction or "",
                color=tag_color,
            ),
            tag_select,
        )
        if inserted:
            return tag

    if existing is not None:
        if existing["builtin_key"] and not normalized_builtin_key:
            return tag
        conn.execute(
            update(tags_table)
            .where(tags_table.c.id == existing["id"])
            .values(
                name=tag,
                builtin_key=normalized_builtin_key or existing["builtin_key"],
                description=description or "",
                instruction=instruction or "",
                color=tag_color if normalized_builtin_key else clean_color(existing["color"]) or tag_color,
            )
        )
    return tag
