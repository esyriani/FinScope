"""View-model builders for the dashboard feature."""

from flask import url_for

from finance_app.core.i18n import gettext
from finance_app.core.money import format_money_display, money_to_float, rounded_money_float
from finance_app.modules.categories.sources import (
    CATEGORY_SOURCE_AI,
    CATEGORY_SOURCE_HISTORY,
    CATEGORY_SOURCE_MANUAL,
    CATEGORY_SOURCE_RULE,
)
from finance_app.modules.categories.service import (
    get_category_rules,
    normalize_merchant_description,
    rule_amount_matches,
)
from finance_app.modules.merchants.repository import merchant_identity_from_row
from finance_app.modules.transactions.constants import (
    AMOUNT_TYPE_CREDIT,
    AMOUNT_TYPE_INCOME,
    AMOUNT_TYPE_SPENDING,
    CATEGORY_SOURCE_FILTER_MANUAL_REVIEWED,
    CATEGORY_STATUS_CATEGORIZED,
    CATEGORY_STATUS_UNKNOWN,
    REVIEW_FILTER_NEEDS_REVIEW,
    REVIEW_FILTER_VERIFIED,
)
from .urls import app_url, dashboard_transactions_url
from .constants import (
    DASHBOARD_BREAKDOWN_TAG,
    DASHBOARD_CATEGORY_SORT_CATEGORY,
    DASHBOARD_CATEGORY_SORT_SHARE,
    DASHBOARD_CATEGORY_SORT_SPENDING,
    DASHBOARD_MERCHANT_SORT_CATEGORY,
    DASHBOARD_MERCHANT_SORT_MERCHANT,
    DASHBOARD_MERCHANT_SORT_PERIOD_CHANGE,
    DASHBOARD_MERCHANT_SORT_RULES,
    DASHBOARD_MERCHANT_SORT_SPENDING,
    DASHBOARD_MERCHANT_SORT_TRANSACTIONS,
    QUICK_VIEW_ALL,
    QUICK_VIEW_CATEGORIZED,
    QUICK_VIEW_NEEDS_REVIEW,
    QUICK_VIEW_UNKNOWN,
)


