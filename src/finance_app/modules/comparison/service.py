"""Application orchestration for the comparison feature."""

from datetime import date
from typing import Any

from finance_app.core.config import settings
from finance_app.core.i18n import month_abbreviation_labels
from finance_app.core.query import CoreFilters
from finance_app.database.engine import db_core_transaction
from finance_app.database.tables import transactions as transactions_table
from finance_app.modules.accounts.filters import parse_account_id
from finance_app.modules.accounts.queries import list_account_options
from finance_app.modules.categories.service import get_category_options
from finance_app.modules.categories.tag_filters import has_concrete_tag_filter
from finance_app.modules.categories.taxonomy import get_tag_option_rows
from finance_app.modules.comparison.constants import (
    ANALYSIS_MODE_OPTIONS,
    ANALYSIS_MODE_SPENDING,
    PERIOD_COMPARISON_OPTIONS,
)
from finance_app.modules.comparison.insight_cards import (
    build_period_insight_groups,
    build_period_unknown_warning,
    build_year_unknown_warning,
)
from finance_app.modules.comparison.insights import build_period_insights
from finance_app.modules.comparison.parsing import (
    clean_categories,
    clean_tags,
    parse_analysis_mode,
    parse_baseline_year,
    parse_comparison_view,
    parse_period_comparison,
    parse_selected_years,
)
from finance_app.modules.comparison.presenter import (
    build_category_comparison,
    build_monthly_spending,
    build_monthly_spending_comparison,
    build_monthly_spending_statistics,
    build_period_category_history,
    build_period_category_rows,
    build_period_filter_context,
    build_period_merchant_activity_history,
    build_period_merchant_history,
    build_period_merchant_rows,
    build_period_metric,
    build_year_filter_context,
    period_comparison_ranges,
)
from finance_app.modules.comparison.queries import (
    build_category_conditions,
    fetch_available_years,
    fetch_category_comparison,
    fetch_historical_monthly_category_analysis,
    fetch_historical_monthly_merchant_transactions,
    fetch_monthly_analysis,
    fetch_period_category_analysis,
    fetch_period_merchant_transactions,
    fetch_period_summary,
    transaction_year,
)
from finance_app.modules.comparison.urls import build_comparison_url
from finance_app.modules.merchants.filters import parse_merchant_id, parse_merchant_query
from finance_app.modules.merchants.repository import find_merchant_by_id
from finance_app.modules.merchants.service import get_merchant_suggestion_limit
from finance_app.modules.settings.runtime import get_int_setting, get_unknown_category


