"""Shared Flask URL helpers for cleaned and preserved query strings.

Feature modules use this boundary when URL behavior is not owned by one feature,
for example when one page links to another feature while preserving filters.
"""

from collections.abc import Mapping
from typing import Any, Protocol
from urllib.parse import urlencode

from flask import url_for


class QueryStringArgs(Protocol):
    """Represent query args that can be copied for URL updates."""

    def to_dict(self, flat: bool = True) -> dict[str, object]:
        """Return query parameters as a dictionary."""
        ...


def clean_query(params: Mapping[str, object]) -> dict[str, object]:
    """Return query parameters with blank values removed."""
    cleaned: dict[str, object] = {}
    for key, value in params.items():
        if isinstance(value, (list, tuple)):
            values = [item for item in value if item not in (None, "")]
            if values:
                cleaned[key] = values
        elif value not in (None, ""):
            cleaned[key] = value
    return cleaned


def build_app_url(endpoint: str, **params: object) -> str:
    """Build an application URL with blank query values removed."""
    query = urlencode(clean_query(params), doseq=True)
    return url_for(endpoint) + (f"?{query}" if query else "")


def build_query_url(
    args: QueryStringArgs,
    endpoint: str,
    route_values: Mapping[str, Any] | None = None,
    **overrides: object,
) -> str:
    """Build an application URL that preserves current query parameters."""
    query = args.to_dict(flat=False)
    for key, value in overrides.items():
        if value in (None, ""):
            query.pop(key, None)
        elif isinstance(value, (list, tuple)):
            query[key] = [str(item) for item in value if item not in (None, "")]
        else:
            query[key] = [str(value)]

    encoded_query = urlencode(clean_query(query), doseq=True)
    route_params = dict(route_values or {})
    return url_for(endpoint, **route_params) + (f"?{encoded_query}" if encoded_query else "")
