"""Application orchestration for the comparison feature."""

from datetime import date

from finance_app.core.config import settings
from finance_app.core.i18n import month_abbreviation_labels
from finance_app.core.query import CoreFilters
from finance_app.database.engine import db_core_transaction
from finance_app.database.tables import transactions as transactions_table
from finance_app.modules.categories.service import get_category_options
from finance_app.modules.categories.tag_filters import has_concrete_tag_filter
from finance_app.modules.categories.taxonomy import get_tag_option_rows
from finance_app.modules.comparison.constants import PERIOD_COMPARISON_OPTIONS
from finance_app.modules.comparison.parsing import (
    clean_categories,
    clean_tags,
    parse_baseline_year,
    parse_period_comparison,
    parse_selected_years,
)
from finance_app.modules.comparison.presenter import (
    build_category_comparison,
    build_period_category_rows,
    build_period_filter_context,
    build_period_insight_groups,
    build_period_insights,
    build_period_merchant_rows,
    build_period_metric,
    build_period_unknown_warning,
    build_year_filter_context,
    build_year_unknown_warning,
    build_monthly_spending,
    period_comparison_ranges,
)
from finance_app.modules.comparison.queries import (
    build_category_conditions,
    fetch_available_years,
    fetch_category_comparison,
    fetch_monthly_spending,
    fetch_period_category_spending,
    fetch_period_merchant_transactions,
    fetch_period_summary,
    transaction_year,
)
from finance_app.modules.comparison.urls import build_comparison_url
from finance_app.modules.settings.runtime import get_int_setting, get_unknown_category


def build_comparison_context(args):
    """Build comparison context."""
    selected_years = parse_selected_years(args.getlist("years"))
    selected_period_categories = clean_categories(args.getlist("period_categories"))
    selected_year_categories = clean_categories(args.getlist("year_categories"))
    selected_period_tags = clean_tags(args.getlist("period_tags"))
    selected_year_tags = clean_tags(args.getlist("year_tags"))
    selected_period_comparison = parse_period_comparison(
        args.get("period_comparison")
    )

    with db_core_transaction() as conn:
        max_years = max(2, get_int_setting(conn, "comparison_max_years", settings.default_comparison_max_years))
        merchant_table_limit = get_int_setting(conn, "merchant_table_limit", settings.default_merchant_table_limit)
        available_years = fetch_available_years(conn)
        category_options = get_category_options(conn)
        tag_options = get_tag_option_rows(conn)
        unknown_category = get_unknown_category(conn)

        if not selected_years:
            selected_years = available_years[:max_years]
        else:
            selected_years = [
                year for year in selected_years if year in available_years
            ]

        if not selected_years:
            selected_years = [date.today().year]

        selected_baseline_year = parse_baseline_year(args.get("baseline_year"), selected_years)

        filters = CoreFilters()
        filters.add(transactions_table.c.ignored == 0)
        filters.add_in(transaction_year(), selected_years)
        for condition in build_category_conditions(
            selected_year_categories,
            selected_year_tags,
            unknown_category,
        ):
            filters.add(condition)

        monthly_rows = fetch_monthly_spending(conn, filters.criteria())
        category_rows = fetch_category_comparison(conn, filters.criteria(), unknown_category)
        period_comparison = build_period_comparison(
            conn,
            selected_period_comparison,
            selected_period_categories,
            selected_period_tags,
            unknown_category,
            merchant_table_limit,
        )

    category_comparison = build_category_comparison(selected_years, category_rows, selected_baseline_year)
    monthly_spending = build_monthly_spending(selected_years, monthly_rows)
    year_unknown_warning = build_year_unknown_warning(category_comparison, unknown_category)

    return dict(
        comparison_has_data=bool(available_years),
        available_years=available_years,
        selected_years=selected_years,
        selected_baseline_year=selected_baseline_year,
        max_comparison_years=max_years,
        category_options=category_options,
        tag_options=tag_options,
        selected_period_categories=selected_period_categories,
        selected_year_categories=selected_year_categories,
        selected_period_tags=selected_period_tags,
        selected_year_tags=selected_year_tags,
        merchant_table_limit=merchant_table_limit,
        period_filter_context=build_period_filter_context(
            PERIOD_COMPARISON_OPTIONS[selected_period_comparison],
            selected_period_categories,
            selected_period_tags,
        ),
        year_filter_context=build_year_filter_context(
            selected_years,
            selected_baseline_year,
            selected_year_categories,
            selected_year_tags,
        ),
        period_clear_url=build_comparison_url(
            years=selected_years,
            baseline_year=selected_baseline_year,
            year_categories=selected_year_categories,
            year_tags=selected_year_tags,
        ),
        year_clear_url=build_comparison_url(
            period_comparison=selected_period_comparison,
            period_categories=selected_period_categories,
            period_tags=selected_period_tags,
        ),
        period_comparison_options=[
            {"value": value, "label": label}
            for value, label in PERIOD_COMPARISON_OPTIONS.items()
        ],
        selected_period_comparison=selected_period_comparison,
        period_comparison=period_comparison,
        category_comparison=category_comparison,
        year_unknown_warning=year_unknown_warning,
        month_labels=month_abbreviation_labels(),
        monthly_spending=monthly_spending,
        monthly_spending_json=[
            {
                "year": year,
                "totals": monthly_spending[year],
            }
            for year in selected_years
        ],
    )




