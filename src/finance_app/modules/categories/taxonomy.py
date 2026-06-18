"""Category and tag taxonomy helpers."""

from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from sqlalchemy import case, delete, func, insert, select, update

from finance_app.core.constants import (
    BASE_DIR,
    CATEGORY_SOURCE_UNKNOWN,
    TRANSACTION_TAG_SOURCES,
)
from finance_app.database.tables import (
    categories as categories_table,
)
from finance_app.database.tables import (
    category_rule_tags as category_rule_tags_table,
)
from finance_app.database.tables import (
    tags as tags_table,
)
from finance_app.database.tables import (
    transaction_tags as transaction_tags_table,
)
from finance_app.database.upsert import insert_or_select_unique_row
from finance_app.modules.categories.builtins import (
    BUILTIN_CATEGORIES,
    BUILTIN_TAGS,
    builtin_category_names,
)
from finance_app.modules.categories.builtins import (
    builtin_tag_names as registry_builtin_tag_names,
)

CATEGORY_SEED_PATH = Path(BASE_DIR) / "taxonomy.yml"
DEFAULT_TAG_COLOR = "#64748b"
TAG_COLORS = {
    "Reimbursable": "#2563eb",
    "Tax": "#b45309",
    "Children": "#db2777",
    "Family": "#7c3aed",
    "Trip": "#0e7490",
    "Conference": "#4f46e5",
    "Shared": "#0f766e",
    "Subscription": "#16a34a",
    "Vehicle": "#c2410c",
    "Insurance": "#dc2626",
    "Donation": "#9333ea",
    "Government": "#475569",
}
TAG_COLOR_PALETTE = (
    "#2563eb",
    "#0f766e",
    "#b45309",
    "#dc2626",
    "#7c3aed",
    "#0e7490",
    "#db2777",
    "#4f46e5",
    "#16a34a",
    "#c2410c",
    "#9333ea",
    "#475569",
)


def load_category_seed(path: str | Path = CATEGORY_SEED_PATH) -> dict[str, list[dict[str, str]]]:
    """Load category seed."""
    if not Path(path).exists():
        return {"categories": [], "tags": []}

    sections: dict[str, list[dict[str, str]]] = {"categories": [], "tags": []}
    current_section: str | None = None
    current_item: dict[str, str] | None = None

    for raw_line in Path(path).read_text(encoding="utf-8").splitlines():
        line = raw_line.rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue

        if not line.startswith(" ") and line.endswith(":"):
            current_section = line[:-1].strip()
            current_item = None
            continue

        if current_section not in sections:
            continue

        stripped = line.strip()
        if stripped.startswith("- "):
            current_item = {}
            sections[current_section].append(current_item)
            stripped = stripped[2:].strip()
            if not stripped:
                continue

        if current_item is None or ":" not in stripped:
            continue

        key, value = stripped.split(":", 1)
        current_item[key.strip()] = unquote_yaml_scalar(value.strip())

    return {
        "categories": [clean_taxonomy_item(item) for item in sections["categories"] if item.get("name")],
        "tags": [clean_taxonomy_item(item) for item in sections["tags"] if item.get("name")],
    }


def builtin_tag_names() -> tuple[str, ...]:
    """Return tag names managed by FinScope."""
    return registry_builtin_tag_names()


def is_builtin_tag_name(name: object) -> bool:
    """Return whether a tag name is managed by FinScope."""
    normalized = clean_label(name).casefold()
    return normalized in {tag_name.casefold() for tag_name in builtin_tag_names()}


def builtin_tag_order_expression() -> Any:
    """Return a Core expression that sorts built-in tags after user tags."""
    return case((tags_table.c.builtin_key.is_not(None), 1), else_=0)


def unquote_yaml_scalar(value: str) -> str:
    """Unquote yaml scalar."""
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def clean_taxonomy_item(item: Mapping[str, Any]) -> dict[str, str]:
    """Clean taxonomy item."""
    return {
        "name": clean_label(item.get("name")),
        "description": str(item.get("description") or "").strip(),
        "instruction": str(item.get("instruction") or "").strip(),
        "color": clean_color(item.get("color")),
    }


def clean_label(value: object) -> str:
    """Clean label."""
    return " ".join(str(value or "").strip().split())


def clean_color(value: object) -> str:
    """Clean color."""
    color = str(value or "").strip()
    hex_digits = "0123456789abcdefABCDEF"
    if len(color) == 7 and color.startswith("#") and all(char in hex_digits for char in color[1:]):
        return color.lower()
    return ""


def tag_color_for_name(name: object) -> str:
    """Build color for name."""
    tag = clean_label(name)
    if not tag:
        return DEFAULT_TAG_COLOR
    if tag in TAG_COLORS:
        return TAG_COLORS[tag]

    checksum = sum((index + 1) * ord(char) for index, char in enumerate(tag.casefold()))
    return TAG_COLOR_PALETTE[checksum % len(TAG_COLOR_PALETTE)]


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


def upsert_category_metadata(
    conn: Any,
    name: object,
    description: object = "",
    instruction: object = "",
    builtin_key: object | None = None,
) -> str | None:
    """Insert or update category metadata."""
    category = clean_label(name)
    if not category:
        return None

    normalized_builtin_key = clean_label(builtin_key).casefold() if builtin_key else None
    category_select = select(
        categories_table.c.id,
        categories_table.c.builtin_key,
    ).where(
        categories_table.c.builtin_key == normalized_builtin_key
        if normalized_builtin_key
        else categories_table.c.name == category
    )
    existing = conn.execute(category_select).mappings().fetchone()
    if existing is None and normalized_builtin_key:
        category_select = select(
            categories_table.c.id,
            categories_table.c.builtin_key,
        ).where(categories_table.c.name == category)
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
    """Insert or update tag metadata."""
    tag = clean_label(name)
    if not tag:
        return None
    tag_color = clean_color(color) or tag_color_for_name(tag)
    normalized_builtin_key = clean_label(builtin_key).casefold() if builtin_key else None

    tag_select = select(
        tags_table.c.id,
        tags_table.c.builtin_key,
        tags_table.c.color,
    ).where(tags_table.c.builtin_key == normalized_builtin_key if normalized_builtin_key else tags_table.c.name == tag)
    existing = conn.execute(tag_select).mappings().fetchone()
    if existing is None and normalized_builtin_key:
        tag_select = select(
            tags_table.c.id,
            tags_table.c.builtin_key,
            tags_table.c.color,
        ).where(tags_table.c.name == tag)
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

    rows = (
        conn.execute(select(tags_table.c.id, tags_table.c.name).where(tags_table.c.name.in_(normalized)))
        .mappings()
        .fetchall()
    )
    return {row["name"]: row["id"] for row in rows}


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
