"""Application orchestration for the Reports feature.

This foundation layer exposes the Reports navigation and request state without
performing analytical queries yet. Later increments will add report definition
execution and presenter-shaped data behind this service boundary.
"""

from typing import Any

from finance_app.modules.reports.definitions import REPORT_SECTIONS, get_report_section
from finance_app.modules.reports.filters import parse_report_request


def build_reports_context(section_key: str, args: Any) -> dict[str, Any]:
    """Build the template context for a Reports shell route."""
    active_section = get_report_section(section_key)
    report_request = parse_report_request(section_key, args)
    return {
        "page_title": "Reports",
        "active_report_section": active_section,
        "report_request": report_request,
        "report_sections": REPORT_SECTIONS,
    }
