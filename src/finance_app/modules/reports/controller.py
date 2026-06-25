"""Flask routes for the Reports feature.

Routes render read-oriented report pages and downloads while delegating request
normalization, analytical queries, and presentation shaping to the Reports
service layer.
"""

from typing import Any

from flask import Blueprint, Response, abort, jsonify, render_template, request
from flask_login import current_user  # type: ignore[import-untyped]

from finance_app.core.i18n import gettext
from finance_app.modules.reports.definitions import (
    REPORT_ACCOUNTS,
    REPORT_INCOME,
    REPORT_MERCHANTS,
    REPORT_OVERVIEW,
    REPORT_TAXONOMY,
)
from finance_app.modules.reports.entities import REPORT_ENTITY_ACCOUNT, REPORT_ENTITY_MERCHANT
from finance_app.modules.reports.export import (
    XLSX_MIME_TYPE,
    report_export_filename,
    reports_overview_csv,
    reports_overview_xlsx,
)
from finance_app.modules.reports.pins import (
    pin_current_report,
    pinned_reports_overview_context,
    save_pinned_report_edits,
)
from finance_app.modules.reports.service import (
    build_reports_account_detail_context,
    build_reports_context,
    build_reports_income_context,
    build_reports_merchant_detail_context,
    build_reports_taxonomy_detail_context,
)
from finance_app.modules.reports.taxonomy import TAXONOMY_TARGET_CATEGORY, TAXONOMY_TARGET_TAG

reports_bp = Blueprint("reports", __name__)


@reports_bp.route("/reports")
def overview() -> str:
    """Render the Reports overview shell."""
    return _render_reports_section(REPORT_OVERVIEW)


@reports_bp.route("/reports/taxonomy")
def taxonomy() -> str:
    """Render the category and tag report index."""
    return _render_reports_section(REPORT_TAXONOMY)


@reports_bp.route("/reports/categories/<int:category_id>")
def category_report(category_id: int) -> str:
    """Render a read-only category report detail page."""
    return render_template(
        "reports.html",
        **_taxonomy_detail_context(TAXONOMY_TARGET_CATEGORY, category_id),
    )


@reports_bp.route("/reports/tags/<int:tag_id>")
def tag_report(tag_id: int) -> str:
    """Render a read-only tag report detail page."""
    return render_template(
        "reports.html",
        **_taxonomy_detail_context(TAXONOMY_TARGET_TAG, tag_id),
    )


@reports_bp.route("/reports/accounts")
def accounts() -> str:
    """Render the account report index."""
    return _render_reports_section(REPORT_ACCOUNTS)


@reports_bp.route("/reports/accounts/<int:account_id>")
def account_report(account_id: int) -> str:
    """Render a read-only account report detail page."""
    return render_template("reports.html", **_entity_detail_context(REPORT_ENTITY_ACCOUNT, account_id))


@reports_bp.route("/reports/merchants")
def merchants() -> str:
    """Render the merchant report index."""
    return _render_reports_section(REPORT_MERCHANTS)


@reports_bp.route("/reports/merchants/<int:merchant_id>")
def merchant_report(merchant_id: int) -> str:
    """Render a read-only merchant report detail page."""
    return render_template("reports.html", **_entity_detail_context(REPORT_ENTITY_MERCHANT, merchant_id))


@reports_bp.route("/reports/income")
def income() -> str:
    """Render the income and credits report."""
    return _render_reports_section(REPORT_INCOME)


@reports_bp.route("/reports/pins", methods=["POST"])
def pin_report() -> Response:
    """Pin the current report view for the authenticated user as JSON."""
    payload = request.get_json(silent=True) or {}
    try:
        result = pin_current_report(int(current_user.id), payload)
    except ValueError as exc:
        response = jsonify({"ok": False, "message": gettext(str(exc))})
        response.status_code = 400
        return response
    response = jsonify(result)
    response.status_code = 400 if not result.get("ok") else 200
    return response


@reports_bp.route("/reports/pins/edit", methods=["POST"])
def save_pinned_reports() -> Response:
    """Save pinned report order, title, and removal edits as JSON."""
    payload = request.get_json(silent=True) or {}
    try:
        result = save_pinned_report_edits(int(current_user.id), payload)
    except ValueError as exc:
        response = jsonify({"ok": False, "message": gettext(str(exc))})
        response.status_code = 400
        return response
    html = render_template("_reports_pins.html", **pinned_reports_overview_context())
    return jsonify({**result, "html": html})


@reports_bp.route("/reports/export.csv")
def export_csv() -> Response:
    """Download the filtered Reports overview as CSV."""
    context = build_reports_context(REPORT_OVERVIEW, request.args)
    output = reports_overview_csv(context["overview_export_rows"])
    return Response(
        output,
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={report_export_filename('csv')}"},
    )


@reports_bp.route("/reports/export.xlsx")
def export_xlsx() -> Response:
    """Download the filtered Reports overview as an Excel workbook."""
    context = build_reports_context(REPORT_OVERVIEW, request.args)
    output = reports_overview_xlsx(context["overview_export_rows"])
    return Response(
        output,
        mimetype=XLSX_MIME_TYPE,
        headers={"Content-Disposition": f"attachment; filename={report_export_filename('xlsx')}"},
    )


@reports_bp.route("/reports/categories/<int:category_id>/export.csv")
def category_export_csv(category_id: int) -> Response:
    """Download a filtered category report as CSV."""
    return _taxonomy_export_response(TAXONOMY_TARGET_CATEGORY, category_id, "csv")


