"""View-model builders for the dashboard feature."""

from collections.abc import Mapping, MutableMapping, Sequence
from typing import Any
from urllib.parse import urlencode

from flask import url_for

from finance_app.core.analytics import (
    QUICK_VIEW_ALL,
    QUICK_VIEW_CATEGORIZED,
    format_money,
    percentage,
)
from finance_app.core.constants import FILTER_MODE_INCLUDE
from finance_app.core.money import format_signed_money_display, rounded_money_float
from finance_app.core.periods import DatePeriod
from finance_app.core.urls import build_app_url
from finance_app.modules.categories.sources import (
    CATEGORY_SOURCE_AI,
    CATEGORY_SOURCE_HISTORY,
    CATEGORY_SOURCE_RULE,
)
from finance_app.modules.categories.tag_filters import UNTAGGED_TAG_FILTER
from finance_app.modules.comparison.urls import build_comparison_url
from finance_app.modules.transactions.constants import (
    AMOUNT_TYPE_INCOME,
    AMOUNT_TYPE_SPENDING,
    CATEGORY_SOURCE_FILTER_MANUAL_REVIEWED,
    CATEGORY_STATUS_CATEGORIZED,
    CATEGORY_STATUS_UNKNOWN,
    REVIEW_FILTER_NEEDS_REVIEW,
)

from .urls import dashboard_transactions_url


