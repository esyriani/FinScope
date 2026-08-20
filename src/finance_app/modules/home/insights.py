"""Quick-insight and URL helpers for the Home feature.

The Home service assembles the command-center context. This module owns compact
comparison-derived insights plus the URL/date formatting helpers shared by Home
activity rows.
"""

from typing import Any
from urllib.parse import urlencode

from flask import has_request_context

from finance_app.core.constants import FILTER_MODE_INCLUDE
from finance_app.core.i18n import gettext
from finance_app.core.money import rounded_money_float
from finance_app.core.periods import PERIOD_CUSTOM
from finance_app.modules.comparison.service import build_period_comparison
from finance_app.modules.comparison.urls import build_comparison_url
from finance_app.modules.dashboard.urls import dashboard_transactions_url
from finance_app.modules.transactions.constants import AMOUNT_TYPE_SPENDING, IGNORED_FILTER_ACTIVE

HOME_QUICK_INSIGHT_LIMIT = 3
HOME_QUICK_INSIGHT_COMPARISON = "month_previous"


def fetch_ranked_comparison_quick_insights(conn: Any, unknown_category: Any, merchant_table_limit: Any) -> Any:
    """Return ranked comparison insight candidates adapted for Home."""
    period_context = build_period_comparison(
        conn,
        HOME_QUICK_INSIGHT_COMPARISON,
        [],
        [],
        unknown_category,
        merchant_table_limit,
        ranked_insights=True,
        insight_ranking_options={"max_count": HOME_QUICK_INSIGHT_LIMIT},
    )
    return [
        home_quick_insight_from_comparison_card(
            insight,
            period_context,
            HOME_QUICK_INSIGHT_COMPARISON,
        )
        for insight in period_context["insights"][:HOME_QUICK_INSIGHT_LIMIT]
    ]


def home_quick_insight_from_comparison_card(card: Any, period_context: Any, comparison_key: Any) -> Any:
    """Adapt a comparison insight card to the compact Home quick-insight row."""
    return {
        **card,
        "value": card.get("summary") or card.get("value") or "",
        "value_type": "text",
        "detail": home_quick_insight_detail(card),
        "detail_is_user_data": True,
        "href": home_quick_insight_href(card, period_context, comparison_key),
    }


def home_quick_insight_detail(card: Any) -> Any:
    """Return a short detail line for a Home insight row."""
    entity = insight_entity(card)
    title = card.get("title") or ""
    badge = card.get("badge") or ""
    if comparison_card_has_entity(card):
        if entity != title:
            return f"{entity} \u00b7 {badge or gettext(title)}"
        if badge:
            return f"{entity} \u00b7 {badge}"
        return entity
    if title and badge:
        return f"{gettext(title)} \u00b7 {badge}"
    if title:
        return gettext(title)
    return card.get("detail") or ""


def comparison_card_has_entity(card: Any) -> Any:
    """Return whether a comparison card detail contains category or merchant user data."""
    group = card.get("group")
    insight_type = str(card.get("insight_type") or "")
    return bool(insight_entity(card)) and (
        group in ("categories", "merchants") or insight_type.startswith(("category_", "merchant_"))
    )


def home_quick_insight_href(card: Any, period_context: Any, comparison_key: Any) -> Any:
    """Return the most useful existing page link for a Home insight card."""
    group = card.get("group")
    insight_type = str(card.get("insight_type") or "")
    entity = insight_entity(card)
    if group == "merchants" or insight_type.startswith("merchant_"):
        return current_period_transactions_url(
            period_context,
            merchant_key=entity,
        )
    if group == "categories" or insight_type.startswith("category_"):
        return comparison_period_url(
            comparison_key,
            categories=[entity] if entity else None,
        )
    return comparison_period_url(comparison_key)


def insight_entity(card: Any) -> Any:
    """Return the category or merchant entity represented by a comparison card."""
    metrics = card.get("selection_metrics") or {}
    if metrics.get("entity_key"):
        return metrics["entity_key"]
    if card.get("merchant_behavior", {}).get("merchant"):
        return card["merchant_behavior"]["merchant"]

    title = str(card.get("title") or "")
    if ":" in title:
        return title.split(":", 1)[0].strip()
    return title


def comparison_period_url(comparison_key: Any, categories: Any = None) -> Any:
    """Return a comparison URL for the Home insight preview."""
    params = {"period_comparison": comparison_key}
    if categories:
        params["period_categories"] = categories
    if has_request_context():
        return build_comparison_url(**params)
    return query_url("/comparison", **params)


