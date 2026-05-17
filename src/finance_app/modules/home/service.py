"""Application orchestration for the home feature."""

from datetime import date
from urllib.parse import urlencode

from flask import has_request_context
from sqlalchemy import case, func, select

from finance_app.background.runner import list_background_jobs
from finance_app.core.config import settings
from finance_app.core.constants import (
    CATEGORY_RULE_SOURCE_AUTOMATIC,
    NON_REPORTABLE_TRANSACTION_KINDS,
    STATEMENT_IMPORT_STATUS_FAILED,
    UNKNOWN_CATEGORY,
)
from finance_app.core.money import rounded_money_float
from finance_app.database.engine import db_core_transaction
from finance_app.database.tables import (
    accounts as accounts_table,
    category_rules as category_rules_table,
    merchants as merchants_table,
    statements as statements_table,
    transactions as transactions_table,
)
from finance_app.modules.calendar.service import build_recurring_activity_context
from finance_app.modules.recurring.service import build_recurring_summary
from finance_app.modules.review.presenter import review_groups, review_summary
from finance_app.modules.settings.runtime import get_int_setting, get_unknown_category


RECENT_ACTIVITY_LIMIT = 5
SUGGESTED_ACTION_LIMIT = 4


def current_year_start():
    """Return the first date of the current local calendar year."""
    return date.today().replace(month=1, day=1)


def build_home_context():
    """Build the Home command-center context.

    The Home page is a lightweight operational read model. It keeps financial
    pulse metrics scoped to the current year while attention and activity items
    use active records across the ledger so unresolved work does not disappear
    merely because it is older than the current reporting period.
    """
    with db_core_transaction() as conn:
        unknown_category = get_unknown_category(conn) or UNKNOWN_CATEGORY
        top_category_limit = get_int_setting(conn, "home_top_category_limit", settings.default_home_top_category_limit)
        start_date = current_year_start()

        overview = fetch_home_overview(conn, unknown_category, start_date)
        attention_summary = fetch_attention_summary(conn, unknown_category)
        statement_count = fetch_statement_count(conn)
        latest_statement = fetch_latest_statement(conn)
        failed_imports = fetch_failed_imports(conn)
        rule_suggestion_count = fetch_rule_suggestion_count(conn)
        review_work = build_review_work_summary(conn, unknown_category)
        top_categories = fetch_top_categories(conn, unknown_category, start_date, top_category_limit)
        recent_statements = fetch_recent_statements(conn)
        recent_reviewed = fetch_recent_reviewed_transactions(conn)
        recent_categorizations = fetch_recent_categorizations(conn)
        recent_rules = fetch_recent_rules(conn)

    recurring_context = (
        build_recurring_activity_context()
        if has_request_context()
        else {"recurring_items": []}
    )
    recurring_items = recurring_context["recurring_items"]
    recurring_summary = build_recurring_summary(recurring_items)
    failed_jobs = [
        job
        for job in list_background_jobs(limit=None)
        if job.get("status") == "failed"
    ]
    ytd_spending = rounded_money_float(overview["ytd_spending"])
    ytd_income = rounded_money_float(overview["ytd_income"])
    ytd_cashflow = rounded_money_float(ytd_income - ytd_spending)
    attention_counts = {
        "unknown_transactions": attention_summary["unknown_count"],
        "needs_review": attention_summary["needs_review_count"],
        "review_groups": review_work["group_count"],
        "overdue_recurring": recurring_summary["overdue_count"],
        "amount_changes": recurring_summary["amount_change_count"],
        "failed_imports": failed_imports["count"],
        "failed_jobs": len(failed_jobs),
        "rule_suggestions": rule_suggestion_count,
    }
    suggested_actions = build_suggested_actions(attention_counts)

    return {
        "overview": overview,
        "statement_count": statement_count,
        "latest_statement": latest_statement,
        "top_categories": [
            {
                **dict(row),
                "total": rounded_money_float(row["total"]),
            }
            for row in top_categories
        ],
        "unknown_category": unknown_category,
        "ytd_spending": ytd_spending,
        "ytd_income": ytd_income,
        "ytd_cashflow": ytd_cashflow,
        "financial_pulse": build_financial_pulse(overview, ytd_income, ytd_spending, ytd_cashflow, attention_counts),
        "pulse_kpis": build_pulse_kpis(ytd_spending, ytd_cashflow, attention_counts),
        "attention_counts": attention_counts,
        "attention_items": build_attention_items(
            attention_counts,
            failed_imports["latest"],
            failed_jobs,
        ),
        "recent_activity": build_recent_activity(
            recent_statements,
            recent_reviewed,
            recent_categorizations,
            recent_rules,
            recurring_items,
        ),
        "suggested_actions": suggested_actions,
        "primary_action": build_primary_action(suggested_actions),
        "quick_insights": build_quick_insights(
            overview,
            latest_statement,
            statement_count,
            top_categories,
            recurring_summary,
        ),
        "recurring_summary": recurring_summary,
        "review_summary": review_work,
    }