def build_period_comparison(
    conn,
    comparison_key,
    selected_categories,
    selected_tags,
    unknown_category,
    merchant_table_limit,
):
    """Build period comparison."""
    ranges = period_comparison_ranges(comparison_key, date.today())
    category_filters = build_category_conditions(selected_categories, selected_tags, unknown_category)
    include_transfer_credits = has_concrete_tag_filter(selected_tags)

    current_summary = fetch_period_summary(
        conn,
        ranges["current_start"],
        ranges["current_end"],
        category_filters,
        unknown_category,
        include_transfer_credits=include_transfer_credits,
    )
    previous_summary = fetch_period_summary(
        conn,
        ranges["previous_start"],
        ranges["previous_end"],
        category_filters,
        unknown_category,
        include_transfer_credits=include_transfer_credits,
    )
    category_rows = build_period_category_rows(
        fetch_period_category_spending(
            conn,
            ranges["current_start"],
            ranges["current_end"],
            category_filters,
            unknown_category,
        ),
        fetch_period_category_spending(
            conn,
            ranges["previous_start"],
            ranges["previous_end"],
            category_filters,
            unknown_category,
        ),
    )
    merchant_rows = build_period_merchant_rows(
        fetch_period_merchant_transactions(
            conn,
            ranges["current_start"],
            ranges["current_end"],
            category_filters,
            unknown_category,
        ),
        fetch_period_merchant_transactions(
            conn,
            ranges["previous_start"],
            ranges["previous_end"],
            category_filters,
            unknown_category,
        ),
        conn,
    )

    totals = [
        build_period_metric(
            "Spending",
            current_summary["spending"],
            previous_summary["spending"],
            "spending",
            ranges["previous_short_label"],
            "money",
        ),
        build_period_metric(
            "Income and Credits",
            current_summary["income"],
            previous_summary["income"],
            "income",
            ranges["previous_short_label"],
            "money",
        ),
        build_period_metric(
            "Net cash flow",
            current_summary["income"] - current_summary["spending"],
            previous_summary["income"] - previous_summary["spending"],
            "net cash flow",
            ranges["previous_short_label"],
            "money",
        ),
        build_period_metric(
            "Transactions",
            current_summary["transaction_count"],
            previous_summary["transaction_count"],
            "transactions",
            ranges["previous_short_label"],
            "count",
        ),
    ]
    unknown_warning = build_period_unknown_warning(
        category_rows,
        current_summary["spending"],
        previous_summary["spending"],
        unknown_category,
    )

    insights = build_period_insights(
        category_rows,
        merchant_rows,
        current_summary,
        previous_summary,
    )

    return {
        **ranges,
        "option_label": PERIOD_COMPARISON_OPTIONS[comparison_key],
        "totals": totals,
        "category_rows": category_rows[:12],
        "merchant_rows": merchant_rows[:merchant_table_limit],
        "insights": insights,
        "insight_groups": build_period_insight_groups(insights),
        "unknown_warning": unknown_warning,
        "current_transaction_count": current_summary["transaction_count"],
        "previous_transaction_count": previous_summary["transaction_count"],
    }


