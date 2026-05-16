"""Application orchestration for the dashboard feature."""

from finance_app.core.constants import FILTER_MODE_INCLUDE, FILTER_MODES, UNKNOWN_CATEGORY
from finance_app.core.money import money_to_float, rounded_money_float
from finance_app.modules.categories.service import get_category_options
from finance_app.modules.categories.taxonomy import get_tag_option_rows
from finance_app.database.engine import db_core_transaction
from finance_app.database.tables import transactions as transactions_table
from finance_app.core.periods import (
    DATE_PERIOD_OPTIONS,
    DEFAULT_DATE_PERIOD,
    PERIOD_CUSTOM,
    format_date_label,
    get_period_label,
    normalize_date_period,
    period_start_date,
    parse_iso_date,
)
from finance_app.modules.settings.runtime import get_int_setting, get_unknown_category
from finance_app.modules.transactions.constants import AMOUNT_TYPE_CREDIT, AMOUNT_TYPE_INCOME, AMOUNT_TYPE_SPENDING
from finance_app.core.query import CoreFilters, parse_sort_direction
from .filters import (
    apply_quick_view_core_filter,
    dashboard_table_default_direction,
    parse_dashboard_table_sort,
    parse_quick_view,
)
from .urls import dashboard_month_url, dashboard_table_sort_url, dashboard_transactions_url
from .queries import (
    fetch_last_upload,
    fetch_merchant_analytics,
    fetch_monthly_expenses,
    fetch_monthly_income,
    fetch_monthly_net,
    fetch_quick_view_counts,
    fetch_spending_by_category,
    fetch_summary,
)
from .constants import (
    DASHBOARD_CATEGORY_SORTS,
    DASHBOARD_CATEGORY_SORT_SPENDING,
    DASHBOARD_MERCHANT_SORT_SPENDING,
    DASHBOARD_MERCHANT_SORTS,
    DASHBOARD_TABLE_CATEGORY,
    DASHBOARD_TABLE_MERCHANT,
    QUICK_VIEW_CUSTOM,
)
from .presenter import (
    attach_data_quality_urls,
    build_cash_flow_summary,
    build_category_rows,
    build_dashboard_links,
    build_data_quality,
    build_quick_view_options,
    build_spending_income_series,
    sort_category_rows,
    sort_merchant_rows,
)