def build_quick_view_options(active_view, counts):
    """Build quick view options."""
    options = [
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

    for option in options:
        option["active"] = option["value"] == active_view

    return options


def build_dashboard_links(
    period,
    filter_mode,
    selected_categories,
    selected_tags=None,
    date_from="",
    date_to="",
    quick_view=QUICK_VIEW_ALL,
    include_transfer_credits=False,
    merchant_search="",
):
    """Build dashboard drill-down links for the current reporting scope."""
    selected_tags = selected_tags or []
    credit_amount_type = AMOUNT_TYPE_CREDIT if include_transfer_credits else AMOUNT_TYPE_INCOME
    return {
        "transactions": dashboard_transactions_url(
            period,
            filter_mode,
            selected_categories,
            True,
            date_from,
            date_to,
            quick_view,
            selected_tags=selected_tags,
            merchant_search=merchant_search,
        ),
        "spending": dashboard_transactions_url(
            period,
            filter_mode,
            selected_categories,
            True,
            date_from,
            date_to,
            quick_view,
            selected_tags=selected_tags,
            merchant_search=merchant_search,
            amount_type=AMOUNT_TYPE_SPENDING,
        ),
        "income": dashboard_transactions_url(
            period,
            filter_mode,
            selected_categories,
            True,
            date_from,
            date_to,
            quick_view,
            selected_tags=selected_tags,
            merchant_search=merchant_search,
            amount_type=credit_amount_type,
        ),
        "unknown": dashboard_transactions_url(
            period,
            filter_mode,
            selected_categories,
            True,
            date_from,
            date_to,
            quick_view,
            selected_tags=selected_tags,
            merchant_search=merchant_search,
            category_status=CATEGORY_STATUS_UNKNOWN,
        ),
        "categorized": dashboard_transactions_url(
            period,
            filter_mode,
            selected_categories,
            True,
            date_from,
            date_to,
            quick_view,
            selected_tags=selected_tags,
            merchant_search=merchant_search,
            category_status=CATEGORY_STATUS_CATEGORIZED,
        ),
        "needs_review": dashboard_transactions_url(
            period,
            filter_mode,
            selected_categories,
            True,
            date_from,
            date_to,
            quick_view,
            selected_tags=selected_tags,
            merchant_search=merchant_search,
            review=REVIEW_FILTER_NEEDS_REVIEW,
        ),
        "verified": dashboard_transactions_url(
            period,
            filter_mode,
            selected_categories,
            True,
            date_from,
            date_to,
            quick_view,
            selected_tags=selected_tags,
            merchant_search=merchant_search,
            review=REVIEW_FILTER_VERIFIED,
        ),
        "review": url_for("review.review"),
        "upload": url_for("upload.upload"),
    }


def build_dashboard_insights(
    summary,
    total_spending,
    period,
    filter_mode,
    selected_categories,
    selected_tags=None,
    date_from="",
    date_to="",
    quick_view=QUICK_VIEW_ALL,
    merchant_search="",
):
    """Build finance-oriented dashboard insight tiles for the current view."""
    selected_tags = selected_tags or []
    transaction_count = summary["transaction_count"] or 0
    categorized_count = summary["categorized_count"] or 0
    verified_count = summary["manually_reviewed_count"] or 0
    untagged_count = summary["untagged_spending_count"] or 0
    untagged_total = rounded_money_float(summary["untagged_spending_total"])
    top_source = top_categorization_source(summary, categorized_count)

    return {
        "average_transaction_amount": rounded_money_float(summary["average_transaction_amount"]),
        "transaction_count": transaction_count,
        "untagged_spending_count": untagged_count,
        "untagged_spending_total": untagged_total,
        "untagged_spending_rate": percentage(untagged_total, total_spending),
        "verified_count": verified_count,
        "verified_rate": percentage(verified_count, transaction_count),
        "top_source": top_source,
        "top_source_url": source_transactions_url(
            top_source["source"],
            period,
            filter_mode,
            selected_categories,
            selected_tags,
            date_from,
            date_to,
            quick_view,
            merchant_search,
        ),
    }


def top_categorization_source(summary, categorized_count):
    """Return the dominant category assignment source in a summary row."""
    candidates = [
        {
            "source": CATEGORY_SOURCE_RULE,
            "label": "Rule",
            "count": summary["rule_count"] or 0,
        },
        {
            "source": CATEGORY_SOURCE_HISTORY,
            "label": "Similarity",
            "count": summary["history_count"] or 0,
        },
        {
            "source": CATEGORY_SOURCE_AI,
            "label": "AI",
            "count": summary["ai_count"] or 0,
        },
        {
            "source": CATEGORY_SOURCE_MANUAL,
            "label": "Manual",
            "count": summary["manual_source_count"] or 0,
        },
    ]
    ordered = sorted(
        enumerate(candidates),
        key=lambda item: (item[1]["count"], -item[0]),
        reverse=True,
    )
    top = ordered[0][1] if ordered else {"source": "", "label": "n/a", "count": 0}
    if top["count"] == 0:
        top = {"source": "", "label": "n/a", "count": 0}
    return {
        **top,
        "rate": percentage(top["count"], categorized_count),
    }


def source_transactions_url(
    source,
    period,
    filter_mode,
    selected_categories,
    selected_tags,
    date_from,
    date_to,
    quick_view,
    merchant_search="",
):
    """Return a transactions URL for a category source insight."""
    if not source:
        return ""
    source_filter = CATEGORY_SOURCE_FILTER_MANUAL_REVIEWED if source == CATEGORY_SOURCE_MANUAL else source
    return dashboard_transactions_url(
        period,
        filter_mode,
        selected_categories,
        True,
        date_from,
        date_to,
        quick_view,
        selected_tags=selected_tags,
        merchant_search=merchant_search,
        category_source=source_filter,
    )


def build_cash_flow_summary(total_income, total_spending):
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
    data_quality,
    period,
    filter_mode,
    selected_categories,
    selected_tags=None,
    date_from="",
    date_to="",
    quick_view=QUICK_VIEW_ALL,
    merchant_search="",
):
    """Attach data quality URLs."""
    selected_tags = selected_tags or []
    urls = {
        "Categorized": dashboard_transactions_url(
            period,
            filter_mode,
            selected_categories,
            True,
            date_from,
            date_to,
            quick_view,
            selected_tags=selected_tags,
            merchant_search=merchant_search,
            category_status=CATEGORY_STATUS_CATEGORIZED,
        ),
        "Needs review": dashboard_transactions_url(
            period,
            filter_mode,
            selected_categories,
            True,
            date_from,
            date_to,
            quick_view,
            selected_tags=selected_tags,
            merchant_search=merchant_search,
            review=REVIEW_FILTER_NEEDS_REVIEW,
        ),
        "Unknown": dashboard_transactions_url(
            period,
            filter_mode,
            selected_categories,
            True,
            date_from,
            date_to,
            quick_view,
            selected_tags=selected_tags,
            merchant_search=merchant_search,
            category_status=CATEGORY_STATUS_UNKNOWN,
        ),
        "Manual reviewed": dashboard_transactions_url(
            period,
            filter_mode,
            selected_categories,
            True,
            date_from,
            date_to,
            quick_view,
            selected_tags=selected_tags,
            merchant_search=merchant_search,
            category_source=CATEGORY_SOURCE_FILTER_MANUAL_REVIEWED,
        ),
        "By rule": dashboard_transactions_url(
            period,
            filter_mode,
            selected_categories,
            True,
            date_from,
            date_to,
            quick_view,
            selected_tags=selected_tags,
            merchant_search=merchant_search,
            category_source=CATEGORY_SOURCE_RULE,
        ),
        "By similarity": dashboard_transactions_url(
            period,
            filter_mode,
            selected_categories,
            True,
            date_from,
            date_to,
            quick_view,
            selected_tags=selected_tags,
            merchant_search=merchant_search,
            category_source=CATEGORY_SOURCE_HISTORY,
        ),
        "By AI": dashboard_transactions_url(
            period,
            filter_mode,
            selected_categories,
            True,
            date_from,
            date_to,
            quick_view,
            selected_tags=selected_tags,
            merchant_search=merchant_search,
            category_source=CATEGORY_SOURCE_AI,
        ),
    }

    data_quality["categorized_url"] = urls["Categorized"]
    data_quality["needs_review_url"] = urls["Needs review"]
    data_quality["review_url"] = url_for("review.review")
    for group in data_quality["metric_groups"]:
        for metric in group.get("metrics", []) + group.get("compact_metrics", []):
            metric["url"] = urls.get(
                metric["label"],
                dashboard_transactions_url(
                    period,
                    filter_mode,
                    selected_categories,
                    True,
                    date_from,
                    date_to,
                    quick_view,
                    selected_tags=selected_tags,
                    merchant_search=merchant_search,
                ),
            )