def fetch_home_overview(conn, unknown_category, start_date):
    """Return current-year transaction totals for the financial pulse.

    Args:
        conn: Active SQLAlchemy Core connection.
        unknown_category: The category label treated as uncategorized.
        start_date: Inclusive date for the current calendar year.

    Returns:
        A mapping with transaction counts, YTD spending, income, unknown count,
        and the latest active reportable transaction date.
    """
    return conn.execute(
        select(
            func.count().label("transaction_count"),
            func.coalesce(
                func.sum(
                    case(
                        (
                            (transactions_table.c.amount > 0)
                            & (transactions_table.c.transaction_kind == "expense"),
                            transactions_table.c.amount,
                        ),
                        else_=0,
                    )
                ),
                0,
            ).label("ytd_spending"),
            func.coalesce(
                func.sum(
                    case(
                        (
                            (transactions_table.c.amount < 0)
                            & (transactions_table.c.transaction_kind == "income"),
                            -transactions_table.c.amount,
                        ),
                        else_=0,
                    )
                ),
                0,
            ).label("ytd_income"),
            func.coalesce(
                func.sum(
                    case(
                        (
                            func.coalesce(transactions_table.c.category, unknown_category) == unknown_category,
                            1,
                        ),
                        else_=0,
                    )
                ),
                0,
            ).label("uncategorized_count"),
            func.max(transactions_table.c.tx_date).label("latest_tx_date"),
        )
        .where(
            transactions_table.c.ignored == 0,
            transactions_table.c.transaction_kind.not_in(NON_REPORTABLE_TRANSACTION_KINDS),
            transactions_table.c.tx_date >= start_date,
        )
    ).mappings().fetchone()


def fetch_attention_summary(conn, unknown_category):
    """Return active ledger counts that should remain visible until resolved."""
    category_value = func.coalesce(transactions_table.c.category, unknown_category)
    return conn.execute(
        select(
            func.coalesce(func.sum(case((category_value == unknown_category, 1), else_=0)), 0).label("unknown_count"),
            func.coalesce(func.sum(case((transactions_table.c.needs_review == 1, 1), else_=0)), 0).label(
                "needs_review_count"
            ),
        )
        .where(
            transactions_table.c.ignored == 0,
            transactions_table.c.transaction_kind.not_in(NON_REPORTABLE_TRANSACTION_KINDS),
        )
    ).mappings().fetchone()


def fetch_statement_count(conn):
    """Return the total number of uploaded statements."""
    return conn.execute(select(func.count()).select_from(statements_table)).scalar_one()


def fetch_latest_statement(conn):
    """Return the most recently uploaded statement with its account label."""
    return conn.execute(latest_statement_query().limit(1)).mappings().fetchone()


def fetch_recent_statements(conn, limit=2):
    """Return recent statement uploads for the activity feed."""
    return conn.execute(latest_statement_query().limit(limit)).mappings().fetchall()


def latest_statement_query():
    """Build the shared statement query used by Home activity widgets."""
    return (
        select(
            statements_table.c.id,
            statements_table.c.filename,
            statements_table.c.uploaded_at,
            statements_table.c.import_status,
            accounts_table.c.name.label("account_name"),
        )
        .select_from(
            statements_table.outerjoin(
                accounts_table,
                accounts_table.c.id == statements_table.c.account_id,
            )
        )
        .order_by(statements_table.c.uploaded_at.desc(), statements_table.c.id.desc())
    )


