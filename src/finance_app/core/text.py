"""Text normalization helpers for imports, matching, and search keys."""

import re
import unicodedata


def strip_accents(value: object) -> str:
    """Return an ASCII representation with combining accent marks removed."""
    return unicodedata.normalize("NFKD", str(value)).encode("ascii", "ignore").decode("ascii")


def normalize_header(value: object) -> str:
    """Return a compact lowercase key for comparing imported column headers."""
    return re.sub(r"[^a-z0-9]+", "", strip_accents(value).lower())
