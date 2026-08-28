"""Application orchestration for the taxonomy admin feature."""

import json
from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy import case, delete, func, select, update

from finance_app.core.builtin_taxonomy import (
    is_builtin_category_name,
    is_builtin_tag_name,
)
from finance_app.core.category_sql import category_assignment_condition, category_assignment_to_row_condition
from finance_app.core.taxonomy import clean_color, clean_label, tag_color_for_name
from finance_app.database.engine import db_core_transaction
from finance_app.database.tables import (
    categories as categories_table,
)
from finance_app.database.tables import (
    category_rule_tags as category_rule_tags_table,
)
from finance_app.database.tables import (
    category_rules as category_rules_table,
)
from finance_app.database.tables import normalize_name_key
from finance_app.database.tables import (
    tags as tags_table,
)
from finance_app.database.tables import (
    transaction_tags as transaction_tags_table,
)
from finance_app.database.tables import (
    transactions as transactions_table,
)
from finance_app.database.taxonomy import (
    builtin_tag_order_expression,
    upsert_category_metadata,
    upsert_tag_metadata,
)
from finance_app.modules.categories.repository import rename_category
from finance_app.modules.taxonomy_admin.forms import (
    parse_category_form,
    parse_required_int,
    parse_tag_form,
)

TAXONOMY_YAML_SECTIONS = ("categories", "tags")


def build_taxonomy_context() -> dict[str, Any]:
    """Build taxonomy context."""
    with db_core_transaction() as conn:
        return {
            "categories": fetch_category_rows(conn),
            "tags": fetch_tag_rows(conn),
        }


def export_taxonomy_yaml(conn: Any = None) -> str:
    """Return category and tag metadata in the FinScope taxonomy YAML format.

    Args:
        conn: Optional open SQLAlchemy Core connection. When omitted, the
            function opens its own transaction.

    Returns:
        YAML text containing category and tag names, descriptions, LLM
        instructions, tag colors, and built-in keys.
    """
    if conn is None:
        with db_core_transaction() as conn:
            return export_taxonomy_yaml(conn)

    lines = ["categories:"]
    for category in fetch_category_export_rows(conn):
        lines.extend(
            [
                f"  - name: {yaml_scalar(category['name'])}",
                f"    description: {yaml_scalar(category['description'])}",
                f"    instruction: {yaml_scalar(category['instruction'])}",
                f"    builtin_key: {yaml_scalar(category['builtin_key'])}",
            ]
        )

    lines.append("tags:")
    for tag in fetch_tag_export_rows(conn):
        lines.extend(
            [
                f"  - name: {yaml_scalar(tag['name'])}",
                f"    description: {yaml_scalar(tag['description'])}",
                f"    instruction: {yaml_scalar(tag['instruction'])}",
                f"    color: {yaml_scalar(tag['color'])}",
                f"    builtin_key: {yaml_scalar(tag['builtin_key'])}",
            ]
        )

    return "\n".join(lines) + "\n"


def import_taxonomy_yaml_text(raw_text: str, conn: Any = None) -> dict[str, int]:
    """Import category and tag metadata from FinScope taxonomy YAML text.

    Args:
        raw_text: Uploaded YAML text using ``categories`` and ``tags`` lists.
        conn: Optional open SQLAlchemy Core connection. When omitted, the
            function opens its own transaction.

    Returns:
        A mapping with imported category count, imported tag count, and skipped
        built-in counts. Built-ins are intentionally skipped because their
        definitions are managed by the application registry.

    Raises:
        ValueError: If the YAML text is malformed or has no importable entries.
    """
    parsed = parse_taxonomy_yaml(raw_text)
    if not parsed["categories"] and not parsed["tags"]:
        raise ValueError("No categories or tags were found.")

    if conn is None:
        with db_core_transaction() as conn:
            return import_taxonomy_yaml_text(raw_text, conn)

    imported_categories = 0
    imported_tags = 0
    skipped_builtin_categories = 0
    skipped_builtin_tags = 0

    for category in parsed["categories"]:
        if category["builtin_key"] or is_builtin_category_name(category["name"]):
            skipped_builtin_categories += 1
            continue
        upsert_category_metadata(
            conn,
            category["name"],
            category["description"],
            category["instruction"],
        )
        imported_categories += 1

    for tag in parsed["tags"]:
        if tag["builtin_key"] or is_builtin_tag_name(tag["name"]):
            skipped_builtin_tags += 1
            continue
        upsert_tag_metadata(
            conn,
            tag["name"],
            tag["description"],
            tag["instruction"],
            tag["color"],
        )
        imported_tags += 1

    if not imported_categories and not imported_tags and (skipped_builtin_categories or skipped_builtin_tags):
        raise ValueError("Only built-in categories or tags were found. Nothing was imported.")

    return {
        "categories": imported_categories,
        "tags": imported_tags,
        "skipped_builtin_categories": skipped_builtin_categories,
        "skipped_builtin_tags": skipped_builtin_tags,
    }


