"""URL builders for the comparison feature."""

from urllib.parse import urlencode

from flask import url_for


def build_comparison_url(**params):
    """Build comparison URL."""
    cleaned = {}
    for key, value in params.items():
        if isinstance(value, (list, tuple)):
            values = [item for item in value if item not in (None, "")]
            if values:
                cleaned[key] = values
        elif value not in (None, ""):
            cleaned[key] = value

    query = urlencode(cleaned, doseq=True)
    return url_for("comparison.comparison") + (f"?{query}" if query else "")