@reports_bp.route("/reports/categories/<int:category_id>/export.xlsx")
def category_export_xlsx(category_id: int) -> Response:
    """Download a filtered category report as an Excel workbook."""
    return _taxonomy_export_response(TAXONOMY_TARGET_CATEGORY, category_id, "xlsx")


@reports_bp.route("/reports/tags/<int:tag_id>/export.csv")
def tag_export_csv(tag_id: int) -> Response:
    """Download a filtered tag report as CSV."""
    return _taxonomy_export_response(TAXONOMY_TARGET_TAG, tag_id, "csv")


@reports_bp.route("/reports/tags/<int:tag_id>/export.xlsx")
def tag_export_xlsx(tag_id: int) -> Response:
    """Download a filtered tag report as an Excel workbook."""
    return _taxonomy_export_response(TAXONOMY_TARGET_TAG, tag_id, "xlsx")


@reports_bp.route("/reports/accounts/<int:account_id>/export.csv")
def account_export_csv(account_id: int) -> Response:
    """Download a filtered account report as CSV."""
    return _entity_export_response(REPORT_ENTITY_ACCOUNT, account_id, "csv")


@reports_bp.route("/reports/accounts/<int:account_id>/export.xlsx")
def account_export_xlsx(account_id: int) -> Response:
    """Download a filtered account report as an Excel workbook."""
    return _entity_export_response(REPORT_ENTITY_ACCOUNT, account_id, "xlsx")


@reports_bp.route("/reports/merchants/<int:merchant_id>/export.csv")
def merchant_export_csv(merchant_id: int) -> Response:
    """Download a filtered merchant report as CSV."""
    return _entity_export_response(REPORT_ENTITY_MERCHANT, merchant_id, "csv")


@reports_bp.route("/reports/merchants/<int:merchant_id>/export.xlsx")
def merchant_export_xlsx(merchant_id: int) -> Response:
    """Download a filtered merchant report as an Excel workbook."""
    return _entity_export_response(REPORT_ENTITY_MERCHANT, merchant_id, "xlsx")


@reports_bp.route("/reports/income/export.csv")
def income_export_csv() -> Response:
    """Download a filtered income and credits report as CSV."""
    return _income_export_response("csv")


@reports_bp.route("/reports/income/export.xlsx")
def income_export_xlsx() -> Response:
    """Download a filtered income and credits report as an Excel workbook."""
    return _income_export_response("xlsx")


def _render_reports_section(section_key: str) -> str:
    """Render one Reports shell route using the shared context builder."""
    return render_template("reports.html", **build_reports_context(section_key, request.args))


def _taxonomy_detail_context(kind: str, target_id: int) -> dict[str, Any]:
    """Build a taxonomy detail context or abort with a 404."""
    try:
        return build_reports_taxonomy_detail_context(kind, target_id, request.args)
    except LookupError:
        abort(404)


def _entity_detail_context(kind: str, target_id: int) -> dict[str, Any]:
    """Build an account or merchant detail context or abort with a 404."""
    try:
        if kind == REPORT_ENTITY_ACCOUNT:
            return build_reports_account_detail_context(target_id, request.args)
        return build_reports_merchant_detail_context(target_id, request.args)
    except LookupError:
        abort(404)


def _taxonomy_export_response(kind: str, target_id: int, extension: str) -> Response:
    """Return a taxonomy detail export response."""
    context = _taxonomy_detail_context(kind, target_id)
    rows = context["taxonomy_export_rows"]
    target = context["taxonomy_target"]
    if extension == "csv":
        output = reports_overview_csv(rows)
        return Response(
            output,
            mimetype="text/csv",
            headers={
                "Content-Disposition": f"attachment; filename={report_export_filename('csv', target.export_stem)}"
            },
        )

    workbook = reports_overview_xlsx(rows)
    return Response(
        workbook,
        mimetype=XLSX_MIME_TYPE,
        headers={"Content-Disposition": f"attachment; filename={report_export_filename('xlsx', target.export_stem)}"},
    )


def _entity_export_response(kind: str, target_id: int, extension: str) -> Response:
    """Return an account or merchant detail export response."""
    context = _entity_detail_context(kind, target_id)
    rows = context["entity_export_rows"]
    target = context["entity_target"]
    if extension == "csv":
        output = reports_overview_csv(rows)
        return Response(
            output,
            mimetype="text/csv",
            headers={
                "Content-Disposition": f"attachment; filename={report_export_filename('csv', target.export_stem)}"
            },
        )

    workbook = reports_overview_xlsx(rows)
    return Response(
        workbook,
        mimetype=XLSX_MIME_TYPE,
        headers={"Content-Disposition": f"attachment; filename={report_export_filename('xlsx', target.export_stem)}"},
    )


def _income_export_response(extension: str) -> Response:
    """Return an income and credits export response."""
    context = build_reports_income_context(request.args)
    rows = context["income_export_rows"]
    stem = "reports-income-and-credits"
    if extension == "csv":
        output = reports_overview_csv(rows)
        return Response(
            output,
            mimetype="text/csv",
            headers={"Content-Disposition": f"attachment; filename={report_export_filename('csv', stem)}"},
        )

    workbook = reports_overview_xlsx(rows)
    return Response(
        workbook,
        mimetype=XLSX_MIME_TYPE,
        headers={"Content-Disposition": f"attachment; filename={report_export_filename('xlsx', stem)}"},
    )
