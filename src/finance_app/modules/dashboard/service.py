"""Application orchestration for the dashboard feature."""

from dataclasses import dataclass
from typing import Any

from finance_app.core.constants import FILTER_MODE_INCLUDE, UNKNOWN_CATEGORY
from finance_app.core.money import rounded_money_float
from finance_app.core.periods import (
    DATE_PERIOD_OPTIONS,
    PERIOD_CUSTOM,
    format_date_label,
    get_period_label,
    period_start_date,
)
from finance_app.core.query import CoreFilters
from finance_app.database.engine import db_core_transaction
from finance_app.database.tables import transactions as transactions_table
from finance_app.modules.accounts.filters import account_filter_condition
from finance_app.modules.accounts.queries import list_account_options
from finance_app.modules.categories.service import get_category_options
from finance_app.modules.categories.tag_filters import has_concrete_tag_filter
from finance_app.modules.categories.taxonomy import get_tag_option_rows
from finance_app.modules.merchants.repository import find_merchant_by_id
from finance_app.modules.merchants.service import get_merchant_suggestion_limit
from finance_app.modules.reports.constants import REPORT_MEASURE_INCOME
from finance_app.modules.reports.urls import build_reports_url
from finance_app.modules.settings.runtime import get_unknown_category

from .filters import (
    DashboardRequest,
    apply_dashboard_dimension_filters,
    apply_quick_view_core_filter,
    parse_dashboard_request,
)
from .presenter import (
    attach_data_quality_urls,
    build_cash_flow_summary,
    build_dashboard_insights,
    build_dashboard_links,
    build_data_quality,
    build_quick_view_options,
)
from .queries import fetch_quick_view_counts, fetch_summary
from .urls import app_url


@dataclass(frozen=True)
class DashboardQueryData:
    """Container for dashboard rows fetched inside one database transaction.

    The service builds SQL filters once, fetches all required aggregates, and
    passes this immutable bundle to pure context-preparation helpers.
    """

    include_transfer_credits: bool
    category_options: list[str]
    tag_options: list[dict[str, Any]]
    account_options: list[dict[str, Any]]
    selected_merchant_label: str
    merchant_suggestion_limit: int
    quick_view_counts: dict[str, Any]
    data_quality: dict[str, Any]
    summary: Any


@dataclass(frozen=True)
class PreparedDashboardData:
    """Container for dashboard view-model values derived from query data."""

    total_spending: float
    total_income: float
    cash_flow_summary: dict[str, Any]
    dashboard_links: dict[str, str]
    dashboard_insights: dict[str, Any]
    data_quality: dict[str, Any]


def build_dashboard_context(args: Any) -> dict[str, Any]:
    """Build the dashboard template context for request query arguments."""
    dashboard_request = parse_dashboard_request(args)
    query_data = fetch_dashboard_query_data(dashboard_request)
    prepared_data = prepare_dashboard_data(dashboard_request, query_data)
    return dashboard_context_payload(dashboard_request, query_data, prepared_data)


def fetch_dashboard_query_data(dashboard_request: DashboardRequest) -> DashboardQueryData:
    """Fetch all database-backed dashboard aggregates for one request.

    Args:
        dashboard_request: Normalized dashboard filter and table-control values.

    Returns:
        A ``DashboardQueryData`` bundle containing settings, taxonomy options,
        summary rows, and quality counters. The returned rows are detached from
        the transaction and safe for later presentation-only processing.
    """
    with db_core_transaction() as conn:
        unknown_category = get_unknown_category(conn) or UNKNOWN_CATEGORY
        scoped_filters = dashboard_base_filters(dashboard_request)
        apply_dashboard_dimension_filters(
            scoped_filters,
            dashboard_request.selected_categories,
            dashboard_request.selected_tags,
            dashboard_request.filter_mode,
            unknown_category,
            dashboard_request.selected_merchant_id,
            dashboard_request.merchant_query,
        )

        quick_view_counts = fetch_quick_view_counts(
            conn,
            scoped_filters.criteria(),
            unknown_category,
        )
        data_quality_summary = fetch_summary(
            conn,
            scoped_filters.criteria(),
            unknown_category,
        )
        filters = scoped_filters.clone()
        apply_quick_view_core_filter(
            filters,
            dashboard_request.quick_view,
            dashboard_request.selected_categories,
            dashboard_request.selected_tags,
            dashboard_request.filter_mode,
            unknown_category,
        )
        filter_criteria = filters.criteria()
        include_transfer_credits = (
            has_concrete_tag_filter(dashboard_request.selected_tags)
            and dashboard_request.filter_mode == FILTER_MODE_INCLUDE
        )

        category_options = get_category_options(conn)
        tag_options = get_tag_option_rows(conn)
        account_options = list_account_options(conn)
        merchant_suggestion_limit = get_merchant_suggestion_limit(conn)
        selected_merchant_label = selected_merchant_option_name(
            conn,
            dashboard_request.selected_merchant_id,
            dashboard_request.merchant_query,
        )
        summary = fetch_summary(
            conn,
            filter_criteria,
            unknown_category,
            include_transfer_credits=include_transfer_credits,
        )
        data_quality = build_data_quality(data_quality_summary)

    return DashboardQueryData(
        include_transfer_credits=include_transfer_credits,
        category_options=category_options,
        tag_options=tag_options,
        account_options=account_options,
        selected_merchant_label=selected_merchant_label,
        merchant_suggestion_limit=merchant_suggestion_limit,
        quick_view_counts=quick_view_counts,
        data_quality=data_quality,
        summary=summary,
    )


