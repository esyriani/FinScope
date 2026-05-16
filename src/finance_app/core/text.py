"""Text normalization helpers."""

import re
import unicodedata


def strip_accents(value):
    """Handle strip accents."""
    return (
        unicodedata.normalize("NFKD", str(value))
        .encode("ascii", "ignore")
        .decode("ascii")
    )


def normalize_header(value):
    """Normalize header."""
    return re.sub(r"[^a-z0-9]+", "", strip_accents(value).lower())