def build_dashboard_context(args):
    """Build dashboard context."""
    period = args.get("period", DEFAULT_DATE_PERIOD).strip()
    filter_mode = args.get("filter_mode", FILTER_MODE_INCLUDE).strip()
    if filter_mode not in FILTER_MODES:
        filter_mode = FILTER_MODE_INCLUDE
    merchant_sort = parse_dashboard_table_sort(
        args.get("merchant_sort"),
        DASHBOARD_MERCHANT_SORTS,
        DASHBOARD_MERCHANT_SORT_SPENDING,
    )
    merchant_direction = parse_sort_direction(
        args.get("merchant_direction"),
        default=dashboard_table_default_direction(merchant_sort),
    )
    category_table_sort = parse_dashboard_table_sort(
        args.get("category_sort"),
        DASHBOARD_CATEGORY_SORTS,
        DASHBOARD_CATEGORY_SORT_SPENDING,
    )
    category_table_direction = parse_sort_direction(
        args.get("category_direction"),
        default=dashboard_table_default_direction(category_table_sort),
    )

    selected_categories = [
        category.strip()
        for category in args.getlist("categories")
        if category.strip()
    ]
    selected_tags = [
        tag.strip()
        for tag in args.getlist("tags")
        if tag.strip()
    ]
    quick_view = parse_quick_view(args.get("quick_view"), selected_categories, selected_tags)
    if quick_view != QUICK_VIEW_CUSTOM:
        selected_categories = []
        selected_tags = []
        filter_mode = FILTER_MODE_INCLUDE

    period = normalize_date_period(period)
    date_from = parse_iso_date(args.get("date_from")) if period == PERIOD_CUSTOM else ""
    date_to = parse_iso_date(args.get("date_to")) if period == PERIOD_CUSTOM else ""
    if date_from and date_to and date_from > date_to:
        date_from, date_to = date_to, date_from
    with db_core_transaction() as conn:
        unknown_category = get_unknown_category(conn) or UNKNOWN_CATEGORY
        merchant_table_limit = get_int_setting(conn, "merchant_table_limit", 10)
        base_filters = CoreFilters()
        base_filters.add(transactions_table.c.ignored == 0)
        start_date = period_start_date(period)
        if start_date:
            base_filters.add(transactions_table.c.tx_date >= start_date)
        if period == PERIOD_CUSTOM:
            if date_from:
                base_filters.add(transactions_table.c.tx_date >= date_from)
            if date_to:
                base_filters.add(transactions_table.c.tx_date <= date_to)

        quick_view_counts = fetch_quick_view_counts(
            conn,
            base_filters.criteria(),
            unknown_category,
        )
        filters = base_filters.clone()
        apply_quick_view_core_filter(
            filters,
            quick_view,
            selected_categories,
            selected_tags,
            filter_mode,
            unknown_category,
        )
        filter_criteria = filters.criteria()
        include_transfer_credits = bool(selected_tags) and filter_mode == FILTER_MODE_INCLUDE

        category_options = get_category_options(conn)
        tag_options = get_tag_option_rows(conn)
        spending_by_category = fetch_spending_by_category(conn, filter_criteria, unknown_category)
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
            period,
            filter_criteria,
            filter_mode,
            selected_categories,
            selected_tags,
            unknown_category,
            date_from,
            date_to,
            quick_view,
            merchant_table_limit,
        )
        summary = fetch_summary(
            conn,
            filter_criteria,
            unknown_category,
            include_transfer_credits=include_transfer_credits,
        )
        data_quality = build_data_quality(summary)
        last_upload = fetch_last_upload(conn)

    total_spending = rounded_money_float(summary["total_spending"])
    total_income = rounded_money_float(summary["total_income"])
    cash_flow_summary = build_cash_flow_summary(total_income, total_spending)
    known_category_spending = sum(money_to_float(row["total"]) for row in spending_by_category)
    category_rows = build_category_rows(
        spending_by_category,
        known_category_spending,
        period,
        filter_mode,
        selected_categories,
        selected_tags,
        date_from,
        date_to,
        quick_view,
    )
    sort_merchant_rows(merchant_rows, merchant_sort, merchant_direction)
    sort_category_rows(category_rows, category_table_sort, category_table_direction)
    dashboard_links = build_dashboard_links(
        period,
        filter_mode,
        selected_categories,
        selected_tags,
        date_from,
        date_to,
        quick_view,
        include_transfer_credits=include_transfer_credits,
    )
    attach_data_quality_urls(
        data_quality,
        period,
        filter_mode,
        selected_categories,
        selected_tags,
        date_from,
        date_to,
        quick_view,
    )
    income_amount_type = AMOUNT_TYPE_CREDIT if include_transfer_credits else AMOUNT_TYPE_INCOME

    return dict(
        selected_period=period,
        period_options=DATE_PERIOD_OPTIONS,
        period_custom=PERIOD_CUSTOM,
        period_label=get_period_label(period, date_from, date_to),
        selected_date_from=date_from,
        selected_date_to=date_to,
        selected_date_from_label=format_date_label(date_from),
        selected_date_to_label=format_date_label(date_to),
        category_options=category_options,
        tag_options=tag_options,
        filter_mode=filter_mode,
        selected_categories=selected_categories,
        selected_tags=selected_tags,
        quick_view=quick_view,
        quick_view_custom=QUICK_VIEW_CUSTOM,
        quick_view_options=build_quick_view_options(quick_view, quick_view_counts),
        total_spending=total_spending,
        total_income=total_income,
        net_cashflow=cash_flow_summary["net_cashflow"],
        cash_flow_summary=cash_flow_summary,
        transaction_count=summary["transaction_count"],
        uncategorized_count=summary["uncategorized_count"],
        data_quality=data_quality,
        dashboard_links=dashboard_links,
        first_tx_date=summary["first_tx_date"],
        last_tx_date=summary["last_tx_date"],
        last_upload=last_upload,
        category_labels=[row["category"] for row in spending_by_category],
        category_totals=[rounded_money_float(row["total"]) for row in spending_by_category],
        category_urls=[
            dashboard_transactions_url(
                period,
                filter_mode,
                selected_categories,
                False,
                date_from,
                date_to,
                quick_view,
                selected_tags=selected_tags,
                category=row["category"],
                amount_type=AMOUNT_TYPE_SPENDING,
            )
            for row in spending_by_category
        ],
        category_rows=category_rows,
        expense_month_labels=[row["month"] for row in monthly_expenses],
        expense_month_totals=[rounded_money_float(row["total"]) for row in monthly_expenses],
        expense_month_urls=[
            dashboard_month_url(
                row["month"],
                filter_mode,
                selected_categories,
                date_from,
                date_to,
                quick_view,
                selected_tags=selected_tags,
                amount_type=AMOUNT_TYPE_SPENDING,
            )
            for row in monthly_expenses
        ],
        income_month_labels=[row["month"] for row in monthly_income],
        income_month_totals=[rounded_money_float(row["total"]) for row in monthly_income],
        income_month_urls=[
            dashboard_month_url(
                row["month"],
                filter_mode,
                selected_categories,
                date_from,
                date_to,
                quick_view,
                selected_tags=selected_tags,
                amount_type=income_amount_type,
            )
            for row in monthly_income
        ],
        spending_income_month_labels=spending_income["labels"],
        spending_income_spending_totals=spending_income["spending_totals"],
        spending_income_income_totals=spending_income["income_totals"],
        spending_income_spending_urls=[
            dashboard_month_url(
                month,
                filter_mode,
                selected_categories,
                date_from,
                date_to,
                quick_view,
                selected_tags=selected_tags,
                amount_type=AMOUNT_TYPE_SPENDING,
            )
            for month in spending_income["labels"]
        ],
        spending_income_income_urls=[
            dashboard_month_url(
                month,
                filter_mode,
                selected_categories,
                date_from,
                date_to,
                quick_view,
                selected_tags=selected_tags,
                amount_type=income_amount_type,
            )
            for month in spending_income["labels"]
        ],
        net_month_labels=[row["month"] for row in monthly_net],
        net_month_totals=[rounded_money_float(row["total"]) for row in monthly_net],
        net_month_urls=[
            dashboard_month_url(
                row["month"],
                filter_mode,
                selected_categories,
                date_from,
                date_to,
                quick_view,
                selected_tags=selected_tags,
            )
            for row in monthly_net
        ],
        merchant_rows=merchant_rows,
        merchant_sort=merchant_sort,
        merchant_direction=merchant_direction,
        merchant_sort_url=lambda sort_name: dashboard_table_sort_url(
            args,
            DASHBOARD_TABLE_MERCHANT,
            sort_name,
            merchant_sort,
            merchant_direction,
        ),
        category_table_sort=category_table_sort,
        category_table_direction=category_table_direction,
        category_sort_url=lambda sort_name: dashboard_table_sort_url(
            args,
            DASHBOARD_TABLE_CATEGORY,
            sort_name,
            category_table_sort,
            category_table_direction,
        ),
    )
