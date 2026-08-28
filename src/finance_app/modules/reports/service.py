"""Application orchestration for the Reports feature.

The service parses report request state, coordinates read-side query helpers,
and returns presenter-shaped contexts for report pages and downloads.
"""

from typing import Any

from flask import url_for

from finance_app.core.analytics import (
    QUICK_VIEW_CATEGORIZED,
    REPORT_BASIS_OPTIONS,
    REPORT_MEASURE_OPTIONS,
    build_quick_view_options,
)
from finance_app.core.periods import DATE_PERIOD_OPTIONS, PERIOD_CUSTOM, format_date_label, get_period_label
from finance_app.modules.reports.definitions import (
    REPORT_ACCOUNTS,
    REPORT_INCOME,
    REPORT_MERCHANTS,
    REPORT_OVERVIEW,
    REPORT_SECTIONS,
    REPORT_TAXONOMY,
    get_report_section,
)
from finance_app.modules.reports.entities import (
    REPORT_ENTITY_ACCOUNT,
    REPORT_ENTITY_MERCHANT,
)
from finance_app.modules.reports.export_presenter import build_export_rows
from finance_app.modules.reports.filters import (
    ReportRequest,
    parse_report_request,
    report_taxonomy_filter_controls_available,
)
from finance_app.modules.reports.pins import (
    REPORT_TYPE_ACCOUNT,
    REPORT_TYPE_MERCHANT,
    current_report_pin_context,
    pinned_reports_overview_context,
)
from finance_app.modules.reports.presenter import (
    build_reports_entity_detail_view,
    build_reports_entity_index_view,
    build_reports_income_view,
    build_reports_overview_view,
    build_reports_taxonomy_index_view,
)
from finance_app.modules.reports.query_data import (
    fetch_reports_entity_detail_query_data,
    fetch_reports_entity_index_query_data,
    fetch_reports_income_query_data,
    fetch_reports_overview_query_data,
    fetch_reports_taxonomy_detail_query_data,
    fetch_reports_taxonomy_index_query_data,
)
from finance_app.modules.reports.taxonomy import (
    TAXONOMY_TARGET_CATEGORY,
)
from finance_app.modules.reports.taxonomy_detail_presenter import build_reports_taxonomy_detail_view
from finance_app.modules.reports.urls import reports_url


def build_reports_context(section_key: str, args: Any) -> dict[str, Any]:
    """Build the template context for a Reports shell route."""
    active_section = get_report_section(section_key)
    report_request = parse_report_request(section_key, args)
    context: dict[str, Any] = {
        "page_title": "Reports",
        "active_report_section": active_section,
        "report_request": report_request,
        "report_sections": REPORT_SECTIONS,
    }
    if section_key == REPORT_OVERVIEW:
        context.update(build_reports_overview_context(args, report_request))
    elif section_key == REPORT_TAXONOMY:
        context.update(build_reports_taxonomy_index_context(args, report_request))
    elif section_key in (REPORT_ACCOUNTS, REPORT_MERCHANTS):
        context.update(build_reports_entity_index_context(section_key, args, report_request))
    elif section_key == REPORT_INCOME:
        context.update(build_reports_income_context(args, report_request))
    return context


def build_reports_overview_context(args: Any, report_request: ReportRequest) -> dict[str, Any]:
    """Build Reports overview filters, rows, charts, and export URLs."""
    query_data = fetch_reports_overview_query_data(report_request)
    report_request = query_data.report_request
    overview = build_reports_overview_view(
        report_request,
        query_data.summary,
        query_data.monthly_rows,
        query_data.category_rows,
        query_data.tag_rows,
        query_data.account_rows,
        query_data.merchant_rows,
    )
    return {
        "report_request": report_request,
        **overview,
        **reports_filter_context(report_request.args, report_request, query_data),
        **pinned_reports_overview_context(),
        **current_report_pin_context(REPORT_OVERVIEW, report_request),
        "overview_export_rows": build_export_rows(overview),
    }


