"""URL builders for the Reports feature."""

from typing import Any

from finance_app.core.urls import QueryStringArgs, build_app_url, build_query_url


def build_reports_url(endpoint: str = "reports.overview", **params: object) -> str:
    """Build a Reports URL with blank query values removed."""
    return build_app_url(endpoint, **params)


def reports_url(
    args: QueryStringArgs,
    endpoint: str = "reports.overview",
    route_values: dict[str, Any] | None = None,
    **overrides: object,
) -> str:
    """Build a Reports URL that preserves current query parameters."""
    return build_query_url(args, endpoint, route_values, **overrides)
