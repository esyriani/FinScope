"""Application orchestration for the dashboard feature."""

from dataclasses import dataclass
from typing import Any, Protocol

from finance_app.core.constants import FILTER_MODE_INCLUDE, UNKNOWN_CATEGORY
from finance_app.core.money import money_to_float, rounded_money_float
from finance_app.core.periods import (
    DATE_PERIOD_OPTIONS,
    PERIOD_CUSTOM,
    format_date_label,
    get_period_label,
    period_start_date,
)
from finance_app.core.query import CoreFilters, QueryArgs
from finance_app.database.engine import db_core_transaction
from finance_app.database.tables import transactions as transactions_table
from finance_app.modules.accounts.filters import account_filter_condition
from finance_app.modules.accounts.queries import list_account_options
from finance_app.modules.categories.service import get_category_options
from finance_app.modules.categories.tag_filters import has_concrete_tag_filter
from finance_app.modules.categories.taxonomy import get_tag_option_rows
from finance_app.modules.settings.runtime import get_int_setting, get_unknown_category
from finance_app.modules.transactions.constants import AMOUNT_TYPE_CREDIT, AMOUNT_TYPE_INCOME, AMOUNT_TYPE_SPENDING

from .constants import (
    DASHBOARD_BREAKDOWN_CATEGORY,
    DASHBOARD_BREAKDOWN_TAG,
    DASHBOARD_INCOME_CATEGORY,
    DASHBOARD_TABLE_CATEGORY,
    DASHBOARD_TABLE_MERCHANT,
)
from .filters import (
    DashboardRequest,
    apply_dashboard_dimension_filters,
    apply_quick_view_core_filter,
    parse_dashboard_request,
)
from .presenter import (
    attach_data_quality_urls,
    build_cash_flow_summary,
    build_category_rows,
    build_dashboard_insights,
    build_dashboard_links,
    build_data_quality,
    build_quick_view_options,
    build_spending_income_series,
    sort_category_rows,
    sort_merchant_rows,
)
from .queries import (
    fetch_merchant_analytics,
    fetch_monthly_expenses,
    fetch_monthly_income,
    fetch_monthly_net,
    fetch_quick_view_counts,
    fetch_spending_by_category,
    fetch_spending_by_tag,
    fetch_summary,
)
from .urls import QueryStringArgs, dashboard_month_url, dashboard_table_sort_url, dashboard_url


class DashboardArgs(QueryArgs, QueryStringArgs, Protocol):
    """Represent request args used by dashboard parsers and URL builders."""


@dataclass(frozen=True)
class DashboardQueryData:
    """Container for dashboard rows fetched inside one database transaction.

    The service builds SQL filters once, fetches all required aggregates, and
    passes this immutable bundle to pure context-preparation helpers.
    """

    unknown_category: str
    include_transfer_credits: bool
    category_options: list[str]
    tag_options: list[dict[str, Any]]
    account_options: list[dict[str, Any]]
    quick_view_counts: dict[str, Any]
    data_quality: dict[str, Any]
    spending_by_category_all: list[Any]
    spending_by_tag: list[dict[str, Any]]
    monthly_expenses: list[dict[str, Any]]
    monthly_income: list[dict[str, Any]]
    spending_income: dict[str, Any]
    monthly_net: list[dict[str, Any]]
    merchant_rows: list[dict[str, Any]]
    summary: Any


@dataclass(frozen=True)
class PreparedDashboardData:
    """Container for dashboard view-model values derived from query data."""

    total_spending: float
    total_income: float
    cash_flow_summary: dict[str, Any]
    category_rows: list[dict[str, Any]]
    chart_category_rows: list[dict[str, Any]]
    dashboard_links: dict[str, str]
    dashboard_insights: dict[str, Any]
    data_quality: dict[str, Any]
    income_amount_type: str


def build_dashboard_context(args: Any) -> dict[str, Any]:
    """Build the dashboard template context for request query arguments."""
    dashboard_request = parse_dashboard_request(args)
    query_data = fetch_dashboard_query_data(dashboard_request)
    prepared_data = prepare_dashboard_data(args, dashboard_request, query_data)
    return dashboard_context_payload(args, dashboard_request, query_data, prepared_data)