def dashboard_sort_text(value):
    """Render sort text."""
    return str(value or "").casefold()


def dashboard_optional_number(value):
    """Render optional number."""
    return (value is not None, value if value is not None else 0)


def sort_merchant_rows(rows, sort, direction):
    """Sort merchant rows."""
    key_map = {
        DASHBOARD_MERCHANT_SORT_MERCHANT: lambda row: dashboard_sort_text(row["merchant"]),
        DASHBOARD_MERCHANT_SORT_CATEGORY: lambda row: dashboard_sort_text(row["category"]),
        DASHBOARD_MERCHANT_SORT_TRANSACTIONS: lambda row: row["transaction_count"],
        DASHBOARD_MERCHANT_SORT_SPENDING: lambda row: row["total"],
        DASHBOARD_MERCHANT_SORT_PERIOD_CHANGE: lambda row: dashboard_optional_number(
            row["period_change"].get("sort_value")
        ),
        DASHBOARD_MERCHANT_SORT_RULES: lambda row: len(row["rules"]),
    }
    rows.sort(key=key_map[sort], reverse=direction == "desc")


def sort_category_rows(rows, sort, direction):
    """Sort category rows."""
    key_map = {
        DASHBOARD_CATEGORY_SORT_CATEGORY: lambda row: dashboard_sort_text(row["category"]),
        DASHBOARD_CATEGORY_SORT_SPENDING: lambda row: row["total"],
        DASHBOARD_CATEGORY_SORT_SHARE: lambda row: row["share"],
    }
    rows.sort(key=key_map[sort], reverse=direction == "desc")


def build_merchant_aggregates(rows, conn=None):
    """Build merchant aggregates."""
    aggregates = {}

    for row in rows:
        merchant = merchant_identity_from_row(row, conn=conn)
        merchant_key = merchant["name"]
        if not merchant_key:
            continue

        aggregate = aggregates.setdefault(
            merchant_key,
            {
                "merchant_id": merchant["id"],
                "merchant_key": merchant_key,
                "cleaned_keys": set(),
                "examples": set(),
                "amounts": [],
                "category_totals": {},
                "category_counts": {},
                "transaction_count": 0,
                "total": 0,
            },
        )
        amount = money_to_float(row["amount"])
        category = row["category"]
        if aggregate["merchant_id"] != merchant["id"]:
            aggregate["merchant_id"] = None
        aggregate["cleaned_keys"].add(merchant["cleaned_key"])
        aggregate["examples"].add(row["description"])
        aggregate["amounts"].append(amount)
        aggregate["transaction_count"] += 1
        aggregate["total"] += amount
        aggregate["category_totals"][category] = aggregate["category_totals"].get(category, 0) + amount
        aggregate["category_counts"][category] = aggregate["category_counts"].get(category, 0) + 1

    return aggregates