def current_period_transactions_url(period_context: Any, *, merchant_key: Any = "") -> Any:
    """Return a current-period transactions URL for merchant insight drill-downs."""
    date_from = period_context["current_start"]
    date_to = period_context["current_end"]
    if has_request_context():
        return dashboard_transactions_url(
            PERIOD_CUSTOM,
            FILTER_MODE_INCLUDE,
            [],
            include_category_filter=False,
            date_from=date_from,
            date_to=date_to,
            amount_type=AMOUNT_TYPE_SPENDING,
            merchant_key=merchant_key,
        )
    return query_url(
        "/transactions",
        period=PERIOD_CUSTOM,
        ignored=IGNORED_FILTER_ACTIVE,
        date_from=date_from,
        date_to=date_to,
        amount_type=AMOUNT_TYPE_SPENDING,
        merchant_key=merchant_key,
    )


def build_quick_insights(
    overview: Any,
    latest_statement: Any,
    statement_count: Any,
    top_categories: Any,
    recurring_summary: Any,
    permissions: Any,
    comparison_quick_insights: Any = None,
) -> Any:
    """Return compact insight rows that avoid dashboard-style analytics."""
    fallback_insights = build_operational_quick_insights(
        overview,
        latest_statement,
        statement_count,
        top_categories,
        recurring_summary,
        permissions,
    )
    insights = list(comparison_quick_insights or [])
    insights.extend(fallback_insights)
    return insights[:HOME_QUICK_INSIGHT_LIMIT]


def build_operational_quick_insights(
    overview: Any,
    latest_statement: Any,
    statement_count: Any,
    top_categories: Any,
    recurring_summary: Any,
    permissions: Any,
) -> Any:
    """Return fallback operational quick-insight rows for sparse ledgers."""
    insights = []
    insights.append(
        {
            "label": "Latest transaction",
            "value": overview["latest_tx_date"] or "",
            "value_type": "date" if overview["latest_tx_date"] else "empty",
            "detail": "Most recent active transaction.",
            "detail_is_user_data": False,
            "href": "/transactions?period=all",
            "icon": "bi-receipt",
        }
    )
    insights.append(
        {
            "label": "Statements",
            "value": statement_count,
            "value_type": "count",
            "detail": latest_statement["filename"] if latest_statement else "No statements uploaded yet.",
            "detail_is_user_data": bool(latest_statement),
            "href": "/upload" if permissions["can_import_statements"] else "/transactions?period=all",
            "icon": "bi-file-earmark-text",
        }
    )
    if top_categories:
        category = top_categories[0]
        insights.append(
            {
                "label": "Top year-to-date category",
                "value": rounded_money_float(category["total"]),
                "value_type": "money",
                "detail": category["category"],
                "detail_is_user_data": True,
                "href": query_url("/transactions", period="ytd", categories=category["category"]),
                "icon": "bi-compass",
            }
        )
    insights.append(
        {
            "label": "Recurring watchlist",
            "value": recurring_summary["overdue_count"] + recurring_summary["amount_change_count"],
            "value_type": "count",
            "detail": "Overdue or changed this month.",
            "detail_is_user_data": False,
            "href": "/recurring",
            "icon": "bi-arrow-repeat",
        }
    )
    return insights


def query_url(path: Any, **params: Any) -> Any:
    """Return a local URL with non-empty query parameters."""
    cleaned = {}
    for key, value in params.items():
        if isinstance(value, (list, tuple)):
            values = [item for item in value if item not in (None, "")]
            if values:
                cleaned[key] = values
        elif value not in (None, ""):
            cleaned[key] = value

    query = urlencode(cleaned, doseq=True)
    return f"{path}?{query}" if query else path


def date_part(value: Any) -> Any:
    """Return the ISO date portion of a database date or timestamp value."""
    if not value:
        return ""
    return str(value).replace(" ", "T").split("T", 1)[0]


def sortable_timestamp(value: Any) -> Any:
    """Return a lexicographically sortable timestamp string."""
    return str(value or "").replace(" ", "T")


def recurring_status_title(status: Any) -> Any:
    """Return the display label for recurring activity status values."""
    return {
        "occurred": "Occurred",
        "amount_changed": "Amount changed",
        "likely_occurred": "Likely occurred",
        "matched": "Likely occurred",
        "expected": "Expected",
        "overdue": "Overdue",
        "possibly_inactive": "Possibly inactive",
    }.get(status, str(status or "").replace("_", " ").title())