def fetch_dashboard_query_data(dashboard_request: DashboardRequest) -> DashboardQueryData:
    """Fetch all database-backed dashboard aggregates for one request.

    Args:
        dashboard_request: Normalized dashboard filter and table-control values.

    Returns:
        A ``DashboardQueryData`` bundle containing settings, taxonomy options,
        summary rows, chart rows, and table rows. The returned rows are detached
        from the transaction and safe for later presentation-only processing.
    """
    with db_core_transaction() as conn:
        unknown_category = get_unknown_category(conn) or UNKNOWN_CATEGORY
        merchant_table_limit = get_int_setting(conn, "merchant_table_limit", 10)
        scoped_filters = dashboard_base_filters(dashboard_request)
        apply_dashboard_dimension_filters(
            scoped_filters,
            dashboard_request.selected_categories,
            dashboard_request.selected_tags,
            dashboard_request.filter_mode,
            unknown_category,
            dashboard_request.merchant_search,
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
        spending_by_category_all = fetch_spending_by_category(
            conn,
            filter_criteria,
            unknown_category,
            include_income_category=True,
        )
        spending_by_tag = fetch_spending_by_tag(
            conn,
            filter_criteria,
            include_income_category=dashboard_request.show_income,
        )
        monthly_expenses = fetch_monthly_expenses(conn, filter_criteria)
        monthly_income = fetch_monthly_income(
            conn,
            filter_criteria,
            include_transfer_credits=include_transfer_credits,
        )
        spending_income = build_spending_income_series(monthly_expenses, monthly_income)
        monthly_net = fetch_monthly_net(
            conn,
            filter_criteria,
            include_transfer_credits=include_transfer_credits,
        )
        merchant_rows = fetch_merchant_analytics(
            conn,
            dashboard_request.period,
            filter_criteria,
            dashboard_request.filter_mode,
            dashboard_request.selected_categories,
            dashboard_request.selected_tags,
            unknown_category,
            dashboard_request.date_from,
            dashboard_request.date_to,
            dashboard_request.quick_view,
            merchant_table_limit,
            dashboard_request.merchant_search,
            dashboard_request.selected_account_id,
        )
        summary = fetch_summary(
            conn,
            filter_criteria,
            unknown_category,
            include_transfer_credits=include_transfer_credits,
        )
        data_quality = build_data_quality(data_quality_summary)

    return DashboardQueryData(
        unknown_category=unknown_category,
        include_transfer_credits=include_transfer_credits,
        category_options=category_options,
        tag_options=tag_options,
        account_options=account_options,
        quick_view_counts=quick_view_counts,
        data_quality=data_quality,
        spending_by_category_all=spending_by_category_all,
        spending_by_tag=spending_by_tag,
        monthly_expenses=monthly_expenses,
        monthly_income=monthly_income,
        spending_income=spending_income,
        monthly_net=monthly_net,
        merchant_rows=merchant_rows,
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
    args: DashboardArgs,
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
    spending_breakdown, breakdown_total = dashboard_breakdown_rows(
        dashboard_request,
        query_data,
        total_spending,
    )
    category_rows = build_category_rows(
        spending_breakdown,
        breakdown_total,
        period,
        filter_mode,
        selected_categories,
        selected_tags,
        date_from,
        date_to,
        quick_view,
        merchant_search,
        dashboard_request.selected_account_id,
        breakdown=dashboard_request.breakdown_mode,
    )
    chart_category_rows = list(category_rows)
    sort_merchant_rows(
        query_data.merchant_rows,
        dashboard_request.merchant_sort,
        dashboard_request.merchant_direction,
    )
    sort_category_rows(
        category_rows,
        dashboard_request.category_table_sort,
        dashboard_request.category_table_direction,
    )
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
    income_amount_type = AMOUNT_TYPE_CREDIT if query_data.include_transfer_credits else AMOUNT_TYPE_INCOME

    return PreparedDashboardData(
        total_spending=total_spending,
        total_income=total_income,
        cash_flow_summary=cash_flow_summary,
        category_rows=category_rows,
        chart_category_rows=chart_category_rows,
        dashboard_links=dashboard_links,
        dashboard_insights=dashboard_insights,
        data_quality=query_data.data_quality,
        income_amount_type=income_amount_type,
    )


def dashboard_breakdown_rows(
    dashboard_request: DashboardRequest,
    query_data: DashboardQueryData,
    total_spending: float,
) -> tuple[list[Any], float]:
    """Return rows and denominator for the selected dashboard breakdown."""
    income_category_name = DASHBOARD_INCOME_CATEGORY.casefold()
    income_category_spending = sum(
        money_to_float(row["total"])
        for row in query_data.spending_by_category_all
        if str(row["category"]).casefold() == income_category_name
    )
    spending_by_category = (
        query_data.spending_by_category_all
        if dashboard_request.show_income
        else [
            row
            for row in query_data.spending_by_category_all
            if str(row["category"]).casefold() != income_category_name
        ]
    )
    spending_breakdown = (
        query_data.spending_by_tag
        if dashboard_request.breakdown_mode == DASHBOARD_BREAKDOWN_TAG
        else spending_by_category
    )
    if dashboard_request.breakdown_mode == DASHBOARD_BREAKDOWN_TAG and not dashboard_request.show_untagged:
        spending_breakdown = [row for row in spending_breakdown if not row.get("untagged")]
    if dashboard_request.breakdown_mode == DASHBOARD_BREAKDOWN_TAG:
        breakdown_total = (
            total_spending
            if dashboard_request.show_income
            else max(
                0,
                total_spending - rounded_money_float(income_category_spending),
            )
        )
    else:
        breakdown_total = sum(money_to_float(row["total"]) for row in spending_by_category)

    return spending_breakdown, breakdown_total


def dashboard_context_payload(
    args: DashboardArgs,
    dashboard_request: DashboardRequest,
    query_data: DashboardQueryData,
    prepared_data: PreparedDashboardData,
) -> dict[str, Any]:
    """Assemble the full template context from query and prepared view data."""
    return {
        **dashboard_period_context(dashboard_request),
        **dashboard_filter_context(dashboard_request, query_data),
        **dashboard_breakdown_context(args, dashboard_request),
        **dashboard_summary_context(query_data, prepared_data),
        **dashboard_category_context(prepared_data),
        **dashboard_month_context(dashboard_request, query_data, prepared_data),
        **dashboard_table_context(args, dashboard_request, query_data),
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
        "merchant_search": dashboard_request.merchant_search,
        "quick_view": dashboard_request.quick_view,
        "quick_view_options": build_quick_view_options(
            dashboard_request.quick_view,
            query_data.quick_view_counts,
        ),
    }


def dashboard_breakdown_context(args: DashboardArgs, dashboard_request: DashboardRequest) -> dict[str, Any]:
    """Return breakdown control labels, URLs, and selected state."""
    breakdown_is_tag = dashboard_request.breakdown_mode == DASHBOARD_BREAKDOWN_TAG
    return {
        "breakdown_mode": dashboard_request.breakdown_mode,
        "breakdown_category": DASHBOARD_BREAKDOWN_CATEGORY,
        "breakdown_tag": DASHBOARD_BREAKDOWN_TAG,
        "breakdown_options": [
            {
                "value": DASHBOARD_BREAKDOWN_CATEGORY,
                "label": "Categories",
                "url": dashboard_url(args, breakdown=DASHBOARD_BREAKDOWN_CATEGORY, show_untagged=""),
                "active": dashboard_request.breakdown_mode == DASHBOARD_BREAKDOWN_CATEGORY,
            },
            {
                "value": DASHBOARD_BREAKDOWN_TAG,
                "label": "Tags",
                "url": dashboard_url(args, breakdown=DASHBOARD_BREAKDOWN_TAG),
                "active": breakdown_is_tag,
            },
        ],
        "breakdown_chart_title": "Spending by tag" if breakdown_is_tag else "Spending by category",
        "breakdown_table_title": "Tag detail" if breakdown_is_tag else "Category detail",
        "breakdown_label": "Tag" if breakdown_is_tag else "Category",
        "breakdown_is_tag": breakdown_is_tag,
        "show_untagged": dashboard_request.show_untagged,
        "show_untagged_url": dashboard_url(
            args,
            breakdown=DASHBOARD_BREAKDOWN_TAG,
            show_untagged="" if dashboard_request.show_untagged else "1",
        ),
        "show_income": dashboard_request.show_income,
        "show_income_url": dashboard_url(
            args,
            show_income="" if dashboard_request.show_income else "1",
        ),
    }


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


def dashboard_category_context(prepared_data: PreparedDashboardData) -> dict[str, Any]:
    """Return chart and table rows for the selected spending breakdown."""
    return {
        "category_labels": [row["category"] for row in prepared_data.chart_category_rows],
        "category_totals": [rounded_money_float(row["total"]) for row in prepared_data.chart_category_rows],
        "category_urls": [row["url"] for row in prepared_data.chart_category_rows],
        "category_rows": prepared_data.category_rows,
    }


def dashboard_month_context(
    dashboard_request: DashboardRequest,
    query_data: DashboardQueryData,
    prepared_data: PreparedDashboardData,
) -> dict[str, Any]:
    """Return monthly chart series and drill-down URLs."""
    return {
        "expense_month_labels": [row["month"] for row in query_data.monthly_expenses],
        "expense_month_totals": [rounded_money_float(row["total"]) for row in query_data.monthly_expenses],
        "expense_month_urls": dashboard_month_urls(
            query_data.monthly_expenses,
            dashboard_request,
            AMOUNT_TYPE_SPENDING,
        ),
        "income_month_labels": [row["month"] for row in query_data.monthly_income],
        "income_month_totals": [rounded_money_float(row["total"]) for row in query_data.monthly_income],
        "income_month_urls": dashboard_month_urls(
            query_data.monthly_income,
            dashboard_request,
            prepared_data.income_amount_type,
        ),
        "spending_income_month_labels": query_data.spending_income["labels"],
        "spending_income_spending_totals": query_data.spending_income["spending_totals"],
        "spending_income_income_totals": query_data.spending_income["income_totals"],
        "spending_income_spending_urls": dashboard_month_urls_for_labels(
            query_data.spending_income["labels"],
            dashboard_request,
            AMOUNT_TYPE_SPENDING,
        ),
        "spending_income_income_urls": dashboard_month_urls_for_labels(
            query_data.spending_income["labels"],
            dashboard_request,
            prepared_data.income_amount_type,
        ),
        "net_month_labels": [row["month"] for row in query_data.monthly_net],
        "net_month_totals": [rounded_money_float(row["total"]) for row in query_data.monthly_net],
        "net_month_urls": dashboard_month_urls_for_labels(
            [row["month"] for row in query_data.monthly_net],
            dashboard_request,
            None,
        ),
    }


def dashboard_month_urls(
    rows: list[dict[str, Any]],
    dashboard_request: DashboardRequest,
    amount_type: str,
) -> list[str]:
    """Return dashboard drill-down URLs for rows containing a month key."""
    return dashboard_month_urls_for_labels(
        [row["month"] for row in rows],
        dashboard_request,
        amount_type,
    )


def dashboard_month_urls_for_labels(
    months: list[str],
    dashboard_request: DashboardRequest,
    amount_type: str | None,
) -> list[str]:
    """Return dashboard drill-down URLs for month labels."""
    return [
        dashboard_month_url(
            month,
            dashboard_request.filter_mode,
            dashboard_request.selected_categories,
            dashboard_request.date_from,
            dashboard_request.date_to,
            dashboard_request.quick_view,
            selected_tags=dashboard_request.selected_tags,
            merchant_search=dashboard_request.merchant_search,
            account_id=dashboard_request.selected_account_id,
            amount_type=amount_type,
        )
        for month in months
    ]


def dashboard_table_context(
    args: DashboardArgs,
    dashboard_request: DashboardRequest,
    query_data: DashboardQueryData,
) -> dict[str, Any]:
    """Return merchant and category table controls for the dashboard template."""
    return {
        "merchant_rows": query_data.merchant_rows,
        "merchant_sort": dashboard_request.merchant_sort,
        "merchant_direction": dashboard_request.merchant_direction,
        "merchant_sort_url": lambda sort_name: dashboard_table_sort_url(
            args,
            DASHBOARD_TABLE_MERCHANT,
            sort_name,
            dashboard_request.merchant_sort,
            dashboard_request.merchant_direction,
        ),
        "category_table_sort": dashboard_request.category_table_sort,
        "category_table_direction": dashboard_request.category_table_direction,
        "category_sort_url": lambda sort_name: dashboard_table_sort_url(
            args,
            DASHBOARD_TABLE_CATEGORY,
            sort_name,
            dashboard_request.category_table_sort,
            dashboard_request.category_table_direction,
        ),
    }
