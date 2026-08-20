"""Presentation shaping helpers for the Home command-center page.

The helpers in this module convert already-loaded read models into
template-ready dictionaries. They do not query or mutate the database.
"""

from typing import Any

from finance_app.core.constants import UNKNOWN_CATEGORY
from finance_app.core.money import rounded_money_float
from finance_app.modules.home.insights import date_part, query_url, recurring_status_title, sortable_timestamp

RECENT_ACTIVITY_LIMIT = 5
SUGGESTED_ACTION_LIMIT = 4


def visible_home_attention_counts(attention_counts: Any, permissions: Any) -> Any:
    """Return Home attention counts for workflows visible to the current user."""
    counts = dict(attention_counts)
    if not permissions["can_edit_transactions"]:
        counts["needs_review"] = 0
        counts["review_groups"] = 0
    if not permissions["can_manage_rules"]:
        counts["rule_suggestions"] = 0
    if not permissions["can_import_statements"]:
        counts["failed_imports"] = 0
    if not permissions["can_manage_jobs"]:
        counts["failed_jobs"] = 0
    return counts


def build_financial_pulse(
    overview: Any, ytd_income: Any, ytd_spending: Any, ytd_cashflow: Any, attention_counts: Any
) -> Any:
    """Build the short semantic financial-state summary for Home."""
    transaction_count = overview["transaction_count"] or 0
    operational_count = open_attention_count(attention_counts)

    if transaction_count == 0:
        state = "empty"
        title = "No current-year transactions"
        summary = "Import a statement to start the financial pulse."
    elif ytd_cashflow > 0:
        state = "surplus"
        title = "Positive cash flow"
        summary = "Income is ahead of spending this year."
    elif ytd_cashflow < 0:
        state = "deficit"
        title = "Cash flow needs attention"
        summary = "Spending is ahead of income this year."
    else:
        state = "balanced"
        title = "Cash flow is balanced"
        summary = "Income and spending are even this year."

    return {
        "state": state,
        "title": title,
        "summary": summary,
        "transaction_count": transaction_count,
        "ytd_income": ytd_income,
        "ytd_spending": ytd_spending,
        "ytd_cashflow": ytd_cashflow,
        "operational_count": operational_count,
    }


def build_pulse_kpis(ytd_spending: Any, ytd_cashflow: Any, attention_counts: Any, permissions: Any) -> Any:
    """Build compact KPI cards for the command-center header."""
    return [
        {
            "label": "Year-to-date cash flow",
            "value": ytd_cashflow,
            "value_type": "money",
            "href": "/dashboard?period=ytd",
            "tone": "success" if ytd_cashflow >= 0 else "danger",
            "detail": "Income less spending.",
        },
        {
            "label": "Year-to-date spending",
            "value": ytd_spending,
            "value_type": "money",
            "href": "/transactions?period=ytd&amount_type=spending",
            "tone": "",
            "detail": "Posted outflows this year.",
        },
        {
            "label": "Open attention",
            "value": open_attention_count(attention_counts),
            "value_type": "count",
            "href": open_attention_href(attention_counts, permissions),
            "tone": "warning" if open_attention_count(attention_counts) else "success",
            "detail": "Transactions or recurring items to clear.",
        },
    ]


def open_attention_href(attention_counts: Any, permissions: Any) -> Any:
    """Return an allowed destination for the Home open-attention KPI."""
    if (
        attention_counts["unknown_transactions"]
        or attention_counts["needs_review"]
        or attention_counts["review_groups"]
    ):
        if permissions["can_edit_transactions"]:
            return "/review"
        return "/transactions?period=all&ignored=active&category_status=unknown"
    if attention_counts["overdue_recurring"] or attention_counts["amount_changes"]:
        return "/recurring"
    if attention_counts["rule_suggestions"] and permissions["can_manage_rules"]:
        return "/rules?approval=suggested"
    if attention_counts["failed_imports"] and permissions["can_import_statements"]:
        return "/upload"
    if attention_counts["failed_jobs"] and permissions["can_manage_jobs"]:
        return "/jobs"
    return "/transactions"


def open_attention_count(attention_counts: Any) -> Any:
    """Return a compact count of operational work without double-counting review rows."""
    review_count = max(
        attention_counts["unknown_transactions"],
        attention_counts["needs_review"],
    )
    return (
        review_count
        + attention_counts["overdue_recurring"]
        + attention_counts["amount_changes"]
        + attention_counts["failed_imports"]
        + attention_counts["failed_jobs"]
        + attention_counts["rule_suggestions"]
    )