def parse_taxonomy_yaml(raw_text: object) -> dict[str, list[dict[str, str]]]:
    """Parse FinScope taxonomy YAML into cleaned category and tag rows.

    The parser intentionally supports the flat YAML list shape used by
    ``taxonomy.yml`` and taxonomy exports, which keeps imports dependency-free
    while still accepting quoted scalar values with escaped newlines.
    """
    sections: dict[str, list[dict[str, str]]] = {section: [] for section in TAXONOMY_YAML_SECTIONS}
    current_section: str | None = None
    current_item: dict[str, str] | None = None

    for line_number, raw_line in enumerate(str(raw_text or "").splitlines(), start=1):
        line = raw_line.rstrip()
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        if not line.startswith(" ") and stripped.endswith(":"):
            current_section = stripped[:-1].strip()
            if current_section not in sections:
                raise ValueError(f"Line {line_number}: unsupported taxonomy section.")
            current_item = None
            continue

        if current_section is None:
            raise ValueError(f"Line {line_number}: expected a taxonomy section.")

        if stripped.startswith("- "):
            current_item = {}
            sections[current_section].append(current_item)
            stripped = stripped[2:].strip()
            if not stripped:
                continue

        if current_item is None:
            raise ValueError(f"Line {line_number}: expected a taxonomy list item.")

        key, value = parse_yaml_key_value(stripped, line_number)
        current_item[key] = yaml_scalar_value(value, line_number)

    cleaned = {
        "categories": [clean_imported_category(item) for item in sections["categories"]],
        "tags": [clean_imported_tag(item) for item in sections["tags"]],
    }
    validate_unique_taxonomy_names(cleaned["categories"], "Category")
    validate_unique_taxonomy_names(cleaned["tags"], "Tag")
    return cleaned


def parse_yaml_key_value(text: str, line_number: int) -> tuple[str, str]:
    """Return a key and scalar text parsed from one YAML mapping line."""
    if ":" not in text:
        raise ValueError(f"Line {line_number}: expected a key and value.")
    key, value = text.split(":", 1)
    key = key.strip()
    if not key:
        raise ValueError(f"Line {line_number}: expected a key.")
    return key, value.strip()


def yaml_scalar(value: object) -> str:
    """Return a quoted scalar compatible with YAML and JSON string parsers."""
    return json.dumps(str(value or ""), ensure_ascii=False)


def yaml_scalar_value(value: object, line_number: int) -> str:
    """Parse a YAML scalar from the supported taxonomy import subset."""
    text = str(value or "").strip()
    if not text:
        return ""
    if text[0] == '"':
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Line {line_number}: invalid quoted value.") from exc
    if len(text) >= 2 and text[0] == text[-1] == "'":
        return text[1:-1].replace("''", "'")
    return text


def clean_imported_category(item: Mapping[str, Any]) -> dict[str, str]:
    """Return one cleaned imported category metadata row."""
    name = clean_label(item.get("name"))
    if not name:
        raise ValueError("Category name is required.")
    return {
        "name": name,
        "description": str(item.get("description") or "").strip(),
        "instruction": str(item.get("instruction") or "").strip(),
        "builtin_key": clean_label(item.get("builtin_key")).casefold(),
    }


def clean_imported_tag(item: Mapping[str, Any]) -> dict[str, str]:
    """Return one cleaned imported tag metadata row."""
    name = clean_label(item.get("name"))
    if not name:
        raise ValueError("Tag name is required.")
    return {
        "name": name,
        "description": str(item.get("description") or "").strip(),
        "instruction": str(item.get("instruction") or "").strip(),
        "color": clean_color(item.get("color")) or tag_color_for_name(name),
        "builtin_key": clean_label(item.get("builtin_key")).casefold(),
    }


def validate_unique_taxonomy_names(rows: Sequence[Mapping[str, str]], label: str) -> None:
    """Raise when an imported taxonomy section repeats a name."""
    seen: set[str] = set()
    for row in rows:
        normalized = row["name"].casefold()
        if normalized in seen:
            raise ValueError(f"{label} names in the taxonomy import must be unique.")
        seen.add(normalized)


def fetch_category_export_rows(conn: Any) -> list[dict[str, Any]]:
    """Return categories with all persisted metadata fields for YAML export."""
    rows = (
        conn.execute(
            select(
                categories_table.c.name,
                func.coalesce(categories_table.c.description, "").label("description"),
                func.coalesce(categories_table.c.instruction, "").label("instruction"),
                func.coalesce(categories_table.c.builtin_key, "").label("builtin_key"),
            ).order_by(
                case((categories_table.c.builtin_key.is_not(None), 1), else_=0),
                func.lower(categories_table.c.name),
                categories_table.c.name,
            )
        )
        .mappings()
        .fetchall()
    )
    return [dict(row) for row in rows]


