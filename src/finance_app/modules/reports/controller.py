"""Flask routes for the Reports feature.

The routes render a read-oriented Reports shell and section navigation. They do
not perform persistence or analytical SQL in this increment; later increments
will attach report execution behind the service layer.
"""

from flask import Blueprint, render_template, request

from finance_app.modules.reports.definitions import (
    REPORT_ACCOUNTS,
    REPORT_INCOME,
    REPORT_MERCHANTS,
    REPORT_OVERVIEW,
    REPORT_TAXONOMY,
)
from finance_app.modules.reports.service import build_reports_context

reports_bp = Blueprint("reports", __name__)


@reports_bp.route("/reports")
def overview() -> str:
    """Render the Reports overview shell."""
    return _render_reports_section(REPORT_OVERVIEW)


@reports_bp.route("/reports/taxonomy")
def taxonomy() -> str:
    """Render the category and tag Reports shell."""
    return _render_reports_section(REPORT_TAXONOMY)


@reports_bp.route("/reports/accounts")
def accounts() -> str:
    """Render the account Reports shell."""
    return _render_reports_section(REPORT_ACCOUNTS)


@reports_bp.route("/reports/merchants")
def merchants() -> str:
    """Render the merchant Reports shell."""
    return _render_reports_section(REPORT_MERCHANTS)


@reports_bp.route("/reports/income")
def income() -> str:
    """Render the income and credits Reports shell."""
    return _render_reports_section(REPORT_INCOME)


def _render_reports_section(section_key: str) -> str:
    """Render one Reports shell route using the shared context builder."""
    return render_template("reports.html", **build_reports_context(section_key, request.args))