def fetch_failed_imports(conn, limit=3):
    """Return failed import count and latest failed statement rows."""
    count = conn.execute(
        select(func.count())
        .select_from(statements_table)
        .where(statements_table.c.import_status == STATEMENT_IMPORT_STATUS_FAILED)
    ).scalar_one()
    rows = conn.execute(
        latest_statement_query()
        .where(statements_table.c.import_status == STATEMENT_IMPORT_STATUS_FAILED)
        .limit(limit)
    ).mappings().fetchall()
    return {
        "count": count,
        "latest": rows,
    }


def fetch_rule_suggestion_count(conn):
    """Return the number of automatic rules still awaiting approval."""
    return conn.execute(
        select(func.count())
        .select_from(category_rules_table)
        .where(
            category_rules_table.c.source == CATEGORY_RULE_SOURCE_AUTOMATIC,
            category_rules_table.c.ai_approved == 0,
        )
    ).scalar_one()


def build_review_work_summary(conn, unknown_category):
    """Return grouped review work for uncategorized merchant action cards."""
    groups = review_groups(conn, unknown_category)
    return review_summary(groups)


def fetch_top_categories(conn, unknown_category, start_date, limit):
    """Return top current-year spending categories for compact Home insights."""
    return conn.execute(
        select(
            func.coalesce(transactions_table.c.category, unknown_category).label("category"),
            func.sum(transactions_table.c.amount).label("total"),
        )
        .where(
            transactions_table.c.amount > 0,
            transactions_table.c.transaction_kind == "expense",
            transactions_table.c.ignored == 0,
            transactions_table.c.tx_date >= start_date,
        )
        .group_by(transactions_table.c.category)
        .order_by(func.sum(transactions_table.c.amount).desc())
        .limit(limit)
    ).mappings().fetchall()


def fetch_recent_reviewed_transactions(conn, limit=2):
    """Return recently reviewed transactions for the activity feed."""
    return conn.execute(
        select(
            transactions_table.c.id,
            transactions_table.c.tx_date,
            transactions_table.c.description,
            transactions_table.c.amount,
            transactions_table.c.category,
            transactions_table.c.reviewed_at,
        )
        .where(
            transactions_table.c.ignored == 0,
            transactions_table.c.reviewed_at.is_not(None),
        )
        .order_by(transactions_table.c.reviewed_at.desc(), transactions_table.c.id.desc())
        .limit(limit)
    ).mappings().fetchall()


def fetch_recent_categorizations(conn, limit=2):
    """Return recent categorization events that were not already reviewed."""
    return conn.execute(
        select(
            transactions_table.c.id,
            transactions_table.c.tx_date,
            transactions_table.c.description,
            transactions_table.c.amount,
            transactions_table.c.category,
            transactions_table.c.category_source,
            transactions_table.c.categorized_at,
        )
        .where(
            transactions_table.c.ignored == 0,
            transactions_table.c.categorized_at.is_not(None),
            transactions_table.c.reviewed_at.is_(None),
        )
        .order_by(transactions_table.c.categorized_at.desc(), transactions_table.c.id.desc())
        .limit(limit)
    ).mappings().fetchall()


def fetch_recent_rules(conn, limit=2):
    """Return recently created category rules for the activity feed."""
    return conn.execute(
        select(
            category_rules_table.c.id,
            category_rules_table.c.keyword,
            category_rules_table.c.category,
            category_rules_table.c.source,
            category_rules_table.c.created_at,
            merchants_table.c.display_name.label("merchant_name"),
        )
        .select_from(
            category_rules_table.outerjoin(
                merchants_table,
                merchants_table.c.id == category_rules_table.c.merchant_id,
            )
        )
        .order_by(category_rules_table.c.created_at.desc(), category_rules_table.c.id.desc())
        .limit(limit)
    ).mappings().fetchall()


def build_financial_pulse(overview, ytd_income, ytd_spending, ytd_cashflow, attention_counts):
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


def build_pulse_kpis(ytd_spending, ytd_cashflow, attention_counts):
    """Build compact KPI cards for the command-center header."""
    return [
        {
            "label": "YTD cash flow",
            "value": ytd_cashflow,
            "value_type": "money",
            "href": "/dashboard?period=ytd",
            "tone": "success" if ytd_cashflow >= 0 else "danger",
            "detail": "Income less spending.",
        },
        {
            "label": "YTD spending",
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
            "href": "/review",
            "tone": "warning" if open_attention_count(attention_counts) else "success",
            "detail": "Transactions or recurring items to clear.",
        },
    ]