def fetch_tag_export_rows(conn: Any) -> list[dict[str, Any]]:
    """Return tags with all persisted metadata fields for YAML export."""
    rows = (
        conn.execute(
            select(
                tags_table.c.name,
                func.coalesce(tags_table.c.description, "").label("description"),
                func.coalesce(tags_table.c.instruction, "").label("instruction"),
                tags_table.c.color,
                func.coalesce(tags_table.c.builtin_key, "").label("builtin_key"),
            ).order_by(
                builtin_tag_order_expression(),
                func.lower(tags_table.c.name),
                tags_table.c.name,
            )
        )
        .mappings()
        .fetchall()
    )
    return [
        {
            **dict(row),
            "color": clean_color(row["color"]) or tag_color_for_name(row["name"]),
        }
        for row in rows
    ]


def create_category_from_form(form: Any) -> Any:
    """Create category from form."""
    values = parse_category_form(form)
    if is_builtin_category_name(values["name"]):
        raise ValueError("Built-in categories are managed by FinScope.")

    with db_core_transaction() as conn:
        category = upsert_category_metadata(
            conn,
            values["name"],
            values["description"],
            values["instruction"],
        )
        return category


def update_category_from_form(form: Any) -> str:
    """Update category from form."""
    values = parse_category_form(form)
    category_id = values["id"]
    if not isinstance(category_id, int):
        raise ValueError("Category was not found.")
    category_name = str(values["name"])

    with db_core_transaction() as conn:
        current = fetch_category_by_id(conn, category_id)
        if current is None:
            raise ValueError("Category was not found.")
        if current["builtin_key"]:
            raise ValueError("Built-in categories cannot be modified.")

        if current["name"] != category_name:
            renamed = rename_category(conn, current["name"], category_name)
            if not renamed:
                raise ValueError("Choose a unique category name.")

        conn.execute(
            update(categories_table)
            .where(categories_table.c.id == category_id)
            .values(
                description=values["description"],
                instruction=values["instruction"],
            )
        )
        return category_name


def create_tag_from_form(form: Any) -> Any:
    """Create tag from form."""
    values = parse_tag_form(form)
    if is_builtin_tag_name(values["name"]):
        raise ValueError("Built-in tags are managed by FinScope.")

    with db_core_transaction() as conn:
        tag = upsert_tag_metadata(
            conn,
            values["name"],
            values["description"],
            values["instruction"],
            values["color"],
        )
        return tag


def update_tag_from_form(form: Any) -> str:
    """Update tag from form."""
    values = parse_tag_form(form)
    tag_id = values["id"]
    if not isinstance(tag_id, int):
        raise ValueError("Tag was not found.")
    tag_name = str(values["name"])

    with db_core_transaction() as conn:
        current = fetch_tag_by_id(conn, tag_id)
        if current is None:
            raise ValueError("Tag was not found.")
        if current["builtin_key"]:
            raise ValueError("Built-in tags cannot be modified.")

        existing = conn.execute(
            select(tags_table.c.id).where(
                tags_table.c.name_key == normalize_name_key(tag_name),
                tags_table.c.id != tag_id,
            )
        ).fetchone()
        if existing:
            raise ValueError("Choose a unique tag name.")

        conn.execute(
            update(tags_table)
            .where(tags_table.c.id == tag_id)
            .values(
                name=tag_name,
                description=values["description"],
                instruction=values["instruction"],
                color=clean_color(values["color"]) or tag_color_for_name(tag_name),
            ),
        )
        return tag_name


def delete_tag_from_form(form: Any) -> str:
    """Delete tag from form."""
    tag_id = parse_required_int(form.get("tag_id"), "Tag")
    with db_core_transaction() as conn:
        tag = fetch_tag_by_id(conn, tag_id)
        if tag is None:
            raise ValueError("Tag was not found.")
        if tag["builtin_key"]:
            raise ValueError("Built-in tags cannot be deleted.")

        usage = fetch_tag_usage(conn, tag_id)
        if usage["transaction_count"] or usage["rule_count"]:
            raise ValueError("Only unused tags can be deleted.")

        conn.execute(delete(tags_table).where(tags_table.c.id == tag_id))
        return tag["name"]


def delete_category_from_form(form: Any) -> str:
    """Delete category from form."""
    category_id = parse_required_int(form.get("category_id"), "Category")
    with db_core_transaction() as conn:
        category = fetch_category_by_id(conn, category_id)
        if category is None:
            raise ValueError("Category was not found.")
        if category["builtin_key"]:
            raise ValueError("Built-in categories cannot be deleted.")

        usage = fetch_category_usage(conn, category_id, category["name"])
        if usage["transaction_count"] or usage["rule_count"]:
            raise ValueError("Only unused categories can be deleted.")

        conn.execute(delete(categories_table).where(categories_table.c.id == category_id))
        return category["name"]