def build_attention_items(attention_counts: Any, failed_imports: Any, failed_jobs: Any, permissions: Any) -> Any:
    """Build prioritized operational items that need user attention."""
    items = []
    if attention_counts["unknown_transactions"]:
        items.append(
            attention_item(
                "unknown_transactions",
                "Unknown transactions",
                (
                    "Categorize unknown rows before relying on reports."
                    if permissions["can_edit_transactions"]
                    else "Inspect uncategorized rows before relying on reports."
                ),
                attention_counts["unknown_transactions"],
                "/transactions?period=all&ignored=active&category_status=unknown",
                "bi-question-circle",
                "warning",
            )
        )
    if attention_counts["overdue_recurring"]:
        items.append(
            attention_item(
                "overdue_recurring",
                "Overdue recurring activity",
                "Review expected recurring items that have not appeared.",
                attention_counts["overdue_recurring"],
                "/recurring?statuses=overdue",
                "bi-calendar-x",
                "warning",
            )
        )
    if attention_counts["rule_suggestions"]:
        items.append(
            attention_item(
                "rule_suggestions",
                "Rule suggestions",
                "Approve suggested rules to reduce future review work.",
                attention_counts["rule_suggestions"],
                "/rules?approval=suggested",
                "bi-magic",
                "info",
            )
        )
    if attention_counts["amount_changes"]:
        items.append(
            attention_item(
                "amount_changes",
                "Recurring amount changes",
                "Check recurring items whose amount moved outside tolerance.",
                attention_counts["amount_changes"],
                "/recurring?statuses=amount_changed",
                "bi-arrow-left-right",
                "info",
            )
        )
    if attention_counts["failed_imports"]:
        latest = failed_imports[0] if failed_imports else None
        items.append(
            attention_item(
                "failed_imports",
                "Failed imports",
                "Retry or inspect the latest failed statement import.",
                attention_counts["failed_imports"],
                "/upload",
                "bi-exclamation-octagon",
                "danger",
                latest["filename"] if latest else "",
            )
        )
    if attention_counts["failed_jobs"]:
        latest = failed_jobs[0] if failed_jobs else None
        items.append(
            attention_item(
                "failed_jobs",
                "Failed jobs",
                "Open the jobs page to inspect failed background work.",
                attention_counts["failed_jobs"],
                "/jobs",
                "bi-activity",
                "danger",
                latest.get("label", "") if latest else "",
            )
        )
    if attention_counts["review_groups"]:
        items.append(
            attention_item(
                "review_groups",
                "Uncategorized merchant groups",
                "Turn repeated unknown merchants into reusable rules.",
                attention_counts["review_groups"],
                "/review",
                "bi-shop",
                "secondary",
            )
        )

    return items


def attention_item(
    key: Any, title: Any, detail: Any, count: Any, href: Any, icon: Any, tone: Any, latest: Any = ""
) -> Any:
    """Return one normalized attention item for the Home template."""
    return {
        "key": key,
        "title": title,
        "detail": detail,
        "count": count,
        "href": href,
        "icon": icon,
        "tone": tone,
        "latest": latest,
    }


def build_recent_activity(
    recent_statements: Any,
    recent_reviewed: Any,
    recent_categorizations: Any,
    recent_rules: Any,
    recurring_items: Any,
    permissions: Any,
) -> Any:
    """Build a small mixed activity feed from existing operational sources."""
    items: list[Any] = []
    if permissions["can_import_statements"]:
        items.extend(statement_activity_item(row) for row in recent_statements)
    items.extend(reviewed_activity_item(row) for row in recent_reviewed)
    items.extend(categorization_activity_item(row) for row in recent_categorizations)
    if permissions["can_manage_rules"]:
        items.extend(rule_activity_item(row) for row in recent_rules)
    items.extend(recurring_activity_items(recurring_items))

    items.sort(key=lambda item: item["sort_key"], reverse=True)
    return items[:RECENT_ACTIVITY_LIMIT]


def statement_activity_item(row: Any) -> Any:
    """Return an activity-feed item for a statement upload."""
    return {
        "type": "statement",
        "label": "Imported statement",
        "name": row["filename"],
        "detail": row["account_name"] or "Personal",
        "date": date_part(row["uploaded_at"]),
        "amount": None,
        "href": "/upload",
        "icon": "bi-cloud-arrow-up",
        "sort_key": sortable_timestamp(row["uploaded_at"]),
    }


