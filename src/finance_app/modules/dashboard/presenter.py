"""View-model builders for the dashboard feature."""

from collections.abc import Mapping, MutableMapping, Sequence
from typing import Any
from urllib.parse import urlencode

from flask import url_for

from finance_app.core.constants import FILTER_MODE_INCLUDE
from finance_app.core.i18n import gettext
from finance_app.core.money import format_signed_money_display, rounded_money_float
from finance_app.core.periods import DatePeriod
from finance_app.modules.categories.sources import (
    CATEGORY_SOURCE_AI,
    CATEGORY_SOURCE_HISTORY,
    CATEGORY_SOURCE_RULE,
)
from finance_app.modules.categories.tag_filters import UNTAGGED_TAG_FILTER
from finance_app.modules.comparison.urls import build_comparison_url
from finance_app.modules.reports.urls import build_reports_url
from finance_app.modules.transactions.constants import (
    AMOUNT_TYPE_INCOME,
    AMOUNT_TYPE_SPENDING,
    CATEGORY_SOURCE_FILTER_MANUAL_REVIEWED,
    CATEGORY_STATUS_CATEGORIZED,
    CATEGORY_STATUS_UNKNOWN,
    REVIEW_FILTER_NEEDS_REVIEW,
)

from .constants import (
    QUICK_VIEW_ALL,
    QUICK_VIEW_CATEGORIZED,
    QUICK_VIEW_NEEDS_REVIEW,
    QUICK_VIEW_UNKNOWN,
)
from .urls import dashboard_transactions_url