def build_reports_taxonomy_index_context(args: Any, report_request: ReportRequest) -> dict[str, Any]:
    """Build taxonomy index filters and category/tag report targets."""
    query_data = fetch_reports_taxonomy_index_query_data(report_request)
    report_request = query_data.report_request
    taxonomy = build_reports_taxonomy_index_view(
        report_request,
        query_data.summary,
        query_data.category_rows,
        query_data.tag_rows,
        query_data.target_options,
    )
    return {
        "report_request": report_request,
        **taxonomy,
        "taxonomy_explorer_filter": str(report_request.args.get("taxonomy_filter", "all") or "all"),
        "taxonomy_explorer_search": str(report_request.args.get("taxonomy_search", "") or ""),
        **reports_filter_context(
            report_request.args,
            report_request,
            query_data,
            clear_endpoint="reports.taxonomy",
            export_csv_endpoint=None,
            export_xlsx_endpoint=None,
        ),
    }


def build_reports_taxonomy_detail_context(kind: str, target_id: int, args: Any) -> dict[str, Any]:
    """Build a category or tag report detail context."""
    report_request = parse_report_request(REPORT_TAXONOMY, args)
    query_data = fetch_reports_taxonomy_detail_query_data(kind, target_id, report_request)
    report_request = query_data.report_request
    detail = build_reports_taxonomy_detail_view(
        report_request,
        query_data.target,
        query_data.summary,
        query_data.monthly_rows,
        query_data.composition_rows,
        query_data.account_rows,
        query_data.merchant_rows,
        query_data.evidence_rows,
        query_data.semantic_summary,
        query_data.target_options,
    )
    export_csv_endpoint = (
        "reports.category_export_csv" if kind == TAXONOMY_TARGET_CATEGORY else "reports.tag_export_csv"
    )
    export_xlsx_endpoint = (
        "reports.category_export_xlsx" if kind == TAXONOMY_TARGET_CATEGORY else "reports.tag_export_xlsx"
    )
    route_values: dict[str, Any] = (
        {"category_id": query_data.target.id} if kind == TAXONOMY_TARGET_CATEGORY else {"tag_id": query_data.target.id}
    )
    return {
        "page_title": f"Reports - {query_data.target.name}",
        "active_report_section": get_report_section(REPORT_TAXONOMY),
        "report_request": report_request,
        "report_sections": REPORT_SECTIONS,
        **detail,
        **reports_filter_context(
            report_request.args,
            report_request,
            query_data,
            clear_endpoint="reports.category_report" if kind == TAXONOMY_TARGET_CATEGORY else "reports.tag_report",
            clear_route_values=route_values,
            export_csv_endpoint=export_csv_endpoint,
            export_xlsx_endpoint=export_xlsx_endpoint,
            export_route_values=route_values,
        ),
        **current_report_pin_context(
            REPORT_TAXONOMY,
            report_request,
            target_kind=kind,
            target_category_id=query_data.target.id if kind == TAXONOMY_TARGET_CATEGORY else None,
            target_tag_id=query_data.target.id if kind != TAXONOMY_TARGET_CATEGORY else None,
        ),
    }


def build_reports_entity_index_context(section_key: str, args: Any, report_request: ReportRequest) -> dict[str, Any]:
    """Build account or merchant index filters and target rows."""
    kind = REPORT_ENTITY_ACCOUNT if section_key == REPORT_ACCOUNTS else REPORT_ENTITY_MERCHANT
    query_data = fetch_reports_entity_index_query_data(kind, report_request)
    report_request = query_data.report_request
    entity = build_reports_entity_index_view(
        report_request,
        query_data.summary,
        query_data.rows,
        kind,
        query_data.target_options,
    )
    return {
        "report_request": report_request,
        **entity,
        "entity_explorer_filter": str(report_request.args.get("entity_filter", "all") or "all"),
        "entity_explorer_search": str(report_request.args.get("entity_search", "") or ""),
        **reports_filter_context(
            report_request.args,
            report_request,
            query_data,
            clear_endpoint="reports.accounts" if kind == REPORT_ENTITY_ACCOUNT else "reports.merchants",
            export_csv_endpoint=None,
            export_xlsx_endpoint=None,
        ),
    }