def fetch_category_rows(conn: Any) -> list[dict[str, Any]]:
    """Fetch category rows."""
    transaction_count = (
        select(func.count())
        .select_from(transactions_table)
        .where(category_assignment_to_row_condition(transactions_table, categories_table))
        .correlate(categories_table)
        .scalar_subquery()
    )
    rule_count = (
        select(func.count())
        .select_from(category_rules_table)
        .where(category_assignment_to_row_condition(category_rules_table, categories_table))
        .correlate(categories_table)
        .scalar_subquery()
    )
    rows = (
        conn.execute(
            select(
                categories_table.c.id,
                categories_table.c.name,
                categories_table.c.builtin_key,
                func.coalesce(categories_table.c.description, "").label("description"),
                func.coalesce(categories_table.c.instruction, "").label("instruction"),
                transaction_count.label("transaction_count"),
                rule_count.label("rule_count"),
            ).order_by(
                case((categories_table.c.builtin_key.is_not(None), 1), else_=0),
                func.lower(categories_table.c.name),
                categories_table.c.name,
            )
        )
        .mappings()
        .fetchall()
    )
    return [
        {
            **dict(row),
            "is_builtin": bool(row["builtin_key"]),
        }
        for row in rows
    ]


def fetch_tag_rows(conn: Any) -> list[dict[str, Any]]:
    """Fetch tag rows."""
    transaction_count = (
        select(func.count())
        .select_from(transaction_tags_table)
        .where(transaction_tags_table.c.tag_id == tags_table.c.id)
        .correlate(tags_table)
        .scalar_subquery()
    )
    rule_count = (
        select(func.count())
        .select_from(category_rule_tags_table)
        .where(category_rule_tags_table.c.tag_id == tags_table.c.id)
        .correlate(tags_table)
        .scalar_subquery()
    )
    rows = (
        conn.execute(
            select(
                tags_table.c.id,
                tags_table.c.name,
                tags_table.c.builtin_key,
                func.coalesce(tags_table.c.description, "").label("description"),
                func.coalesce(tags_table.c.instruction, "").label("instruction"),
                tags_table.c.color,
                transaction_count.label("transaction_count"),
                rule_count.label("rule_count"),
            ).order_by(
                builtin_tag_order_expression(),
                func.lower(tags_table.c.name),
                tags_table.c.name,
            )
        )
        .mappings()
        .fetchall()
    )

    return [
        {
            **dict(row),
            "color": clean_color(row["color"]) or tag_color_for_name(row["name"]),
            "is_builtin": bool(row["builtin_key"]),
        }
        for row in rows
    ]


def fetch_category_by_id(conn: Any, category_id: int) -> Any:
    """Fetch category by ID."""
    return (
        conn.execute(
            select(
                categories_table.c.id,
                categories_table.c.name,
                categories_table.c.builtin_key,
                categories_table.c.description,
                categories_table.c.instruction,
            ).where(categories_table.c.id == category_id)
        )
        .mappings()
        .fetchone()
    )


def fetch_tag_by_id(conn: Any, tag_id: int) -> Any:
    """Fetch tag by ID."""
    return (
        conn.execute(
            select(
                tags_table.c.id,
                tags_table.c.name,
                tags_table.c.builtin_key,
                tags_table.c.description,
                tags_table.c.instruction,
                tags_table.c.color,
            ).where(tags_table.c.id == tag_id)
        )
        .mappings()
        .fetchone()
    )


def fetch_tag_usage(conn: Any, tag_id: int) -> dict[str, int]:
    """Fetch tag usage."""
    return {
        "transaction_count": conn.execute(
            select(func.count()).select_from(transaction_tags_table).where(transaction_tags_table.c.tag_id == tag_id)
        ).scalar_one(),
        "rule_count": conn.execute(
            select(func.count())
            .select_from(category_rule_tags_table)
            .where(category_rule_tags_table.c.tag_id == tag_id)
        ).scalar_one(),
    }


def fetch_category_usage(conn: Any, category_id: int, category_name: str) -> dict[str, int]:
    """Fetch category usage."""
    return {
        "transaction_count": conn.execute(
            select(func.count())
            .select_from(transactions_table)
            .where(category_assignment_condition(transactions_table, category_id, category_name))
        ).scalar_one(),
        "rule_count": conn.execute(
            select(func.count())
            .select_from(category_rules_table)
            .where(category_assignment_condition(category_rules_table, category_id, category_name))
        ).scalar_one(),
    }