def merchant_primary_category(aggregate):
    """Build primary category."""
    categories = sorted(
        aggregate["category_totals"],
        key=lambda category: (
            aggregate["category_totals"][category],
            aggregate["category_counts"][category],
            category,
        ),
        reverse=True,
    )
    if not categories:
        return {"label": "n/a", "count": 0, "total": 0}

    primary = categories[0]
    extra_count = max(0, len(categories) - 1)
    label = primary if extra_count == 0 else f"{primary} +{extra_count}"
    return {
        "label": label,
        "count": aggregate["category_counts"][primary],
        "total": aggregate["category_totals"][primary],
    }


def merchant_matching_rules(aggregate, rules):
    """Build matching rules."""
    matches = []
    merchant_key = aggregate["merchant_key"]
    amounts = aggregate["amounts"]
    candidates = [merchant_key]
    candidates.extend(
        cleaned_key
        for cleaned_key in sorted(aggregate["cleaned_keys"])
        if cleaned_key and cleaned_key not in candidates
    )

    for rule in rules:
        rule_merchant_id = rule["merchant_id"] if "merchant_id" in rule.keys() else rule.get("merchant_id")
        if rule_merchant_id is not None:
            if aggregate.get("merchant_id") is None or int(aggregate["merchant_id"]) != int(rule_merchant_id):
                continue
            if not any(rule_amount_matches(rule, amount) for amount in amounts):
                continue

            matches.append(
                {
                    "keyword": rule["keyword"],
                    "category": rule["category"],
                    "source": rule["source"],
                    "url": app_url("rules.rules", search=rule["merchant_name"] or rule["keyword"]),
                }
            )
            continue

        keyword = normalize_merchant_description(rule["keyword"])
        if not keyword or not any(keyword in candidate for candidate in candidates):
            continue
        if not any(rule_amount_matches(rule, amount) for amount in amounts):
            continue

        matches.append(
            {
                "keyword": rule["keyword"],
                "category": rule["category"],
                "source": rule["source"],
                "url": app_url("rules.rules", search=rule["keyword"]),
            }
        )

    return matches[:3]


def merchant_period_change(current_total, previous_total):
    """Build period change."""
    if previous_total is None:
        return {
            "label": "n/a",
            "detail": gettext("No comparison"),
            "direction": "flat",
            "sort_value": None,
        }

    previous_total = round(previous_total, 2)
    if previous_total == 0:
        return {
            "label": "New",
            "detail": gettext("No prior spending"),
            "direction": "up",
            "sort_value": 999999,
        }

    percent = round(((current_total - previous_total) / previous_total) * 100)
    direction = "up" if percent > 0 else "down" if percent < 0 else "flat"
    return {
        "label": f"{percent:+d}%" if percent else "0%",
        "detail": gettext("prior {amount}", amount=format_money_text(previous_total)),
        "direction": direction,
        "sort_value": percent,
    }


def format_money_text(value):
    """Format money text."""
    return format_money_display(value, places=0)


def build_category_rows(
    spending_by_category,
    total_spending,
    period,
    filter_mode,
    selected_categories,
    selected_tags=None,
    date_from="",
    date_to="",
    quick_view=QUICK_VIEW_ALL,
    merchant_search="",
    breakdown="category",
):
    """Build category or tag breakdown rows."""
    selected_tags = selected_tags or []
    rows = []

    for row in spending_by_category:
        row_data = dict(row)
        total = rounded_money_float(row_data["total"])
        is_tag_breakdown = breakdown == DASHBOARD_BREAKDOWN_TAG
        tag_name = row_data.get("tag", "")
        is_untagged = bool(row_data.get("untagged"))
        label = gettext("Untagged") if is_untagged else row_data["category"]
        if is_tag_breakdown:
            url = (
                ""
                if is_untagged
                else dashboard_transactions_url(
                    period,
                    filter_mode,
                    selected_categories,
                    True,
                    date_from,
                    date_to,
                    quick_view,
                    selected_tags=selected_tags,
                    merchant_search=merchant_search,
                    tags=[tag_name],
                    amount_type=AMOUNT_TYPE_SPENDING,
                )
            )
        else:
            url = dashboard_transactions_url(
                period,
                filter_mode,
                selected_categories,
                False,
                date_from,
                date_to,
                quick_view,
                selected_tags=selected_tags,
                merchant_search=merchant_search,
                category=row_data["category"],
                amount_type=AMOUNT_TYPE_SPENDING,
            )
        rows.append(
            {
                "category": label,
                "total": total,
                "share": round((total / total_spending) * 100, 1) if total_spending else 0,
                "url": url,
            }
        )

    max_total = max((row["total"] for row in rows), default=0)
    for row in rows:
        row["bar_width"] = round((row["total"] / max_total) * 100, 1) if max_total else 0

    return rows