def build_comparison_context(args: Any) -> dict[str, Any]:
    """Build comparison context."""
    selected_comparison_view = parse_comparison_view(args.get("comparison_view"))
    selected_years = parse_selected_years(args.getlist("years"))
    selected_period_categories = clean_categories(args.getlist("period_categories"))
    selected_year_categories = clean_categories(args.getlist("year_categories"))
    selected_period_tags = clean_tags(args.getlist("period_tags"))
    selected_year_tags = clean_tags(args.getlist("year_tags"))
    selected_account_id = parse_account_id(args.get("account_id"))
    selected_merchant_id = parse_merchant_id(args.get("merchant_id"))
    merchant_query = parse_merchant_query(args.get("merchant_query"))
    selected_analysis_mode = parse_analysis_mode(args.get("analysis_mode"))
    selected_analysis_mode_option = ANALYSIS_MODE_OPTIONS[selected_analysis_mode]
    selected_period_comparison = parse_period_comparison(args.get("period_comparison"))

    with db_core_transaction() as conn:
        max_years = max(2, get_int_setting(conn, "comparison_max_years", settings.default_comparison_max_years))
        insight_card_limit = get_int_setting(
            conn,
            "comparison_insight_card_limit",
            settings.default_comparison_insight_card_limit,
        )
        merchant_table_limit = get_int_setting(conn, "merchant_table_limit", settings.default_merchant_table_limit)
        merchant_suggestion_limit = get_merchant_suggestion_limit(conn)
        account_options = list_account_options(conn)
        selected_account_name = selected_account_option_name(account_options, selected_account_id)
        selected_merchant_label = selected_merchant_option_name(conn, selected_merchant_id, merchant_query)
        available_years = fetch_available_years(conn, selected_account_id, selected_merchant_id, merchant_query)
        category_options = get_category_options(conn)
        tag_options = get_tag_option_rows(conn)
        unknown_category = get_unknown_category(conn)

        if not selected_years:
            selected_years = available_years[:max_years]
        else:
            selected_years = [year for year in selected_years if year in available_years]

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
            account_id=selected_account_id,
            merchant_id=selected_merchant_id,
            merchant_query=merchant_query,
        ):
            filters.add(condition)

        include_year_transfer_credits = has_concrete_tag_filter(selected_year_tags)
        monthly_rows = fetch_monthly_analysis(
            conn,
            filters.criteria(),
            selected_analysis_mode,
            include_transfer_credits=include_year_transfer_credits,
        )
        category_rows = fetch_category_comparison(
            conn,
            filters.criteria(),
            unknown_category,
            selected_analysis_mode,
            include_transfer_credits=include_year_transfer_credits,
        )
        period_comparison = build_period_comparison(
            conn,
            selected_period_comparison,
            selected_period_categories,
            selected_period_tags,
            unknown_category,
            merchant_table_limit,
            selected_analysis_mode,
            account_id=selected_account_id,
            ranked_insights=True,
            insight_ranking_options={"max_count": insight_card_limit},
            merchant_id=selected_merchant_id,
            merchant_query=merchant_query,
        )

    category_comparison = build_category_comparison(selected_years, category_rows, selected_baseline_year)
    monthly_spending = build_monthly_spending(selected_years, monthly_rows)
    monthly_spending_comparison = build_monthly_spending_comparison(
        selected_years,
        monthly_spending,
        selected_baseline_year,
    )
    monthly_spending_statistics = build_monthly_spending_statistics(selected_years, monthly_rows)
    year_unknown_warning = (
        build_year_unknown_warning(category_comparison, unknown_category)
        if selected_analysis_mode == ANALYSIS_MODE_SPENDING
        else None
    )

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
        selected_account_id=selected_account_id,
        selected_account_name=selected_account_name,
        account_options=account_options,
        selected_merchant_id=selected_merchant_id,
        merchant_query=merchant_query,
        selected_merchant_label=selected_merchant_label,
        merchant_suggestion_limit=merchant_suggestion_limit,
        selected_comparison_view=selected_comparison_view,
        selected_analysis_mode=selected_analysis_mode,
        selected_analysis_mode_option=selected_analysis_mode_option,
        analysis_mode_options=list(ANALYSIS_MODE_OPTIONS.values()),
        merchant_table_limit=merchant_table_limit,
        comparison_insight_card_limit=insight_card_limit,
        period_filter_context=build_period_filter_context(
            PERIOD_COMPARISON_OPTIONS[selected_period_comparison],
            selected_period_categories,
            selected_period_tags,
            selected_account_name,
            selected_merchant_label,
            selected_analysis_mode_option["noun"],
        ),
        year_filter_context=build_year_filter_context(
            selected_years,
            selected_baseline_year,
            selected_year_categories,
            selected_year_tags,
            selected_account_name,
            selected_merchant_label,
            selected_analysis_mode_option["noun"],
        ),
        period_clear_url=build_comparison_url(
            comparison_view="period",
            years=selected_years,
            baseline_year=selected_baseline_year,
            year_categories=selected_year_categories,
            year_tags=selected_year_tags,
            account_id=selected_account_id,
            merchant_id=selected_merchant_id,
            merchant_query=merchant_query,
            analysis_mode=selected_analysis_mode,
        ),
        year_clear_url=build_comparison_url(
            comparison_view="year",
            period_comparison=selected_period_comparison,
            period_categories=selected_period_categories,
            period_tags=selected_period_tags,
            account_id=selected_account_id,
            merchant_id=selected_merchant_id,
            merchant_query=merchant_query,
            analysis_mode=selected_analysis_mode,
        ),
        period_comparison_options=[
            {"value": value, "label": label} for value, label in PERIOD_COMPARISON_OPTIONS.items()
        ],
        selected_period_comparison=selected_period_comparison,
        period_comparison=period_comparison,
        category_comparison=category_comparison,
        year_unknown_warning=year_unknown_warning,
        month_labels=month_abbreviation_labels(),
        monthly_spending=monthly_spending,
        monthly_spending_comparison=monthly_spending_comparison,
        monthly_spending_statistics=monthly_spending_statistics,
        monthly_spending_json=[
            {
                "year": year,
                "totals": monthly_spending[year],
            }
            for year in selected_years
        ],
        monthly_spending_statistics_json=monthly_spending_statistics,
    )


