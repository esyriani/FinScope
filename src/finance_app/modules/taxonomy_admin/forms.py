"""Form parsing and validation helpers for the taxonomy admin feature."""

from collections.abc import Mapping
from typing import Any

from finance_app.core.taxonomy import clean_color, clean_label, tag_color_for_name


def parse_category_form(form: Mapping[str, Any]) -> dict[str, object]:
    """Parse category form."""
    name = clean_label(form.get("name") or form.get("category"))
    if not name:
        raise ValueError("Category name is required.")

    return {
        "id": parse_optional_int(form.get("category_id")),
        "name": name,
        "description": str(form.get("description") or "").strip(),
        "instruction": str(form.get("instruction") or "").strip(),
    }


def parse_tag_form(form: Mapping[str, Any]) -> dict[str, object]:
    """Parse tag form."""
    name = clean_label(form.get("name") or form.get("tag"))
    if not name:
        raise ValueError("Tag name is required.")

    color = clean_color(form.get("color")) or tag_color_for_name(name)
    return {
        "id": parse_optional_int(form.get("tag_id")),
        "name": name,
        "description": str(form.get("description") or "").strip(),
        "instruction": str(form.get("instruction") or "").strip(),
        "color": color,
    }


def parse_required_int(value: object, label: str) -> int:
    """Parse required int."""
    parsed = parse_optional_int(value)
    if parsed is None:
        raise ValueError(f"{label} was not found.")
    return parsed


def parse_optional_int(value: object) -> int | None:
    """Parse optional int."""
    try:
        parsed = int(str(value or "").strip())
    except ValueError:
        return None
    return parsed if parsed > 0 else None