def build_spending_income_series(monthly_expenses, monthly_income):
    """Build spending income series."""
    expense_by_month = {row["month"]: rounded_money_float(row["total"]) for row in monthly_expenses}
    income_by_month = {row["month"]: rounded_money_float(row["total"]) for row in monthly_income}
    months = sorted(set(expense_by_month) | set(income_by_month))

    return {
        "labels": months,
        "spending_totals": [expense_by_month.get(month, 0) for month in months],
        "income_totals": [income_by_month.get(month, 0) for month in months],
    }


def build_data_quality(summary):
    """Build data quality."""
    transaction_count = summary["transaction_count"] or 0
    categorized_count = summary["categorized_count"] or 0
    uncategorized_count = summary["uncategorized_count"] or 0
    unknown_needs_review_count = summary["unknown_needs_review_count"] or 0
    needs_review_count = summary["needs_review_count"] or 0
    manually_reviewed_count = summary["manually_reviewed_count"] or 0
    rule_count = summary["rule_count"] or 0
    history_count = summary["history_count"] or 0
    ai_count = summary["ai_count"] or 0

    categorized_rate = percentage(categorized_count, transaction_count)
    unknown_rate = percentage(uncategorized_count, transaction_count)
    needs_review_rate = percentage(needs_review_count, transaction_count)
    manually_reviewed_rate = percentage(manually_reviewed_count, transaction_count)
    rule_rate = percentage(rule_count, transaction_count)
    history_rate = percentage(history_count, transaction_count)
    ai_rate = percentage(ai_count, transaction_count)
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

    status_note = ""
    if needs_review_count and unknown_needs_review_count == needs_review_count:
        status_note = gettext("Reason: all transactions needing review are currently UNKNOWN.")
    elif needs_review_count and unknown_needs_review_count:
        status_note = gettext(
            "Reason: {unknown_count} of {review_count} transactions needing review are currently UNKNOWN.",
            unknown_count=unknown_needs_review_count,
            review_count=needs_review_count,
        )

    source_metrics = [
        {
            "label": "By rule",
            "count": rule_count,
            "rate": rule_rate,
        },
        {
            "label": "Manual reviewed",
            "count": manually_reviewed_count,
            "rate": manually_reviewed_rate,
        },
        {
            "label": "By similarity",
            "count": history_count,
            "rate": history_rate,
        },
        {
            "label": "By AI",
            "count": ai_count,
            "rate": ai_rate,
        },
    ]
    visible_source_metrics = [
        metric for metric in source_metrics if metric["count"] > 0 or metric["label"] == "By rule"
    ]
    compact_source_metrics = [
        metric for metric in source_metrics if metric["count"] == 0 and metric["label"] != "By rule"
    ]

    metric_groups = [
        {
            "label": "Categorization status",
            "note": status_note,
            "metrics": [
                {
                    "label": "Categorized",
                    "count": categorized_count,
                    "rate": categorized_rate,
                },
                {
                    "label": "Needs review",
                    "count": needs_review_count,
                    "rate": needs_review_rate,
                },
            ],
        },
        {
            "label": "Categorization source",
            "metrics": visible_source_metrics,
            "compact_metrics": compact_source_metrics,
        },
    ]
    review_count = needs_review_count + max(0, uncategorized_count - unknown_needs_review_count)
    review_label = gettext(
        (
            "Review {count} transaction needing review"
            if review_count == 1
            else "Review {count} transactions needing review"
        ),
        count=review_count,
    )

    return {
        "transaction_count": transaction_count,
        "quality_score": quality_score,
        "level": level,
        "message": message,
        "metric_groups": metric_groups,
        "review_label": review_label,
    }


def percentage(count, total):
    """Handle percentage."""
    if not total:
        return 0

    return round((count / total) * 100, 1)
