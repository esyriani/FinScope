"""Application orchestration for the dashboard feature."""

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from finance_app.core.analytics import REPORT_MEASURE_INCOME, build_cash_flow_summary, build_data_quality
from finance_app.core.config import settings
from finance_app.core.constants import UNKNOWN_CATEGORY
from finance_app.core.money import rounded_money_float
from finance_app.core.periods import (
    DATE_PERIOD_OPTIONS,
    PERIOD_CUSTOM,
    format_date_label,
    get_period_label,
    period_start_date,
    previous_period_date_range,
)
from finance_app.core.query import CoreFilters
from finance_app.core.urls import build_app_url
from finance_app.database.engine import db_core_transaction
from finance_app.database.tables import transactions as transactions_table
from finance_app.modules.accounts.filters import account_filter_condition
from finance_app.modules.accounts.queries import list_account_options
from finance_app.modules.comparison.constants import ANALYSIS_MODE_SPENDING
from finance_app.modules.merchants.repository import find_merchant_by_id
from finance_app.modules.merchants.service import get_merchant_suggestion_limit
from finance_app.modules.settings.runtime import get_int_setting, get_unknown_category

from .filters import (
    DashboardRequest,
    apply_dashboard_dimension_filters,
    apply_quick_view_core_filter,
    parse_dashboard_request,
)
from .presenter import (
    attach_data_quality_urls,
    build_classification_scope_options,
    build_dashboard_chart_data,
    build_dashboard_links,
    build_top_driver_previews,
)
from .queries import (
    fetch_monthly_preview,
    fetch_quick_view_counts,
    fetch_summary,
    fetch_top_spending_categories,
    fetch_top_spending_changes,
    fetch_top_spending_merchants,
)


@dataclass(frozen=True)
class DashboardQueryData:
    """Container for dashboard rows fetched inside one database transaction.

    The service builds SQL filters once, fetches all required aggregates, and
    passes this immutable bundle to pure context-preparation helpers.
    """

    account_options: list[dict[str, Any]]
    selected_merchant_label: str
    merchant_suggestion_limit: int
    quick_view_counts: dict[str, Any]
    data_quality: dict[str, Any]
    summary: Any
    monthly_rows: list[dict[str, Any]]
    top_category_rows: list[dict[str, Any]]
    top_merchant_rows: list[dict[str, Any]]
    top_change_rows: list[dict[str, Any]]