def open_attention_count(attention_counts):
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


def build_attention_items(attention_counts, failed_imports, failed_jobs):
    """Build prioritized operational items that need user attention."""
    items = []
    if attention_counts["unknown_transactions"]:
        items.append(
            attention_item(
                "unknown_transactions",
                "Unknown transactions",
                "Categorize unknown rows before relying on reports.",
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


def attention_item(key, title, detail, count, href, icon, tone, latest=""):
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


def build_recent_activity(recent_statements, recent_reviewed, recent_categorizations, recent_rules, recurring_items):
    """Build a small mixed activity feed from existing operational sources."""
    items = []
    items.extend(statement_activity_item(row) for row in recent_statements)
    items.extend(reviewed_activity_item(row) for row in recent_reviewed)
    items.extend(categorization_activity_item(row) for row in recent_categorizations)
    items.extend(rule_activity_item(row) for row in recent_rules)
    items.extend(recurring_activity_items(recurring_items))

    items.sort(key=lambda item: item["sort_key"], reverse=True)
    return items[:RECENT_ACTIVITY_LIMIT]


def statement_activity_item(row):
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


def reviewed_activity_item(row):
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


def categorization_activity_item(row):
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


def rule_activity_item(row):
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


def recurring_activity_items(recurring_items, limit=2):
    """Return activity-feed items for recent current-month recurring signals."""
    priority = {
        "overdue": 0,
        "amount_changed": 1,
        "expected": 2,
        "occurred": 3,
        "likely_occurred": 4,
        "matched": 5,
    }
    candidates = [
        item
        for item in recurring_items
        if item["status"] in priority
    ]
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


def build_suggested_actions(attention_counts):
    """Return primary Home actions with links into the existing workflow."""
    recurring_count = attention_counts["overdue_recurring"] + attention_counts["amount_changes"]
    actions = [
        {
            "label": "Review unknown transactions",
            "detail": "Clear the highest-risk categorization work.",
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
        {
            "label": "Create rule",
            "detail": "Automate repeated merchant categorization.",
            "href": "/rules",
            "icon": "bi-tags",
            "count": attention_counts["review_groups"],
            "primary": False,
            "priority": 2,
        },
        {
            "label": "Import statement",
            "detail": "Add new bank, credit card, or Interac activity.",
            "href": "/upload",
            "icon": "bi-cloud-arrow-up",
            "count": None,
            "primary": attention_counts["unknown_transactions"] == 0 and recurring_count == 0,
            "priority": 3,
        },
    ]
    actions.sort(key=lambda action: (not action["primary"], action["priority"]))
    return actions[:SUGGESTED_ACTION_LIMIT]


def build_primary_action(suggested_actions):
    """Return the most important Home shortcut for the pulse banner."""
    primary_actions = [action for action in suggested_actions if action["primary"]]
    return primary_actions[0] if primary_actions else suggested_actions[0]


def build_quick_insights(overview, latest_statement, statement_count, top_categories, recurring_summary):
    """Return compact insight rows that avoid dashboard-style analytics."""
    insights = []
    insights.append(
        {
            "label": "Latest transaction",
            "value": overview["latest_tx_date"] or "",
            "value_type": "date" if overview["latest_tx_date"] else "empty",
            "detail": "Most recent active transaction.",
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
            "href": "/upload",
            "icon": "bi-file-earmark-text",
        }
    )
    if top_categories:
        category = top_categories[0]
        insights.append(
            {
                "label": "Top YTD category",
                "value": rounded_money_float(category["total"]),
                "value_type": "money",
                "detail": category["category"],
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
            "href": "/recurring",
            "icon": "bi-arrow-repeat",
        }
    )
    return insights


def query_url(path, **params):
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


def date_part(value):
    """Return the ISO date portion of a database date or timestamp value."""
    if not value:
        return ""
    return str(value).replace(" ", "T").split("T", 1)[0]


def sortable_timestamp(value):
    """Return a lexicographically sortable timestamp string."""
    return str(value or "").replace(" ", "T")


def recurring_status_title(status):
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