def reviewed_activity_item(row: Any) -> Any:
    """Return an activity-feed item for a reviewed transaction."""
    return {
        "type": "reviewed",
        "label": "Reviewed transaction",
        "name": row["description"],
        "detail": row["category"] or UNKNOWN_CATEGORY,
        "date": date_part(row["reviewed_at"]),
        "amount": rounded_money_float(row["amount"]),
        "href": query_url("/transactions", period="all", search=row["description"]),
        "icon": "bi-check2-square",
        "sort_key": sortable_timestamp(row["reviewed_at"]),
    }


def categorization_activity_item(row: Any) -> Any:
    """Return an activity-feed item for a transaction categorization."""
    return {
        "type": "categorized",
        "label": "Categorized transaction",
        "name": row["description"],
        "detail": row["category"] or UNKNOWN_CATEGORY,
        "date": date_part(row["categorized_at"]),
        "amount": rounded_money_float(row["amount"]),
        "href": query_url("/transactions", period="all", search=row["description"]),
        "icon": "bi-tags",
        "sort_key": sortable_timestamp(row["categorized_at"]),
    }


def rule_activity_item(row: Any) -> Any:
    """Return an activity-feed item for a created rule."""
    label = row["merchant_name"] or row["keyword"]
    return {
        "type": "rule",
        "label": "Created rule",
        "name": label,
        "detail": row["category"] or UNKNOWN_CATEGORY,
        "date": date_part(row["created_at"]),
        "amount": None,
        "href": query_url("/rules", search=label),
        "icon": "bi-tags",
        "sort_key": sortable_timestamp(row["created_at"]),
    }


def recurring_activity_items(recurring_items: Any, limit: Any = 2) -> Any:
    """Return activity-feed items for recent current-month recurring signals."""
    priority = {
        "overdue": 0,
        "amount_changed": 1,
        "expected": 2,
        "occurred": 3,
        "likely_occurred": 4,
        "matched": 5,
    }
    candidates = [item for item in recurring_items if item["status"] in priority]
    candidates.sort(key=lambda item: (priority[item["status"]], item["date"], item["merchant"]))
    return [
        {
            "type": "recurring",
            "label": "Recurring detection",
            "name": item["merchant"],
            "detail": recurring_status_title(item["status"]),
            "date": item["date"],
            "amount": rounded_money_float(item["amount"]),
            "href": query_url("/recurring", statuses=item["status"]),
            "icon": "bi-arrow-repeat",
            "sort_key": sortable_timestamp(item["date"]),
        }
        for item in candidates[:limit]
    ]


def build_suggested_actions(attention_counts: Any, permissions: Any) -> Any:
    """Return primary Home actions with links into the existing workflow."""
    recurring_count = attention_counts["overdue_recurring"] + attention_counts["amount_changes"]
    actions = [
        {
            "label": (
                "Review unknown transactions" if permissions["can_edit_transactions"] else "Unknown transactions"
            ),
            "detail": (
                "Clear the highest-risk categorization work."
                if permissions["can_edit_transactions"]
                else "Inspect uncategorized rows before relying on reports."
            ),
            "href": "/transactions?period=all&ignored=active&category_status=unknown",
            "icon": "bi-check2-square",
            "count": attention_counts["unknown_transactions"],
            "primary": attention_counts["unknown_transactions"] > 0,
            "priority": 0,
        },
        {
            "label": "Review recurring activity",
            "detail": "Check overdue and changed recurring items.",
            "href": "/recurring",
            "icon": "bi-arrow-repeat",
            "count": recurring_count,
            "primary": attention_counts["unknown_transactions"] == 0 and recurring_count > 0,
            "priority": 1,
        },
    ]
    if permissions["can_manage_rules"]:
        actions.append(
            {
                "label": "Create rule",
                "detail": "Automate repeated merchant categorization.",
                "href": "/rules",
                "icon": "bi-tags",
                "count": attention_counts["review_groups"],
                "primary": False,
                "priority": 2,
            }
        )
    if permissions["can_import_statements"]:
        actions.append(
            {
                "label": "Import statement",
                "detail": "Add new bank, credit card, or Interac activity.",
                "href": "/upload",
                "icon": "bi-cloud-arrow-up",
                "count": None,
                "primary": attention_counts["unknown_transactions"] == 0 and recurring_count == 0,
                "priority": 3,
            }
        )
    actions.sort(key=lambda action: (not action["primary"], action["priority"]))
    return actions[:SUGGESTED_ACTION_LIMIT]


def build_primary_action(suggested_actions: Any) -> Any:
    """Return the most important Home shortcut for the pulse banner."""
    primary_actions = [action for action in suggested_actions if action["primary"]]
    return primary_actions[0] if primary_actions else suggested_actions[0]