def build_quick_view_options(active_view: str, counts: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Build quick view options."""
    options: list[dict[str, Any]] = [
        {
            "value": QUICK_VIEW_CATEGORIZED,
            "label": "Categorized",
            "count": counts["categorized_count"],
        },
        {
            "value": QUICK_VIEW_NEEDS_REVIEW,
            "label": "Needs review",
            "count": counts["needs_review_count"],
        },
        {
            "value": QUICK_VIEW_UNKNOWN,
            "label": "Unknown",
            "count": counts["unknown_count"],
        },
        {
            "value": QUICK_VIEW_ALL,
            "label": "All",
            "count": counts["all_count"],
        },
    ]
    options = [option for option in options if option["count"] > 0]

    for option in options:
        option["active"] = option["value"] == active_view

    return options


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


def build_cash_flow_summary(total_income: float, total_spending: float) -> dict[str, Any]:
    """Build cash flow summary."""
    net_cashflow = round(total_income - total_spending, 2)
    if net_cashflow > 0:
        status = "surplus"
        net_detail = gettext("Surplus: income is higher than spending.")
    elif net_cashflow < 0:
        status = "deficit"
        net_detail = gettext("Deficit: spending is higher than income.")
    else:
        status = "balanced"
        net_detail = gettext("Balanced: income and spending are equal.")

    if total_income > 0:
        savings_rate = round((net_cashflow / total_income) * 100, 1)
        savings_rate_label = f"{savings_rate}%"
        spending_rate = round((total_spending / total_income) * 100, 1)
        savings_detail = gettext("Spending is {rate}% of income.", rate=spending_rate)
    else:
        savings_rate = None
        savings_rate_label = "n/a"
        savings_detail = gettext("No income in this view.")

    return {
        "status": status,
        "income_detail": gettext("Credits in the selected period."),
        "spending_detail": gettext("Outflows in the selected period."),
        "net_cashflow": net_cashflow,
        "net_detail": net_detail,
        "savings_rate": savings_rate,
        "savings_rate_label": savings_rate_label,
        "savings_detail": savings_detail,
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


def build_data_quality(summary: Mapping[str, Any]) -> dict[str, Any]:
    """Build data quality."""
    transaction_count = summary["transaction_count"] or 0
    categorized_count = summary["categorized_count"] or 0
    uncategorized_count = summary["uncategorized_count"] or 0
    unknown_needs_review_count = summary["unknown_needs_review_count"] or 0
    needs_review_count = summary["needs_review_count"] or 0
    untagged_count = summary.get("untagged_count", summary.get("untagged_spending_count", 0)) or 0
    untagged_spending_total = rounded_money_float(summary.get("untagged_spending_total", 0))
    unknown_spending_total = rounded_money_float(summary.get("unknown_spending_total", 0))
    unknown_income_total = rounded_money_float(summary.get("unknown_income_total", 0))
    manually_reviewed_count = summary["manually_reviewed_count"] or 0
    rule_count = summary["rule_count"] or 0
    history_count = summary["history_count"] or 0
    ai_count = summary["ai_count"] or 0
    manual_source_count = summary["manual_source_count"] or 0

    categorized_rate = percentage(categorized_count, transaction_count)
    unknown_rate = percentage(uncategorized_count, transaction_count)
    needs_review_rate = percentage(needs_review_count, transaction_count)
    quality_score = round(categorized_rate)
    risk_rate = max(unknown_rate, needs_review_rate)

    if transaction_count == 0:
        level = "empty"
        message = gettext("No transactions in this view.")
    elif risk_rate >= 25:
        level = "danger"
        if needs_review_count:
            message = gettext(
                "{count} of {total} transactions need review. Category-level charts may be misleading.",
                count=needs_review_count,
                total=transaction_count,
            )
        else:
            message = gettext(
                "{count} of {total} transactions are unknown. Category-level charts may be misleading.",
                count=uncategorized_count,
                total=transaction_count,
            )
    elif risk_rate >= 10:
        level = "warning"
        if needs_review_count:
            message = gettext(
                "{count} of {total} transactions need review.",
                count=needs_review_count,
                total=transaction_count,
            )
        else:
            message = gettext(
                "{count} of {total} transactions are unknown.",
                count=uncategorized_count,
                total=transaction_count,
            )
    else:
        level = "good"
        message = gettext("Category data is ready for analysis.")

    review_count = needs_review_count + max(0, uncategorized_count - unknown_needs_review_count)
    review_label = gettext(
        (
            "Review {count} transaction needing review"
            if review_count == 1
            else "Review {count} transactions needing review"
        ),
        count=review_count,
    )
    if unknown_needs_review_count == 1:
        unknown_review_sentence = gettext("1 unknown transaction needs review.")
    elif unknown_needs_review_count:
        unknown_review_sentence = gettext(
            "{count} unknown transactions need review.",
            count=unknown_needs_review_count,
        )
    else:
        unknown_review_sentence = gettext("No unknown transactions need review.")

    driver_warning = ""
    if unknown_needs_review_count or uncategorized_count:
        driver_warning = gettext("Category and merchant drivers may be incomplete until unknown rows are reviewed.")

    readiness_metrics = [
        {
            "label": "Categorized",
            "value": f"{quality_score}%",
            "detail": gettext(
                "{count} of {total} reportable rows",
                count=categorized_count,
                total=transaction_count,
            ),
            "tone": "neutral",
        },
        {
            "label": "Unknown needing review",
            "value": unknown_needs_review_count,
            "detail": unknown_review_sentence,
            "tone": "warning" if unknown_needs_review_count else "neutral",
        },
        {
            "label": "Untagged",
            "value": untagged_count,
            "detail": gettext(
                "{amount} untagged spending",
                amount=format_money(untagged_spending_total),
            ),
            "tone": "neutral",
        },
    ]

    return {
        "transaction_count": transaction_count,
        "categorized_count": categorized_count,
        "unknown_count": uncategorized_count,
        "unknown_needs_review_count": unknown_needs_review_count,
        "unknown_rate": unknown_rate,
        "needs_review_count": needs_review_count,
        "needs_review_rate": needs_review_rate,
        "untagged_count": untagged_count,
        "untagged_spending_total": untagged_spending_total,
        "unknown_spending_total": unknown_spending_total,
        "unknown_income_total": unknown_income_total,
        "rule_count": rule_count,
        "manual_source_count": manual_source_count,
        "history_count": history_count,
        "ai_count": ai_count,
        "manual_reviewed_count": manually_reviewed_count,
        "quality_score": quality_score,
        "level": level,
        "message": message,
        "readiness_metrics": readiness_metrics,
        "unknown_review_sentence": unknown_review_sentence,
        "driver_warning": driver_warning,
        "review_label": review_label,
    }


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
        return build_reports_url("reports.taxonomy", **report_params)

    merchant_id = row.get("merchant_id")
    if merchant_id:
        return app_detail_url(
            "reports.merchant_report",
            {"merchant_id": merchant_id},
            without_keys(report_params, "merchant_id", "merchant_query"),
        )
    merchant_query = row.get("merchant_key") or row.get("label")
    if merchant_query:
        return build_reports_url(
            "reports.merchants",
            **without_keys(report_params, "merchant_id", "merchant_query"),
            merchant_query=merchant_query,
        )
    return build_reports_url("reports.merchants", **report_params)


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


def format_money(value: float) -> str:
    """Return a money label for server-rendered dashboard details."""
    return format_signed_money_display(value).lstrip("+")


def percentage(count: float, total: float) -> float:
    """Handle percentage."""
    if not total:
        return 0

    return round((count / total) * 100, 1)
