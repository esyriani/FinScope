"""Application orchestration for the Reports feature.

The service parses report request state, coordinates read-side query helpers,
and returns presenter-shaped contexts for report pages and downloads.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

from flask import url_for

from finance_app.core.constants import UNKNOWN_CATEGORY
from finance_app.core.periods import DATE_PERIOD_OPTIONS, PERIOD_CUSTOM, format_date_label, get_period_label
from finance_app.database.engine import db_core_transaction
from finance_app.modules.accounts.queries import list_account_options
from finance_app.modules.categories.service import get_category_options
from finance_app.modules.categories.taxonomy import get_tag_option_rows
from finance_app.modules.dashboard.constants import QUICK_VIEW_ALL, QUICK_VIEW_CATEGORIZED
from finance_app.modules.dashboard.presenter import build_quick_view_options
from finance_app.modules.merchants.repository import find_merchant_by_id
from finance_app.modules.merchants.service import get_merchant_suggestion_limit
from finance_app.modules.reports.constants import REPORT_BASIS_OPTIONS, REPORT_MEASURE_OPTIONS
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
    ReportEntityTarget,
    resolve_account_report_target,
    resolve_merchant_report_target,
)
from finance_app.modules.reports.filters import (
    ReportRequest,
    parse_report_request,
    report_taxonomy_filter_controls_available,
    report_taxonomy_filters_active,
)
from finance_app.modules.reports.pins import (
    REPORT_TYPE_ACCOUNT,
    REPORT_TYPE_MERCHANT,
    current_report_pin_context,
    pinned_reports_overview_context,
)
from finance_app.modules.reports.presenter import (
    build_export_rows,
    build_reports_entity_detail_view,
    build_reports_entity_index_view,
    build_reports_income_view,
    build_reports_overview_view,
    build_reports_taxonomy_detail_view,
    build_reports_taxonomy_index_view,
)
from finance_app.modules.reports.queries import (
    account_target_condition,
    fetch_account_breakdown,
    fetch_category_breakdown,
    fetch_entity_target_options,
    fetch_merchant_breakdown,
    fetch_monthly_overview,
    fetch_reimbursable_tag_summary,
    fetch_reimbursement_category_summary,
    fetch_report_quick_view_counts,
    fetch_report_summary,
    fetch_tag_breakdown,
    fetch_taxonomy_category_rows,
    fetch_taxonomy_evidence_rows,
    fetch_taxonomy_tag_rows,
    fetch_taxonomy_target_options,
    income_credit_target_condition,
    merchant_target_condition,
    report_quick_view_conditions,
    report_taxonomy_filter_conditions,
    reports_base_filters,
    taxonomy_target_condition,
)
from finance_app.modules.reports.taxonomy import (
    TAXONOMY_TARGET_CATEGORY,
    TaxonomyReportTarget,
    resolve_taxonomy_report_target,
)
from finance_app.modules.reports.urls import reports_url
from finance_app.modules.settings.runtime import get_unknown_category


@dataclass(frozen=True)
class ReportsOverviewQueryData:
    """Database-backed rows for the Reports overview."""

    report_request: ReportRequest
    unknown_category: str
    account_options: list[dict[str, Any]]
    selected_merchant_label: str
    merchant_suggestion_limit: int
    quick_view_counts: dict[str, Any]
    summary: Any
    monthly_rows: list[dict[str, Any]]
    category_rows: list[Any]
    tag_rows: list[dict[str, Any]]
    account_rows: list[Any]
    merchant_rows: list[dict[str, Any]]


@dataclass(frozen=True)
class ReportsTaxonomyIndexQueryData:
    """Database-backed rows for the taxonomy report index."""

    report_request: ReportRequest
    unknown_category: str
    account_options: list[dict[str, Any]]
    selected_merchant_label: str
    merchant_suggestion_limit: int
    quick_view_counts: dict[str, Any]
    summary: Any
    category_rows: list[Any]
    tag_rows: list[Any]
    target_options: list[Any]


@dataclass(frozen=True)
class ReportsTaxonomyDetailQueryData:
    """Database-backed rows for a category or tag report detail page."""

    report_request: ReportRequest
    unknown_category: str
    account_options: list[dict[str, Any]]
    selected_merchant_label: str
    merchant_suggestion_limit: int
    quick_view_counts: dict[str, Any]
    target: TaxonomyReportTarget
    summary: Any
    monthly_rows: list[dict[str, Any]]
    composition_rows: list[Any]
    account_rows: list[Any]
    merchant_rows: list[dict[str, Any]]
    evidence_rows: list[dict[str, Any]]
    semantic_summary: Any
    target_options: list[Any]


@dataclass(frozen=True)
class ReportsEntityIndexQueryData:
    """Database-backed rows for account and merchant report indexes."""

    report_request: ReportRequest
    unknown_category: str
    account_options: list[dict[str, Any]]
    selected_merchant_label: str
    merchant_suggestion_limit: int
    quick_view_counts: dict[str, Any]
    summary: Any
    rows: list[Any]
    target_options: list[Any]
    category_options: list[str]
    tag_options: list[dict[str, str]]


@dataclass(frozen=True)
class ReportsEntityDetailQueryData:
    """Database-backed rows for an account or merchant report detail page."""

    report_request: ReportRequest
    unknown_category: str
    account_options: list[dict[str, Any]]
    selected_merchant_label: str
    merchant_suggestion_limit: int
    quick_view_counts: dict[str, Any]
    target: ReportEntityTarget
    summary: Any
    monthly_rows: list[dict[str, Any]]
    category_rows: list[Any]
    tag_rows: list[Any]
    account_rows: list[Any]
    merchant_rows: list[dict[str, Any]]
    evidence_rows: list[dict[str, Any]]
    target_options: list[Any]
    category_options: list[str]
    tag_options: list[dict[str, str]]


@dataclass(frozen=True)
class ReportsIncomeQueryData:
    """Database-backed rows for the income and credits report."""

    report_request: ReportRequest
    unknown_category: str
    account_options: list[dict[str, Any]]
    selected_merchant_label: str
    merchant_suggestion_limit: int
    quick_view_counts: dict[str, Any]
    summary: Any
    monthly_rows: list[dict[str, Any]]
    category_rows: list[Any]
    tag_rows: list[Any]
    account_rows: list[Any]
    merchant_rows: list[dict[str, Any]]
    evidence_rows: list[dict[str, Any]]
    category_options: list[str]
    tag_options: list[dict[str, str]]


class EffectiveReportArgs:
    """Query-args adapter used when Reports resolves an implicit scope."""

    def __init__(self, source: Any, overrides: Mapping[str, object]) -> None:
        raw_values = source.to_dict(flat=False)
        self._values: dict[str, list[object]] = {}
        for key, value in raw_values.items():
            if isinstance(value, (list, tuple)):
                self._values[str(key)] = list(value)
            else:
                self._values[str(key)] = [value]

        for key, value in overrides.items():
            if value in (None, ""):
                self._values.pop(key, None)
            elif isinstance(value, (list, tuple)):
                self._values[key] = [item for item in value if item not in (None, "")]
            else:
                self._values[key] = [value]

    def get(self, key: str, default: object | None = None) -> object:
        """Return one query value for a key."""
        values = self._values.get(key)
        return values[0] if values else default

    def getlist(self, key: str) -> list[object]:
        """Return all query values for a repeated key."""
        return list(self._values.get(key, []))

    def to_dict(self, flat: bool = True) -> dict[str, object]:
        """Return query parameters as a dictionary."""
        if flat:
            return {key: values[0] for key, values in self._values.items() if values}
        return {key: list(values) for key, values in self._values.items() if values}


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


def effective_report_request(report_request: ReportRequest, quick_view_counts: Mapping[str, Any]) -> ReportRequest:
    """Return a report request that avoids an empty implicit categorized view."""
    explicit_quick_view = str(report_request.args.get("quick_view", "") or "").strip()
    categorized_count = int(quick_view_counts.get("categorized_count") or 0)
    all_count = int(quick_view_counts.get("all_count") or 0)
    if (
        not explicit_quick_view
        and report_request.quick_view == QUICK_VIEW_CATEGORIZED
        and categorized_count == 0
        and all_count > 0
    ):
        return replace(
            report_request,
            quick_view=QUICK_VIEW_ALL,
            args=EffectiveReportArgs(report_request.args, {"quick_view": QUICK_VIEW_ALL}),
        )
    return report_request


def fetch_reports_overview_query_data(report_request: ReportRequest) -> ReportsOverviewQueryData:
    """Fetch Reports overview aggregates inside one database transaction."""
    with db_core_transaction() as conn:
        unknown_category = get_unknown_category(conn) or UNKNOWN_CATEGORY
        base_filters = reports_base_filters(report_request).criteria()
        quick_view_counts = fetch_report_quick_view_counts(
            conn,
            base_filters,
            unknown_category,
            report_request.basis,
        )
        report_request = effective_report_request(report_request, quick_view_counts)
        filters = report_filters_with_quick_view(base_filters, report_request, unknown_category)
        account_options = list_account_options(conn)
        selected_merchant_label = selected_merchant_option_name(
            conn,
            report_request.selected_merchant_id,
            report_request.merchant_query,
        )
        return ReportsOverviewQueryData(
            report_request=report_request,
            unknown_category=unknown_category,
            account_options=account_options,
            selected_merchant_label=selected_merchant_label,
            merchant_suggestion_limit=get_merchant_suggestion_limit(conn),
            quick_view_counts=quick_view_counts,
            summary=fetch_report_summary(conn, filters, unknown_category, report_request.basis),
            monthly_rows=fetch_monthly_overview(conn, filters, report_request.basis),
            category_rows=fetch_category_breakdown(conn, filters, unknown_category, report_request.basis),
            tag_rows=fetch_tag_breakdown(conn, filters, report_request.basis),
            account_rows=fetch_account_breakdown(conn, filters, report_request.basis),
            merchant_rows=fetch_merchant_breakdown(conn, filters, report_request.basis),
        )


def fetch_reports_income_query_data(report_request: ReportRequest) -> ReportsIncomeQueryData:
    """Fetch income and credits report aggregates inside one database transaction."""
    with db_core_transaction() as conn:
        unknown_category = get_unknown_category(conn) or UNKNOWN_CATEGORY
        base_filters = reports_base_filters(report_request).criteria()
        income_scope_filters = [*base_filters, income_credit_target_condition(report_request.basis)]
        quick_view_counts = fetch_report_quick_view_counts(
            conn,
            income_scope_filters,
            unknown_category,
            report_request.basis,
        )
        report_request = effective_report_request(report_request, quick_view_counts)
        income_filters = report_filters_with_quick_view(income_scope_filters, report_request, unknown_category)
        account_options = list_account_options(conn)
        selected_merchant_label = selected_merchant_option_name(
            conn,
            report_request.selected_merchant_id,
            report_request.merchant_query,
        )
        return ReportsIncomeQueryData(
            report_request=report_request,
            unknown_category=unknown_category,
            account_options=account_options,
            selected_merchant_label=selected_merchant_label,
            merchant_suggestion_limit=get_merchant_suggestion_limit(conn),
            quick_view_counts=quick_view_counts,
            summary=fetch_report_summary(conn, income_filters, unknown_category, report_request.basis),
            monthly_rows=fetch_monthly_overview(conn, income_filters, report_request.basis),
            category_rows=fetch_category_breakdown(conn, income_filters, unknown_category, report_request.basis),
            tag_rows=fetch_tag_breakdown(conn, income_filters, report_request.basis),
            account_rows=fetch_account_breakdown(conn, income_filters, report_request.basis),
            merchant_rows=fetch_merchant_breakdown(conn, income_filters, report_request.basis),
            evidence_rows=fetch_taxonomy_evidence_rows(conn, income_filters, unknown_category, report_request.basis),
            category_options=report_category_filter_options(conn, unknown_category),
            tag_options=get_tag_option_rows(conn),
        )


def fetch_reports_taxonomy_index_query_data(report_request: ReportRequest) -> ReportsTaxonomyIndexQueryData:
    """Fetch taxonomy index aggregates inside one database transaction."""
    with db_core_transaction() as conn:
        unknown_category = get_unknown_category(conn) or UNKNOWN_CATEGORY
        base_filters = reports_base_filters(report_request).criteria()
        quick_view_counts = fetch_report_quick_view_counts(
            conn,
            base_filters,
            unknown_category,
            report_request.basis,
        )
        report_request = effective_report_request(report_request, quick_view_counts)
        filters = report_filters_with_quick_view(base_filters, report_request, unknown_category)
        account_options = list_account_options(conn)
        selected_merchant_label = selected_merchant_option_name(
            conn,
            report_request.selected_merchant_id,
            report_request.merchant_query,
        )
        return ReportsTaxonomyIndexQueryData(
            report_request=report_request,
            unknown_category=unknown_category,
            account_options=account_options,
            selected_merchant_label=selected_merchant_label,
            merchant_suggestion_limit=get_merchant_suggestion_limit(conn),
            quick_view_counts=quick_view_counts,
            summary=fetch_report_summary(conn, filters, unknown_category, report_request.basis),
            category_rows=fetch_taxonomy_category_rows(conn, filters, unknown_category, report_request.basis),
            tag_rows=fetch_taxonomy_tag_rows(conn, filters, report_request.basis),
            target_options=fetch_taxonomy_target_options(conn),
        )


def fetch_reports_taxonomy_detail_query_data(
    kind: str,
    target_id: int,
    report_request: ReportRequest,
) -> ReportsTaxonomyDetailQueryData:
    """Fetch scoped aggregates for one category or tag report target."""
    with db_core_transaction() as conn:
        unknown_category = get_unknown_category(conn) or UNKNOWN_CATEGORY
        target = resolve_taxonomy_report_target(conn, kind, target_id)
        if target is None:
            raise LookupError("Taxonomy report target was not found.")

        base_filters = reports_base_filters(report_request).criteria()
        target_scope_filters = [*base_filters, taxonomy_target_condition(target, unknown_category)]
        quick_view_counts = fetch_report_quick_view_counts(
            conn,
            target_scope_filters,
            unknown_category,
            report_request.basis,
        )
        report_request = effective_report_request(report_request, quick_view_counts)
        target_filters = report_filters_with_quick_view(target_scope_filters, report_request, unknown_category)
        account_options = list_account_options(conn)
        selected_merchant_label = selected_merchant_option_name(
            conn,
            report_request.selected_merchant_id,
            report_request.merchant_query,
        )
        composition_rows = (
            fetch_tag_breakdown(conn, target_filters, report_request.basis)
            if target.kind == TAXONOMY_TARGET_CATEGORY
            else fetch_category_breakdown(conn, target_filters, unknown_category, report_request.basis)
        )
        semantic_summary = None
        if target.is_reimbursable_tag:
            semantic_summary = fetch_reimbursable_tag_summary(conn, target_filters)
        elif target.is_reimbursement_category:
            semantic_summary = fetch_reimbursement_category_summary(conn, target_filters)

        return ReportsTaxonomyDetailQueryData(
            report_request=report_request,
            unknown_category=unknown_category,
            account_options=account_options,
            selected_merchant_label=selected_merchant_label,
            merchant_suggestion_limit=get_merchant_suggestion_limit(conn),
            quick_view_counts=quick_view_counts,
            target=target,
            summary=fetch_report_summary(conn, target_filters, unknown_category, report_request.basis),
            monthly_rows=fetch_monthly_overview(conn, target_filters, report_request.basis),
            composition_rows=composition_rows,
            account_rows=fetch_account_breakdown(conn, target_filters, report_request.basis),
            merchant_rows=fetch_merchant_breakdown(conn, target_filters, report_request.basis),
            evidence_rows=fetch_taxonomy_evidence_rows(
                conn,
                target_filters,
                unknown_category,
                report_request.basis,
                limit=5,
            ),
            semantic_summary=semantic_summary,
            target_options=fetch_taxonomy_target_options(conn),
        )


def fetch_reports_entity_index_query_data(kind: str, report_request: ReportRequest) -> ReportsEntityIndexQueryData:
    """Fetch account or merchant index aggregates inside one database transaction."""
    with db_core_transaction() as conn:
        unknown_category = get_unknown_category(conn) or UNKNOWN_CATEGORY
        base_filters = reports_base_filters(
            report_request,
            include_account=kind != REPORT_ENTITY_ACCOUNT,
            include_merchant=kind != REPORT_ENTITY_MERCHANT,
        ).criteria()
        quick_view_counts = fetch_report_quick_view_counts(
            conn,
            base_filters,
            unknown_category,
            report_request.basis,
        )
        report_request = effective_report_request(report_request, quick_view_counts)
        filters = report_filters_with_quick_view(base_filters, report_request, unknown_category)
        account_options = list_account_options(conn)
        selected_merchant_label = selected_merchant_option_name(
            conn,
            report_request.selected_merchant_id,
            report_request.merchant_query,
        )
        rows = (
            fetch_account_breakdown(conn, filters, report_request.basis)
            if kind == REPORT_ENTITY_ACCOUNT
            else fetch_merchant_breakdown(conn, filters, report_request.basis)
        )
        return ReportsEntityIndexQueryData(
            report_request=report_request,
            unknown_category=unknown_category,
            account_options=account_options,
            selected_merchant_label=selected_merchant_label,
            merchant_suggestion_limit=get_merchant_suggestion_limit(conn),
            quick_view_counts=quick_view_counts,
            summary=fetch_report_summary(conn, filters, unknown_category, report_request.basis),
            rows=rows,
            target_options=fetch_entity_target_options(conn, kind),
            category_options=report_category_filter_options(conn, unknown_category),
            tag_options=get_tag_option_rows(conn),
        )


def fetch_reports_entity_detail_query_data(
    kind: str,
    target_id: int,
    report_request: ReportRequest,
) -> ReportsEntityDetailQueryData:
    """Fetch scoped aggregates for one account or merchant report target."""
    with db_core_transaction() as conn:
        unknown_category = get_unknown_category(conn) or UNKNOWN_CATEGORY
        target = (
            resolve_account_report_target(conn, target_id)
            if kind == REPORT_ENTITY_ACCOUNT
            else resolve_merchant_report_target(conn, target_id)
        )
        if target is None:
            raise LookupError("Report target was not found.")

        base_filters = reports_base_filters(
            report_request,
            include_account=kind != REPORT_ENTITY_ACCOUNT,
            include_merchant=kind != REPORT_ENTITY_MERCHANT,
        ).criteria()
        target_filter = (
            account_target_condition(target.id)
            if kind == REPORT_ENTITY_ACCOUNT
            else merchant_target_condition(target.id)
        )
        target_scope_filters = [*base_filters, target_filter]
        quick_view_counts = fetch_report_quick_view_counts(
            conn,
            target_scope_filters,
            unknown_category,
            report_request.basis,
        )
        report_request = effective_report_request(report_request, quick_view_counts)
        target_filters = report_filters_with_quick_view(target_scope_filters, report_request, unknown_category)
        account_options = list_account_options(conn)
        selected_merchant_label = selected_merchant_option_name(
            conn,
            report_request.selected_merchant_id,
            report_request.merchant_query,
        )

        return ReportsEntityDetailQueryData(
            report_request=report_request,
            unknown_category=unknown_category,
            account_options=account_options,
            selected_merchant_label=selected_merchant_label,
            merchant_suggestion_limit=get_merchant_suggestion_limit(conn),
            quick_view_counts=quick_view_counts,
            target=target,
            summary=fetch_report_summary(conn, target_filters, unknown_category, report_request.basis),
            monthly_rows=fetch_monthly_overview(conn, target_filters, report_request.basis),
            category_rows=fetch_category_breakdown(conn, target_filters, unknown_category, report_request.basis),
            tag_rows=fetch_tag_breakdown(conn, target_filters, report_request.basis),
            account_rows=fetch_account_breakdown(conn, target_filters, report_request.basis),
            merchant_rows=fetch_merchant_breakdown(conn, target_filters, report_request.basis),
            evidence_rows=fetch_taxonomy_evidence_rows(
                conn,
                target_filters,
                unknown_category,
                report_request.basis,
                limit=5,
            ),
            target_options=fetch_entity_target_options(conn, kind),
            category_options=report_category_filter_options(conn, unknown_category),
            tag_options=get_tag_option_rows(conn),
        )


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


def report_filters_with_quick_view(
    filters: Sequence[Any],
    report_request: ReportRequest,
    unknown_category: str,
) -> list[Any]:
    """Return report filters plus the selected quick-view status shortcut."""
    taxonomy_filters = (
        report_taxonomy_filter_conditions(report_request, unknown_category)
        if report_taxonomy_filters_active(report_request)
        else []
    )
    return [
        *filters,
        *report_quick_view_conditions(report_request.quick_view, unknown_category),
        *taxonomy_filters,
    ]


def report_category_filter_options(conn: Any, unknown_category: str) -> list[str]:
    """Return category filter choices for categorized report refiners."""
    return [category for category in get_category_options(conn) if category != unknown_category]


def selected_merchant_option_name(conn: Any, selected_merchant_id: int | None, merchant_query: str = "") -> str:
    """Return the selected merchant label for report filter summaries and forms."""
    if selected_merchant_id is None:
        return merchant_query
    merchant = find_merchant_by_id(conn, selected_merchant_id)
    if merchant is None:
        return merchant_query
    return str(merchant["merchant_key"])
