"""Shared taxonomy metadata utilities.

Provides pure category/tag label cleanup, tag color normalization, and taxonomy
seed-file loading for database seeders and feature modules without coupling
them to category administration code.
"""

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from finance_app.core.constants import BASE_DIR

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
    """Load category and tag seed rows from the FinScope taxonomy YAML file."""
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


def unquote_yaml_scalar(value: str) -> str:
    """Remove one surrounding YAML quote pair from a simple scalar."""
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def clean_taxonomy_item(item: Mapping[str, Any]) -> dict[str, str]:
    """Normalize a taxonomy seed item into the database seed shape."""
    return {
        "name": clean_label(item.get("name")),
        "description": str(item.get("description") or "").strip(),
        "instruction": str(item.get("instruction") or "").strip(),
        "color": clean_color(item.get("color")),
    }


def clean_label(value: object) -> str:
    """Return a display label with repeated whitespace collapsed."""
    return " ".join(str(value or "").strip().split())


def clean_color(value: object) -> str:
    """Return a lowercase hex color, or an empty string when invalid."""
    color = str(value or "").strip()
    hex_digits = "0123456789abcdefABCDEF"
    if len(color) == 7 and color.startswith("#") and all(char in hex_digits for char in color[1:]):
        return color.lower()
    return ""


def tag_color_for_name(name: object) -> str:
    """Return the deterministic default color for a tag label."""
    tag = clean_label(name)
    if not tag:
        return DEFAULT_TAG_COLOR
    if tag in TAG_COLORS:
        return TAG_COLORS[tag]

    checksum = sum((index + 1) * ord(char) for index, char in enumerate(tag.casefold()))
    return TAG_COLOR_PALETTE[checksum % len(TAG_COLOR_PALETTE)]