def dashboard_base_filters(dashboard_request: DashboardRequest) -> CoreFilters:
    """Return the date-scoped base filters shared by dashboard queries."""
    filters = CoreFilters()
    filters.add(transactions_table.c.ignored == 0)
    filters.add(account_filter_condition(dashboard_request.selected_account_id))
    start_date = period_start_date(dashboard_request.period)
    if start_date:
        filters.add(transactions_table.c.tx_date >= start_date)
    if dashboard_request.period == PERIOD_CUSTOM:
        if dashboard_request.date_from:
            filters.add(transactions_table.c.tx_date >= dashboard_request.date_from)
        if dashboard_request.date_to:
            filters.add(transactions_table.c.tx_date <= dashboard_request.date_to)
    return filters


def prepare_dashboard_data(
    dashboard_request: DashboardRequest,
    query_data: DashboardQueryData,
) -> PreparedDashboardData:
    """Prepare presentation rows and derived totals for the dashboard context."""
    period = dashboard_request.period
    filter_mode = dashboard_request.filter_mode
    selected_categories = dashboard_request.selected_categories
    selected_tags = dashboard_request.selected_tags
    date_from = dashboard_request.date_from
    date_to = dashboard_request.date_to
    quick_view = dashboard_request.quick_view
    merchant_search = dashboard_request.merchant_search

    total_spending = rounded_money_float(query_data.summary["total_spending"])
    total_income = rounded_money_float(query_data.summary["total_income"])
    cash_flow_summary = build_cash_flow_summary(total_income, total_spending)
    dashboard_links = build_dashboard_links(
        period,
        filter_mode,
        selected_categories,
        selected_tags,
        date_from,
        date_to,
        quick_view,
        include_transfer_credits=query_data.include_transfer_credits,
        merchant_search=merchant_search,
        account_id=dashboard_request.selected_account_id,
    )
    dashboard_insights = build_dashboard_insights(
        query_data.summary,
        total_spending,
        period,
        filter_mode,
        selected_categories,
        selected_tags,
        date_from,
        date_to,
        quick_view,
        merchant_search,
        dashboard_request.selected_account_id,
    )
    attach_data_quality_urls(
        query_data.data_quality,
        period,
        filter_mode,
        selected_categories,
        selected_tags,
        date_from,
        date_to,
        quick_view,
        merchant_search,
        dashboard_request.selected_account_id,
    )

    return PreparedDashboardData(
        total_spending=total_spending,
        total_income=total_income,
        cash_flow_summary=cash_flow_summary,
        dashboard_links=dashboard_links,
        dashboard_insights=dashboard_insights,
        data_quality=query_data.data_quality,
    )


def dashboard_context_payload(
    dashboard_request: DashboardRequest,
    query_data: DashboardQueryData,
    prepared_data: PreparedDashboardData,
) -> dict[str, Any]:
    """Assemble the full template context from query and prepared view data."""
    return {
        **dashboard_period_context(dashboard_request),
        **dashboard_filter_context(dashboard_request, query_data),
        **dashboard_summary_context(query_data, prepared_data),
        **dashboard_reports_context(dashboard_request),
    }