def build_reports_account_detail_context(account_id: int, args: Any) -> dict[str, Any]:
    """Build an account report detail context."""
    return build_reports_entity_detail_context(REPORT_ENTITY_ACCOUNT, account_id, args)


def build_reports_merchant_detail_context(merchant_id: int, args: Any) -> dict[str, Any]:
    """Build a merchant report detail context."""
    return build_reports_entity_detail_context(REPORT_ENTITY_MERCHANT, merchant_id, args)


def build_reports_entity_detail_context(kind: str, target_id: int, args: Any) -> dict[str, Any]:
    """Build an account or merchant report detail context."""
    section_key = REPORT_ACCOUNTS if kind == REPORT_ENTITY_ACCOUNT else REPORT_MERCHANTS
    report_request = parse_report_request(section_key, args)
    query_data = fetch_reports_entity_detail_query_data(kind, target_id, report_request)
    report_request = query_data.report_request
    detail = build_reports_entity_detail_view(
        report_request,
        query_data.target,
        query_data.summary,
        query_data.monthly_rows,
        query_data.category_rows,
        query_data.tag_rows,
        query_data.account_rows,
        query_data.merchant_rows,
        query_data.evidence_rows,
        query_data.target_options,
    )
    export_csv_endpoint = (
        "reports.account_export_csv" if kind == REPORT_ENTITY_ACCOUNT else "reports.merchant_export_csv"
    )
    export_xlsx_endpoint = (
        "reports.account_export_xlsx" if kind == REPORT_ENTITY_ACCOUNT else "reports.merchant_export_xlsx"
    )
    route_values: dict[str, Any] = (
        {"account_id": query_data.target.id} if kind == REPORT_ENTITY_ACCOUNT else {"merchant_id": query_data.target.id}
    )
    filter_context = reports_filter_context(
        report_request.args,
        report_request,
        query_data,
        clear_endpoint="reports.account_report" if kind == REPORT_ENTITY_ACCOUNT else "reports.merchant_report",
        clear_route_values=route_values,
        export_csv_endpoint=export_csv_endpoint,
        export_xlsx_endpoint=export_xlsx_endpoint,
        export_route_values=route_values,
    )
    if kind == REPORT_ENTITY_ACCOUNT:
        filter_context["selected_account_id"] = query_data.target.id
        filter_context["reports_export_csv_url"] = reports_url(
            report_request.args,
            endpoint=export_csv_endpoint,
            route_values=route_values,
            account_id=None,
        )
        filter_context["reports_export_xlsx_url"] = reports_url(
            report_request.args,
            endpoint=export_xlsx_endpoint,
            route_values=route_values,
            account_id=None,
        )
    else:
        filter_context["selected_merchant_id"] = query_data.target.id
        filter_context["selected_merchant_label"] = query_data.target.name
        filter_context["merchant_query"] = query_data.target.name
        filter_context["reports_export_csv_url"] = reports_url(
            report_request.args,
            endpoint=export_csv_endpoint,
            route_values=route_values,
            merchant_id=None,
            merchant_query=None,
            merchant_search=None,
        )
        filter_context["reports_export_xlsx_url"] = reports_url(
            report_request.args,
            endpoint=export_xlsx_endpoint,
            route_values=route_values,
            merchant_id=None,
            merchant_query=None,
            merchant_search=None,
        )

    return {
        "page_title": f"Reports - {query_data.target.name}",
        "active_report_section": get_report_section(section_key),
        "report_request": report_request,
        "report_sections": REPORT_SECTIONS,
        **detail,
        **filter_context,
        **current_report_pin_context(
            REPORT_TYPE_ACCOUNT if kind == REPORT_ENTITY_ACCOUNT else REPORT_TYPE_MERCHANT,
            report_request,
            target_kind=kind,
            target_account_id=query_data.target.id if kind == REPORT_ENTITY_ACCOUNT else None,
            target_merchant_id=query_data.target.id if kind == REPORT_ENTITY_MERCHANT else None,
        ),
    }