@dataclass(frozen=True)
class PreparedDashboardData:
    """Container for dashboard view-model values derived from query data."""

    total_spending: float
    total_income: float
    cash_flow_summary: dict[str, Any]
    dashboard_links: dict[str, str]
    data_quality: dict[str, Any]
    chart_data: dict[str, list[Any]]
    top_driver_previews: dict[str, Any]


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
        top_driver_limit = get_int_setting(
            conn,
            "dashboard_top_driver_limit",
            settings.default_dashboard_top_driver_limit,
        )
        scoped_filters = dashboard_base_filters(dashboard_request)
        apply_dashboard_dimension_filters(
            scoped_filters,
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
        analysis_filters = scoped_filters.clone()
        apply_quick_view_core_filter(
            analysis_filters,
            dashboard_request.quick_view,
            unknown_category,
        )
        reporting_criteria = scoped_filters.criteria()
        analysis_criteria = analysis_filters.criteria()
        previous_filters = dashboard_previous_period_filters(dashboard_request)
        if previous_filters is not None:
            apply_dashboard_dimension_filters(
                previous_filters,
                dashboard_request.selected_merchant_id,
                dashboard_request.merchant_query,
            )
            apply_quick_view_core_filter(
                previous_filters,
                dashboard_request.quick_view,
                unknown_category,
            )

        account_options = list_account_options(conn)
        merchant_suggestion_limit = get_merchant_suggestion_limit(conn)
        selected_merchant_label = selected_merchant_option_name(
            conn,
            dashboard_request.selected_merchant_id,
            dashboard_request.merchant_query,
        )
        summary = fetch_summary(
            conn,
            reporting_criteria,
            unknown_category,
        )
        data_quality = build_data_quality(data_quality_summary)
        monthly_rows = fetch_monthly_preview(conn, reporting_criteria)
        top_category_rows = fetch_top_spending_categories(
            conn,
            analysis_criteria,
            unknown_category,
            limit=top_driver_limit,
        )
        top_merchant_rows = fetch_top_spending_merchants(conn, analysis_criteria, limit=top_driver_limit)
        top_change_rows = (
            fetch_top_spending_changes(
                conn,
                analysis_criteria,
                previous_filters.criteria(),
                unknown_category,
                limit=top_driver_limit,
            )
            if previous_filters is not None
            else []
        )

    return DashboardQueryData(
        account_options=account_options,
        selected_merchant_label=selected_merchant_label,
        merchant_suggestion_limit=merchant_suggestion_limit,
        quick_view_counts=quick_view_counts,
        data_quality=data_quality,
        summary=summary,
        monthly_rows=monthly_rows,
        top_category_rows=top_category_rows,
        top_merchant_rows=top_merchant_rows,
        top_change_rows=top_change_rows,
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


def dashboard_previous_period_filters(dashboard_request: DashboardRequest) -> CoreFilters | None:
    """Return filters for the previous comparable period, if one can be derived."""
    previous_start, previous_end = dashboard_previous_period_bounds(dashboard_request)
    if previous_start is None or previous_end is None:
        return None

    filters = CoreFilters()
    filters.add(transactions_table.c.ignored == 0)
    filters.add(account_filter_condition(dashboard_request.selected_account_id))
    filters.add(transactions_table.c.tx_date >= previous_start.isoformat())
    filters.add(transactions_table.c.tx_date < previous_end.isoformat())
    return filters


def dashboard_previous_period_bounds(dashboard_request: DashboardRequest) -> tuple[date | None, date | None]:
    """Return the previous comparable period as inclusive start and exclusive end."""
    if dashboard_request.period != PERIOD_CUSTOM:
        return previous_period_date_range(dashboard_request.period)

    if not dashboard_request.date_from or not dashboard_request.date_to:
        return None, None

    current_start = date.fromisoformat(dashboard_request.date_from)
    current_end = date.fromisoformat(dashboard_request.date_to)
    period_days = (current_end - current_start).days + 1
    previous_end = current_start
    previous_start = current_start - timedelta(days=period_days)
    return previous_start, previous_end


def prepare_dashboard_data(
    dashboard_request: DashboardRequest,
    query_data: DashboardQueryData,
) -> PreparedDashboardData:
    """Prepare presentation rows and derived totals for the dashboard context."""
    period = dashboard_request.period
    date_from = dashboard_request.date_from
    date_to = dashboard_request.date_to
    quick_view = dashboard_request.quick_view
    merchant_search = dashboard_request.merchant_search

    total_spending = rounded_money_float(query_data.summary["total_spending"])
    total_income = rounded_money_float(query_data.summary["total_income"])
    cash_flow_summary = build_cash_flow_summary(total_income, total_spending)
    dashboard_links = build_dashboard_links(
        period,
        date_from,
        date_to,
        merchant_search=merchant_search,
        account_id=dashboard_request.selected_account_id,
    )
    attach_data_quality_urls(
        query_data.data_quality,
        period,
        date_from,
        date_to,
        quick_view,
        merchant_search,
        dashboard_request.selected_account_id,
    )
    chart_data = build_dashboard_chart_data(query_data.monthly_rows)
    top_driver_previews = build_top_driver_previews(
        query_data.top_category_rows,
        query_data.top_merchant_rows,
        query_data.top_change_rows,
        total_spending,
        dashboard_report_params(dashboard_request),
        dashboard_comparison_params(dashboard_request),
    )

    return PreparedDashboardData(
        total_spending=total_spending,
        total_income=total_income,
        cash_flow_summary=cash_flow_summary,
        dashboard_links=dashboard_links,
        data_quality=query_data.data_quality,
        chart_data=chart_data,
        top_driver_previews=top_driver_previews,
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
        "account_options": query_data.account_options,
        "selected_account_id": dashboard_request.selected_account_id,
        "selected_merchant_id": dashboard_request.selected_merchant_id,
        "selected_merchant_label": query_data.selected_merchant_label,
        "merchant_suggestion_limit": query_data.merchant_suggestion_limit,
        "merchant_query": dashboard_request.merchant_query,
        "merchant_search": dashboard_request.merchant_search,
        "quick_view": dashboard_request.quick_view,
        "classification_scope_options": build_classification_scope_options(
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
        "data_quality": prepared_data.data_quality,
        "dashboard_links": prepared_data.dashboard_links,
        "dashboard_chart_data": prepared_data.chart_data,
        "top_driver_previews": prepared_data.top_driver_previews,
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
        "overview": build_app_url("reports.overview", **params),
        "taxonomy": build_app_url("reports.taxonomy", **params),
        "accounts": build_app_url("reports.accounts", **params),
        "merchants": build_app_url("reports.merchants", **params),
        "income": build_app_url("reports.income", **params, measure=REPORT_MEASURE_INCOME),
        "comparison": build_app_url("comparison.comparison", **dashboard_comparison_params(dashboard_request)),
    }


def dashboard_report_params(dashboard_request: DashboardRequest) -> dict[str, object]:
    """Return query parameters shared by Dashboard and Reports filters."""
    params: dict[str, object] = {
        "period": dashboard_request.period,
        "quick_view": dashboard_request.quick_view,
    }
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


def dashboard_comparison_params(dashboard_request: DashboardRequest) -> dict[str, object]:
    """Return Comparison parameters matching the dashboard's compact previews."""
    params: dict[str, object] = {
        "comparison_view": "period",
        "analysis_mode": ANALYSIS_MODE_SPENDING,
        "period_comparison": dashboard_period_comparison_key(dashboard_request),
    }
    if dashboard_request.selected_account_id:
        params["account_id"] = dashboard_request.selected_account_id
    if dashboard_request.selected_merchant_id:
        params["merchant_id"] = dashboard_request.selected_merchant_id
    if dashboard_request.merchant_query:
        params["merchant_query"] = dashboard_request.merchant_query
    return params


def dashboard_period_comparison_key(dashboard_request: DashboardRequest) -> str:
    """Return the closest predefined Comparison period for the dashboard period."""
    if dashboard_request.period == "ytd":
        return "ytd_last_year"
    if dashboard_request.period == "month":
        return "month_previous"
    return "month_previous"