def dashboard_period_context(dashboard_request: DashboardRequest) -> dict[str, Any]:
    """Return date-period context values for the dashboard template."""
    return {
        "selected_period": dashboard_request.period,
        "period_options": DATE_PERIOD_OPTIONS,
        "period_custom": PERIOD_CUSTOM,
        "period_label": get_period_label(
            dashboard_request.period,
            dashboard_request.date_from,
            dashboard_request.date_to,
        ),
        "selected_date_from": dashboard_request.date_from,
        "selected_date_to": dashboard_request.date_to,
        "selected_date_from_label": format_date_label(dashboard_request.date_from),
        "selected_date_to_label": format_date_label(dashboard_request.date_to),
    }


def dashboard_filter_context(dashboard_request: DashboardRequest, query_data: DashboardQueryData) -> dict[str, Any]:
    """Return filter and quick-view context values for the dashboard template."""
    return {
        "category_options": query_data.category_options,
        "tag_options": query_data.tag_options,
        "filter_mode": dashboard_request.filter_mode,
        "selected_categories": dashboard_request.selected_categories,
        "selected_tags": dashboard_request.selected_tags,
        "account_options": query_data.account_options,
        "selected_account_id": dashboard_request.selected_account_id,
        "selected_merchant_id": dashboard_request.selected_merchant_id,
        "selected_merchant_label": query_data.selected_merchant_label,
        "merchant_suggestion_limit": query_data.merchant_suggestion_limit,
        "merchant_query": dashboard_request.merchant_query,
        "merchant_search": dashboard_request.merchant_search,
        "quick_view": dashboard_request.quick_view,
        "quick_view_options": build_quick_view_options(
            dashboard_request.quick_view,
            query_data.quick_view_counts,
        ),
    }


def selected_merchant_option_name(conn: Any, selected_merchant_id: int | None, merchant_query: str = "") -> str:
    """Return the selected merchant label for filter summaries and forms."""
    if selected_merchant_id is None:
        return merchant_query
    merchant = find_merchant_by_id(conn, selected_merchant_id)
    if merchant is None:
        return merchant_query
    return str(merchant["merchant_key"])


def dashboard_summary_context(query_data: DashboardQueryData, prepared_data: PreparedDashboardData) -> dict[str, Any]:
    """Return total, insight, and data-quality context values."""
    summary = query_data.summary
    return {
        "total_spending": prepared_data.total_spending,
        "total_income": prepared_data.total_income,
        "net_cashflow": prepared_data.cash_flow_summary["net_cashflow"],
        "cash_flow_summary": prepared_data.cash_flow_summary,
        "transaction_count": summary["transaction_count"],
        "uncategorized_count": summary["uncategorized_count"],
        "dashboard_insights": prepared_data.dashboard_insights,
        "data_quality": prepared_data.data_quality,
        "dashboard_links": prepared_data.dashboard_links,
        "first_tx_date": summary["first_tx_date"],
        "last_tx_date": summary["last_tx_date"],
    }


def dashboard_reports_context(dashboard_request: DashboardRequest) -> dict[str, Any]:
    """Return links from the simplified dashboard into detailed Reports pages."""
    return {"dashboard_report_links": build_dashboard_report_links(dashboard_request)}


def build_dashboard_report_links(dashboard_request: DashboardRequest) -> dict[str, str]:
    """Build Reports URLs that preserve shared dashboard filter state."""
    params = dashboard_report_params(dashboard_request)
    return {
        "overview": build_reports_url("reports.overview", **params),
        "taxonomy": build_reports_url("reports.taxonomy", **params),
        "accounts": build_reports_url("reports.accounts", **params),
        "merchants": build_reports_url("reports.merchants", **params),
        "income": build_reports_url("reports.income", **params, measure=REPORT_MEASURE_INCOME),
        "comparison": app_url("comparison.comparison"),
    }


def dashboard_report_params(dashboard_request: DashboardRequest) -> dict[str, object]:
    """Return query parameters shared by Dashboard and Reports filters."""
    params: dict[str, object] = {"period": dashboard_request.period}
    if dashboard_request.period == PERIOD_CUSTOM:
        params["date_from"] = dashboard_request.date_from
        params["date_to"] = dashboard_request.date_to
    if dashboard_request.selected_account_id:
        params["account_id"] = dashboard_request.selected_account_id
    if dashboard_request.selected_merchant_id:
        params["merchant_id"] = dashboard_request.selected_merchant_id
    if dashboard_request.merchant_query:
        params["merchant_query"] = dashboard_request.merchant_query
    return params