def build_reports_income_context(args: Any, report_request: ReportRequest | None = None) -> dict[str, Any]:
    """Build income and credits report filters, rows, charts, and export URLs."""
    report_request = report_request or parse_report_request(REPORT_INCOME, args)
    query_data = fetch_reports_income_query_data(report_request)
    report_request = query_data.report_request
    income = build_reports_income_view(
        report_request,
        query_data.summary,
        query_data.monthly_rows,
        query_data.category_rows,
        query_data.tag_rows,
        query_data.account_rows,
        query_data.merchant_rows,
        query_data.evidence_rows,
    )
    return {
        "report_request": report_request,
        **income,
        **reports_filter_context(
            report_request.args,
            report_request,
            query_data,
            clear_endpoint="reports.income",
            export_csv_endpoint="reports.income_export_csv",
            export_xlsx_endpoint="reports.income_export_xlsx",
        ),
        **current_report_pin_context(REPORT_INCOME, report_request),
    }


def reports_filter_context(
    args: Any,
    report_request: ReportRequest,
    query_data: Any,
    *,
    clear_endpoint: str = "reports.overview",
    clear_route_values: dict[str, Any] | None = None,
    export_csv_endpoint: str | None = "reports.export_csv",
    export_xlsx_endpoint: str | None = "reports.export_xlsx",
    export_route_values: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return filter controls and labels for the Reports overview."""
    taxonomy_filter_controls_available = report_taxonomy_filter_controls_available(report_request.section_key)
    return {
        "selected_period": report_request.period,
        "period_options": DATE_PERIOD_OPTIONS,
        "period_custom": PERIOD_CUSTOM,
        "period_label": get_period_label(report_request.period, report_request.date_from, report_request.date_to),
        "selected_date_from": report_request.date_from,
        "selected_date_to": report_request.date_to,
        "selected_date_from_label": format_date_label(report_request.date_from),
        "selected_date_to_label": format_date_label(report_request.date_to),
        "measure_options": REPORT_MEASURE_OPTIONS,
        "selected_measure": report_request.measure,
        "basis_options": REPORT_BASIS_OPTIONS,
        "selected_basis": report_request.basis,
        "quick_view": report_request.quick_view,
        "quick_view_options": build_quick_view_options(
            report_request.quick_view,
            query_data.quick_view_counts,
        ),
        "reports_scope_label": "Scope" if taxonomy_filter_controls_available else "Quick view",
        "categorized_quick_view_value": QUICK_VIEW_CATEGORIZED,
        "reports_show_account_filter": report_request.section_key != REPORT_ACCOUNTS,
        "reports_show_merchant_filter": report_request.section_key != REPORT_MERCHANTS,
        "reports_taxonomy_filter_controls_available": taxonomy_filter_controls_available,
        "reports_taxonomy_filter_controls_visible": report_request.quick_view == QUICK_VIEW_CATEGORIZED,
        "selected_categories": report_request.selected_categories,
        "selected_tags": report_request.selected_tags,
        "category_options": getattr(query_data, "category_options", []),
        "tag_options": getattr(query_data, "tag_options", []),
        "account_options": query_data.account_options,
        "selected_account_id": report_request.selected_account_id,
        "selected_merchant_id": report_request.selected_merchant_id,
        "selected_merchant_label": query_data.selected_merchant_label,
        "merchant_query": report_request.merchant_query,
        "merchant_suggestion_limit": query_data.merchant_suggestion_limit,
        "reports_clear_url": url_for(clear_endpoint, **(clear_route_values or {})),
        "reports_export_csv_url": (
            reports_url(args, endpoint=export_csv_endpoint, route_values=export_route_values)
            if export_csv_endpoint
            else ""
        ),
        "reports_export_xlsx_url": (
            reports_url(args, endpoint=export_xlsx_endpoint, route_values=export_route_values)
            if export_xlsx_endpoint
            else ""
        ),
    }