def build_classification_scope_options(active_view: str, counts: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Build Dashboard classification-scope filter options."""
    options: list[dict[str, Any]] = [
        {
            "value": QUICK_VIEW_CATEGORIZED,
            "label": "Categorized",
            "count": counts["categorized_count"],
        },
        {
            "value": QUICK_VIEW_ALL,
            "label": "All",
            "count": counts["all_count"],
        },
    ]
    for option in options:
        option["active"] = option["value"] == active_view
    return options


def build_dashboard_links(
    period: DatePeriod,
    date_from: str = "",
    date_to: str = "",
    quick_view: str = QUICK_VIEW_ALL,
    merchant_search: str = "",
    account_id: int | None = None,
) -> dict[str, str]:
    """Build dashboard detail links for the current reporting scope."""
    return {
        "transactions": dashboard_transactions_url(
            period,
            FILTER_MODE_INCLUDE,
            [],
            False,
            date_from,
            date_to,
            quick_view,
            merchant_search=merchant_search,
            account_id=account_id,
        ),
        "spending": dashboard_transactions_url(
            period,
            FILTER_MODE_INCLUDE,
            [],
            False,
            date_from,
            date_to,
            quick_view,
            merchant_search=merchant_search,
            account_id=account_id,
            amount_type=AMOUNT_TYPE_SPENDING,
        ),
        "income": dashboard_transactions_url(
            period,
            FILTER_MODE_INCLUDE,
            [],
            False,
            date_from,
            date_to,
            quick_view,
            merchant_search=merchant_search,
            account_id=account_id,
            amount_type=AMOUNT_TYPE_INCOME,
        ),
        "unknown": dashboard_transactions_url(
            period,
            FILTER_MODE_INCLUDE,
            [],
            False,
            date_from,
            date_to,
            quick_view,
            merchant_search=merchant_search,
            account_id=account_id,
            category_status=CATEGORY_STATUS_UNKNOWN,
        ),
        "categorized": dashboard_transactions_url(
            period,
            FILTER_MODE_INCLUDE,
            [],
            False,
            date_from,
            date_to,
            quick_view,
            merchant_search=merchant_search,
            account_id=account_id,
            category_status=CATEGORY_STATUS_CATEGORIZED,
        ),
        "needs_review": dashboard_transactions_url(
            period,
            FILTER_MODE_INCLUDE,
            [],
            False,
            date_from,
            date_to,
            quick_view,
            merchant_search=merchant_search,
            account_id=account_id,
            review=REVIEW_FILTER_NEEDS_REVIEW,
        ),
        "review": url_for("review.review"),
        "upload": url_for("upload.upload"),
    }


def attach_data_quality_urls(
    data_quality: MutableMapping[str, Any],
    period: DatePeriod,
    date_from: str = "",
    date_to: str = "",
    quick_view: str = QUICK_VIEW_ALL,
    merchant_search: str = "",
    account_id: int | None = None,
) -> None:
    """Attach data quality URLs."""
    reporting_quick_view = QUICK_VIEW_ALL
    urls = {
        "Categorized": dashboard_transactions_url(
            period,
            FILTER_MODE_INCLUDE,
            [],
            False,
            date_from,
            date_to,
            reporting_quick_view,
            merchant_search=merchant_search,
            account_id=account_id,
            category_status=CATEGORY_STATUS_CATEGORIZED,
        ),
        "Needs review": dashboard_transactions_url(
            period,
            FILTER_MODE_INCLUDE,
            [],
            False,
            date_from,
            date_to,
            reporting_quick_view,
            merchant_search=merchant_search,
            account_id=account_id,
            review=REVIEW_FILTER_NEEDS_REVIEW,
        ),
        "Unknown": dashboard_transactions_url(
            period,
            FILTER_MODE_INCLUDE,
            [],
            False,
            date_from,
            date_to,
            reporting_quick_view,
            merchant_search=merchant_search,
            account_id=account_id,
            category_status=CATEGORY_STATUS_UNKNOWN,
        ),
        "Manual reviewed": dashboard_transactions_url(
            period,
            FILTER_MODE_INCLUDE,
            [],
            False,
            date_from,
            date_to,
            reporting_quick_view,
            merchant_search=merchant_search,
            account_id=account_id,
            category_source=CATEGORY_SOURCE_FILTER_MANUAL_REVIEWED,
        ),
        "By rule": dashboard_transactions_url(
            period,
            FILTER_MODE_INCLUDE,
            [],
            False,
            date_from,
            date_to,
            reporting_quick_view,
            merchant_search=merchant_search,
            account_id=account_id,
            category_source=CATEGORY_SOURCE_RULE,
        ),
        "By similarity": dashboard_transactions_url(
            period,
            FILTER_MODE_INCLUDE,
            [],
            False,
            date_from,
            date_to,
            reporting_quick_view,
            merchant_search=merchant_search,
            account_id=account_id,
            category_source=CATEGORY_SOURCE_HISTORY,
        ),
        "By AI": dashboard_transactions_url(
            period,
            FILTER_MODE_INCLUDE,
            [],
            False,
            date_from,
            date_to,
            reporting_quick_view,
            merchant_search=merchant_search,
            account_id=account_id,
            category_source=CATEGORY_SOURCE_AI,
        ),
    }
    transactions_url = dashboard_transactions_url(
        period,
        FILTER_MODE_INCLUDE,
        [],
        False,
        date_from,
        date_to,
        reporting_quick_view,
        merchant_search=merchant_search,
        account_id=account_id,
    )
    untagged_url = dashboard_transactions_url(
        period,
        FILTER_MODE_INCLUDE,
        [],
        False,
        date_from,
        date_to,
        reporting_quick_view,
        selected_tags=[UNTAGGED_TAG_FILTER],
        merchant_search=merchant_search,
        account_id=account_id,
    )

    data_quality["categorized_url"] = urls["Categorized"]
    data_quality["needs_review_url"] = urls["Needs review"]
    data_quality["review_url"] = url_for("review.review")
    data_quality["unknown_url"] = urls["Unknown"]
    data_quality["transactions_url"] = transactions_url
    data_quality["untagged_url"] = untagged_url
    metric_urls = {
        "Categorized": urls["Categorized"],
        "Unknown needing review": data_quality["review_url"],
        "Untagged": untagged_url,
    }
    for metric in data_quality["readiness_metrics"]:
        metric["url"] = metric_urls.get(metric["label"], transactions_url)

    data_quality["detail_rows"] = [
        {"label": "Reportable rows", "value": data_quality["transaction_count"], "url": transactions_url},
        {"label": "Categorized rows", "value": data_quality["categorized_count"], "url": urls["Categorized"]},
        {"label": "Unknown rows", "value": data_quality["unknown_count"], "url": urls["Unknown"]},
        {"label": "Needs-review rows", "value": data_quality["needs_review_count"], "url": urls["Needs review"]},
        {"label": "Untagged rows", "value": data_quality["untagged_count"], "url": untagged_url},
    ]
    data_quality["impact_rows"] = [
        {
            "label": "Unknown spending amount",
            "value": format_money(data_quality["unknown_spending_total"]),
            "url": urls["Unknown"],
        },
        {
            "label": "Unknown income/credit amount",
            "value": format_money(data_quality["unknown_income_total"]),
            "url": urls["Unknown"],
        },
        {
            "label": "Untagged spending amount",
            "value": format_money(data_quality["untagged_spending_total"]),
            "url": untagged_url,
        },
    ]
    data_quality["source_rows"] = [
        {"label": "Rule", "value": data_quality["rule_count"], "url": urls["By rule"]},
        {"label": "Manual", "value": data_quality["manual_source_count"], "url": urls["Manual reviewed"]},
        {"label": "Similarity", "value": data_quality["history_count"], "url": urls["By similarity"]},
        {"label": "AI", "value": data_quality["ai_count"], "url": urls["By AI"]},
    ]


def build_dashboard_chart_data(monthly_rows: Sequence[Mapping[str, Any]]) -> dict[str, list[Any]]:
    """Build compact Dashboard trend chart JSON."""
    return {
        "spendingIncomeMonthLabels": [row["label"] for row in monthly_rows],
        "spendingIncomeSpendingTotals": [rounded_money_float(row["spending"]) for row in monthly_rows],
        "spendingIncomeIncomeTotals": [rounded_money_float(row["income"]) for row in monthly_rows],
        "netMonthLabels": [row["label"] for row in monthly_rows],
        "netMonthTotals": [rounded_money_float(row["net"]) for row in monthly_rows],
    }


def build_top_driver_previews(
    category_rows: Sequence[Mapping[str, Any]],
    merchant_rows: Sequence[Mapping[str, Any]],
    change_rows: Sequence[Mapping[str, Any]],
    total_spending: float,
    report_params: Mapping[str, object],
    comparison_params: Mapping[str, object],
) -> dict[str, Any]:
    """Build compact top-driver preview rows for the Dashboard."""
    return {
        "categories": build_driver_rows(category_rows, total_spending, "category", report_params),
        "merchants": build_driver_rows(merchant_rows, total_spending, "merchant", report_params),
        "changes": build_change_rows(change_rows, comparison_params),
    }


def build_driver_rows(
    rows: Sequence[Mapping[str, Any]],
    total_spending: float,
    row_kind: str,
    report_params: Mapping[str, object],
) -> list[dict[str, Any]]:
    """Return compact spending-driver rows with Reports destinations."""
    prepared: list[dict[str, Any]] = []
    max_value = max((abs(rounded_money_float(row.get("spending"))) for row in rows), default=0)
    for row in rows:
        spending = rounded_money_float(row.get("spending"))
        prepared.append(
            {
                "label": str(row.get("label") or ""),
                "amount": spending,
                "transaction_count": int(row.get("transaction_count") or 0),
                "share": percentage(spending, total_spending),
                "bar_width": percentage(abs(spending), max_value),
                "url": driver_report_url(row_kind, row, report_params),
                "action_label": "View report",
            }
        )
    return prepared


def driver_report_url(row_kind: str, row: Mapping[str, Any], report_params: Mapping[str, object]) -> str:
    """Return a Reports URL for a Dashboard preview driver row."""
    if row_kind == "category":
        category_id = row.get("category_id")
        if category_id:
            return app_detail_url(
                "reports.category_report",
                {"category_id": category_id},
                report_params,
            )
        return build_app_url("reports.taxonomy", **report_params)

    merchant_id = row.get("merchant_id")
    if merchant_id:
        return app_detail_url(
            "reports.merchant_report",
            {"merchant_id": merchant_id},
            without_keys(report_params, "merchant_id", "merchant_query"),
        )
    merchant_query = row.get("merchant_key") or row.get("label")
    if merchant_query:
        return build_app_url(
            "reports.merchants",
            **without_keys(report_params, "merchant_id", "merchant_query"),
            merchant_query=merchant_query,
        )
    return build_app_url("reports.merchants", **report_params)


def build_change_rows(
    rows: Sequence[Mapping[str, Any]],
    comparison_params: Mapping[str, object],
) -> list[dict[str, Any]]:
    """Return compact current-versus-previous change rows."""
    max_value = max((abs(rounded_money_float(row.get("change"))) for row in rows), default=0)
    prepared: list[dict[str, Any]] = []
    for row in rows:
        change = rounded_money_float(row.get("change"))
        current = rounded_money_float(row.get("current"))
        previous = rounded_money_float(row.get("previous"))
        prepared.append(
            {
                "label": str(row.get("label") or ""),
                "kind": str(row.get("kind") or ""),
                "current": current,
                "previous": previous,
                "change": change,
                "change_label": format_signed_money(change),
                "direction": "up" if change > 0 else "down" if change < 0 else "flat",
                "bar_width": percentage(abs(change), max_value),
                "url": change_comparison_url(row, comparison_params),
                "kind_label": "Category" if row.get("kind") == "category" else "Merchant",
                "action_label": "Compare",
            }
        )
    return prepared


def change_comparison_url(row: Mapping[str, Any], comparison_params: Mapping[str, object]) -> str:
    """Return a Comparison URL for a Dashboard change preview row."""
    params = dict(comparison_params)
    if row.get("kind") == "category":
        params["period_categories"] = [row.get("label")]
    elif row.get("kind") == "merchant":
        params.pop("merchant_id", None)
        params.pop("merchant_query", None)
        if row.get("merchant_id"):
            params["merchant_id"] = row.get("merchant_id")
        params["merchant_query"] = row.get("merchant_key") or row.get("label")
    return build_comparison_url(**params)


def app_detail_url(endpoint: str, route_values: dict[str, Any], params: Mapping[str, object]) -> str:
    """Build a route-value URL with cleaned query parameters."""
    cleaned = {
        key: value
        for key, value in params.items()
        if value not in (None, "")
        and not (isinstance(value, (list, tuple)) and not [item for item in value if item not in (None, "")])
    }
    query = urlencode(cleaned, doseq=True)
    base_url = url_for(endpoint, **route_values)
    return f"{base_url}?{query}" if query else base_url


def without_keys(params: Mapping[str, object], *keys: str) -> dict[str, object]:
    """Return URL parameters without the supplied keys."""
    excluded = set(keys)
    return {key: value for key, value in params.items() if key not in excluded}


def format_signed_money(value: float) -> str:
    """Return a signed money label using the configured browser/server format style."""
    return format_signed_money_display(value)
