"""Static integrity checks for translation catalogs."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

FRENCH_CATALOG = Path("src/finance_app/translations/fr.json")


def test_french_catalog_does_not_have_duplicate_keys() -> None:
    """Verify duplicate JSON keys cannot silently shadow earlier translations."""
    key_counts = Counter(key for key, _ in french_catalog_pairs())
    duplicates = {key: count for key, count in sorted(key_counts.items()) if count > 1}

    assert duplicates == {}


def test_french_catalog_is_ascii_only() -> None:
    """Verify the French catalog remains JSON-escaped for reviewable diffs."""
    non_ascii = sorted({character for character in FRENCH_CATALOG.read_text(encoding="utf-8") if ord(character) > 127})

    assert non_ascii == []


def french_catalog_pairs() -> list[tuple[str, str]]:
    """Load the French catalog as ordered pairs so duplicate keys are visible."""
    return json.loads(FRENCH_CATALOG.read_text(encoding="utf-8"), object_pairs_hook=list)
