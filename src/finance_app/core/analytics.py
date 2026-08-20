"""Shared analytics constants and view-model helpers.

Dashboard, Reports, and pinned report views use the same classification scopes,
measure labels, cash-flow summary rules, and data-quality summaries. This module
owns those cross-feature contracts without depending on feature packages.
"""

from collections.abc import Mapping
from typing import Any

from finance_app.core.i18n import gettext
from finance_app.core.money import format_signed_money_display, rounded_money_float

QUICK_VIEW_ALL = "all"
QUICK_VIEW_NEEDS_REVIEW = "needs_review"
QUICK_VIEW_UNKNOWN = "unknown"
QUICK_VIEW_CATEGORIZED = "categorized"
QUICK_VIEW_CUSTOM = "custom"
QUICK_VIEW_OPTIONS = {
    QUICK_VIEW_ALL,
    QUICK_VIEW_NEEDS_REVIEW,
    QUICK_VIEW_UNKNOWN,
    QUICK_VIEW_CATEGORIZED,
    QUICK_VIEW_CUSTOM,
}

REPORT_MEASURE_SPENDING = "spending"
REPORT_MEASURE_INCOME = "income"
REPORT_MEASURE_NET = "net"
REPORT_MEASURES = (
    REPORT_MEASURE_SPENDING,
    REPORT_MEASURE_INCOME,
    REPORT_MEASURE_NET,
)
REPORT_MEASURE_OPTIONS = (
    {"value": REPORT_MEASURE_SPENDING, "label": "Spending"},
    {"value": REPORT_MEASURE_INCOME, "label": "Income and credits"},
    {"value": REPORT_MEASURE_NET, "label": "Net cash flow"},
)

REPORT_BASIS_CASH_FLOW = "cash_flow"
REPORT_BASIS_LEDGER = "ledger"
REPORT_BASES = (
    REPORT_BASIS_CASH_FLOW,
    REPORT_BASIS_LEDGER,
)
REPORT_BASIS_OPTIONS = (
    {
        "value": REPORT_BASIS_CASH_FLOW,
        "label": "Reportable cash flow",
        "description": "Transfers, payments, and reimbursement credits are excluded from ordinary reporting.",
    },
    {
        "value": REPORT_BASIS_LEDGER,
        "label": "Ledger rows",
        "description": "All active ledger rows are included, including payments and transfers.",
    },
)


def build_quick_view_options(active_view: str, counts: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Build shared quick-view options from classification counters."""
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
    options = [option for option in options if option["count"] > 0 or option["value"] == active_view]

    for option in options:
        option["active"] = option["value"] == active_view

    return options


def build_cash_flow_summary(total_income: float, total_spending: float) -> dict[str, Any]:
    """Build shared cash-flow summary metadata."""
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


def build_data_quality(summary: Mapping[str, Any]) -> dict[str, Any]:
    """Build shared classification and tagging quality summary metadata."""
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


def format_money(value: float) -> str:
    """Return a money label for server-rendered analytics details."""
    return format_signed_money_display(value).lstrip("+")


def percentage(count: float, total: float) -> float:
    """Return a one-decimal percentage, or zero when the denominator is empty."""
    if not total:
        return 0

    return round((count / total) * 100, 1)