def build_period_comparison(
    conn: Any,
    comparison_key: str,
    selected_categories: list[str],
    selected_tags: list[str],
    unknown_category: str,
    merchant_table_limit: int,
    analysis_mode: str = ANALYSIS_MODE_SPENDING,
    *,
    account_id: int | None = None,
    merchant_id: int | None = None,
    merchant_query: str = "",
    ranked_insights: bool = False,
    insight_ranking_options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build period comparison."""
    ranges = period_comparison_ranges(comparison_key, date.today())
    analysis_mode_option = ANALYSIS_MODE_OPTIONS[analysis_mode]
    analysis_noun = str(analysis_mode_option["noun"])
    positive_tone = str(analysis_mode_option["positive_tone"])
    category_filters = build_category_conditions(
        selected_categories,
        selected_tags,
        unknown_category,
        account_id,
        merchant_id,
        merchant_query,
    )
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
        fetch_period_category_analysis(
            conn,
            ranges["current_start"],
            ranges["current_end"],
            category_filters,
            unknown_category,
            analysis_mode,
            include_transfer_credits=include_transfer_credits,
        ),
        fetch_period_category_analysis(
            conn,
            ranges["previous_start"],
            ranges["previous_end"],
            category_filters,
            unknown_category,
            analysis_mode,
            include_transfer_credits=include_transfer_credits,
        ),
        analysis_noun,
        positive_tone,
    )
    merchant_rows = build_period_merchant_rows(
        fetch_period_merchant_transactions(
            conn,
            ranges["current_start"],
            ranges["current_end"],
            category_filters,
            unknown_category,
            analysis_mode,
            include_transfer_credits=include_transfer_credits,
        ),
        fetch_period_merchant_transactions(
            conn,
            ranges["previous_start"],
            ranges["previous_end"],
            category_filters,
            unknown_category,
            analysis_mode,
            include_transfer_credits=include_transfer_credits,
        ),
        conn,
        analysis_noun,
        positive_tone,
    )

    totals = [
        build_period_metric(
            "Spending",
            current_summary["spending"],
            previous_summary["spending"],
            "spending",
            ranges["previous_short_label"],
            "money",
            positive_tone="danger",
        ),
        build_period_metric(
            "Income and Credits",
            current_summary["income"],
            previous_summary["income"],
            "income",
            ranges["previous_short_label"],
            "money",
            positive_tone="success",
        ),
        build_period_metric(
            "Net cash flow",
            current_summary["income"] - current_summary["spending"],
            previous_summary["income"] - previous_summary["spending"],
            "net cash flow",
            ranges["previous_short_label"],
            "money",
            positive_tone="success",
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
    unknown_warning = None
    if analysis_mode == ANALYSIS_MODE_SPENDING:
        unknown_warning = build_period_unknown_warning(
            category_rows,
            current_summary["spending"],
            previous_summary["spending"],
            unknown_category,
        )
    category_history = None
    merchant_history = None
    merchant_activity_history = None
    if ranked_insights:
        category_history = build_period_category_history(
            fetch_historical_monthly_category_analysis(
                conn,
                ranges["current_start"],
                category_filters,
                unknown_category,
                analysis_mode,
                include_transfer_credits=include_transfer_credits,
            )
        )
        historical_merchant_rows = fetch_historical_monthly_merchant_transactions(
            conn,
            ranges["current_start"],
            category_filters,
            unknown_category,
            analysis_mode,
            include_transfer_credits=include_transfer_credits,
        )
        merchant_history = build_period_merchant_history(
            historical_merchant_rows,
            conn,
        )
        merchant_activity_history = build_period_merchant_activity_history(
            historical_merchant_rows,
            conn,
            ranges["current_start"],
        )

    insights = build_period_insights(
        category_rows,
        merchant_rows,
        current_summary,
        previous_summary,
        analysis_noun=analysis_noun,
        spending_insights=analysis_mode == ANALYSIS_MODE_SPENDING,
        category_history=category_history,
        merchant_history=merchant_history,
        merchant_activity_history=merchant_activity_history,
        ranked=ranked_insights,
        ranking_options=insight_ranking_options,
    )

    return {
        **ranges,
        "option_label": PERIOD_COMPARISON_OPTIONS[comparison_key],
        "totals": totals,
        "analysis_mode": analysis_mode,
        "analysis_mode_option": analysis_mode_option,
        "category_rows": category_rows[:12],
        "merchant_rows": merchant_rows[:merchant_table_limit],
        "insights": insights,
        "insight_groups": build_period_insight_groups(insights),
        "unknown_warning": unknown_warning,
        "current_transaction_count": current_summary["transaction_count"],
        "previous_transaction_count": previous_summary["transaction_count"],
    }


def selected_account_option_name(account_options: list[dict[str, Any]], selected_account_id: int | None) -> str:
    """Return the selected account display name for filter summaries."""
    if selected_account_id is None:
        return ""
    for account in account_options:
        if int(account["id"]) == selected_account_id:
            return str(account["name"])
    return ""


def selected_merchant_option_name(conn: Any, selected_merchant_id: int | None, merchant_query: str = "") -> str:
    """Return the selected merchant display name for filter summaries."""
    if selected_merchant_id is None:
        return merchant_query
    merchant = find_merchant_by_id(conn, selected_merchant_id)
    if merchant is None:
        return merchant_query
    return str(merchant["merchant_key"])
