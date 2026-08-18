"""Application orchestration for the home feature."""

from datetime import date, datetime
from typing import Any
from urllib.parse import urlencode

from flask import has_request_context
from flask_login import current_user  # type: ignore[import-untyped]
from sqlalchemy import case, func, select

from finance_app.background.runner import list_background_jobs
from finance_app.core.category_sql import category_label_expression, transaction_category_label_expression
from finance_app.core.config import settings
from finance_app.core.constants import (
    CATEGORY_RULE_SOURCE_AUTOMATIC,
    FILTER_MODE_INCLUDE,
    NON_REPORTABLE_TRANSACTION_KINDS,
    STATEMENT_IMPORT_STATUS_FAILED,
    UNKNOWN_CATEGORY,
)
from finance_app.core.i18n import gettext
from finance_app.core.money import rounded_money_float
from finance_app.core.periods import PERIOD_CUSTOM
from finance_app.core.reporting import (
    income_amount_expression,
    income_or_tagged_transfer_credit_clause,
    reportable_transaction_clause,
    spending_impact_amount_expression,
    spending_impact_clause,
)
from finance_app.database.engine import db_core_transaction
from finance_app.database.tables import (
    accounts as accounts_table,
)
from finance_app.database.tables import (
    category_rules as category_rules_table,
)
from finance_app.database.tables import (
    merchants as merchants_table,
)
from finance_app.database.tables import (
    statements as statements_table,
)
from finance_app.database.tables import (
    transactions as transactions_table,
)
from finance_app.modules.auth.permissions import (
    PERMISSION_EDIT_TRANSACTIONS,
    PERMISSION_IMPORT_STATEMENTS,
    PERMISSION_MANAGE_JOBS,
    PERMISSION_MANAGE_RULES,
    current_user_can,
)
from finance_app.modules.calendar.service import build_recurring_activity_context
from finance_app.modules.comparison.service import build_period_comparison
from finance_app.modules.comparison.urls import build_comparison_url
from finance_app.modules.dashboard.urls import dashboard_transactions_url
from finance_app.modules.recurring.service import build_recurring_summary
from finance_app.modules.review.service import review_groups, review_summary
from finance_app.modules.settings.runtime import get_int_setting, get_unknown_category
from finance_app.modules.transactions.constants import AMOUNT_TYPE_SPENDING, IGNORED_FILTER_ACTIVE
from finance_app.modules.users import repository as user_repository

RECENT_ACTIVITY_LIMIT = 5
SUGGESTED_ACTION_LIMIT = 4
HOME_QUICK_INSIGHT_LIMIT = 3
HOME_QUICK_INSIGHT_COMPARISON = "month_previous"


def current_year_start() -> Any:
    """Return the first date of the current local calendar year."""
    return date.today().replace(month=1, day=1)


def build_home_context() -> Any:
    """Build the Home command-center context.

    The Home page is a lightweight operational read model. It keeps financial
    pulse metrics scoped to the current year while attention and activity items
    use active records across the ledger so unresolved work does not disappear
    merely because it belongs to an earlier reporting period.
    """
    with db_core_transaction() as conn:
        unknown_category = get_unknown_category(conn) or UNKNOWN_CATEGORY
        top_category_limit = get_int_setting(conn, "home_top_category_limit", settings.default_home_top_category_limit)
        merchant_table_limit = get_int_setting(conn, "merchant_table_limit", settings.default_merchant_table_limit)
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
        recent_reviewed = fetch_recent_reviewed_transactions(conn, unknown_category)
        recent_categorizations = fetch_recent_categorizations(conn, unknown_category)
        recent_rules = fetch_recent_rules(conn, unknown_category)
        comparison_quick_insights = fetch_ranked_comparison_quick_insights(
            conn,
            unknown_category,
            merchant_table_limit,
        )
        sharing_context = build_user_sharing_context(conn)

    recurring_context = build_recurring_activity_context() if has_request_context() else {"recurring_items": []}
    recurring_items = recurring_context["recurring_items"]
    recurring_summary = build_recurring_summary(recurring_items)
    failed_jobs = [job for job in list_background_jobs(limit=None) if job.get("status") == "failed"]
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
    permissions = home_permissions()
    visible_attention_counts = visible_home_attention_counts(attention_counts, permissions)
    suggested_actions = build_suggested_actions(visible_attention_counts, permissions)

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
        "financial_pulse": build_financial_pulse(
            overview,
            ytd_income,
            ytd_spending,
            ytd_cashflow,
            visible_attention_counts,
        ),
        "pulse_kpis": build_pulse_kpis(ytd_spending, ytd_cashflow, visible_attention_counts, permissions),
        "attention_counts": visible_attention_counts,
        "attention_items": build_attention_items(
            visible_attention_counts,
            failed_imports["latest"],
            failed_jobs,
            permissions,
        ),
        "recent_activity": build_recent_activity(
            recent_statements,
            recent_reviewed,
            recent_categorizations,
            recent_rules,
            recurring_items,
            permissions,
        ),
        "suggested_actions": suggested_actions,
        "primary_action": build_primary_action(suggested_actions),
        "quick_insights": build_quick_insights(
            overview,
            latest_statement,
            statement_count,
            top_categories,
            recurring_summary,
            permissions,
            comparison_quick_insights,
        ),
        "recurring_summary": recurring_summary,
        "review_summary": review_work,
        "home_greeting": build_home_greeting(),
        "home_sharing": sharing_context,
    }


def build_home_greeting() -> Any:
    """Return the personalized Home greeting message and display name."""
    display_name = "there"
    if has_request_context() and getattr(current_user, "is_authenticated", False):
        display_name = current_user.display_name or current_user.username

    hour = datetime.now().hour
    if hour < 12:
        message = "Good morning, {name}"
    elif hour < 18:
        message = "Good afternoon, {name}"
    else:
        message = "Good evening, {name}"
    return {"message": message, "name": display_name}


def build_user_sharing_context(conn: Any) -> Any:
    """Return subtle shared-access copy for the Home title area."""
    if not has_request_context() or not getattr(current_user, "is_authenticated", False):
        return {"message": "", "params": {}}

    users = [dict(row) for row in user_repository.list_users(conn) if row["is_active"]]
    owner = next((user for user in users if user["role"] == "owner"), None)
    current_user_id = int(current_user.id)
    others = [user for user in users if int(user["id"]) != current_user_id]
    owner_name = display_name_for_user(owner) if owner else "Owner"

    if not others:
        return sharing_context("Only you have access")

    shared_params = sharing_names_or_count(others)
    if owner and current_user_id == int(owner["id"]):
        return sharing_context(shared_params["message"], **shared_params["params"])

    return sharing_context(
        f"{shared_params['message']} \u00b7 Owner: {{owner}}",
        **shared_params["params"],
        owner=owner_name,
    )


def sharing_names_or_count(users: Any) -> Any:
    """Return shared-with message parts for a compact user list."""
    names = [display_name_for_user(user) for user in users]
    if len(names) <= 2:
        return {"message": "Shared with {names}", "params": {"names": " and ".join(names)}}
    return {"message": "Shared with {count} users", "params": {"count": len(names)}}


def sharing_context(message: Any, names: Any = "", count: Any = 0, owner: Any = "") -> Any:
    """Return a complete Home sharing message object for templates."""
    return {
        "message": message,
        "params": {
            "names": names,
            "count": count,
            "owner": owner,
        },
    }


def display_name_for_user(user: Any) -> Any:
    """Return a user's display label for collaborative context."""
    return (user or {}).get("display_name") or (user or {}).get("username") or ""


def home_permissions() -> Any:
    """Return current-user permissions that affect Home links and actions."""
    if not has_request_context():
        return {
            "can_edit_transactions": True,
            "can_import_statements": True,
            "can_manage_jobs": True,
            "can_manage_rules": True,
        }
    return {
        "can_edit_transactions": current_user_can(PERMISSION_EDIT_TRANSACTIONS),
        "can_import_statements": current_user_can(PERMISSION_IMPORT_STATEMENTS),
        "can_manage_jobs": current_user_can(PERMISSION_MANAGE_JOBS),
        "can_manage_rules": current_user_can(PERMISSION_MANAGE_RULES),
    }


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


def fetch_home_overview(conn: Any, unknown_category: Any, start_date: Any) -> Any:
    """Return current-year transaction totals for the financial pulse.

    Args:
        conn: Active SQLAlchemy Core connection.
        unknown_category: The category label treated as uncategorized.
        start_date: Inclusive date for the current calendar year.

    Returns:
        A mapping with transaction counts, year-to-date spending, income, unknown count,
        and the latest active reportable transaction date.
    """
    category_value = transaction_category_label_expression(unknown_category)
    reportable = reportable_transaction_clause()
    spending_amount = spending_impact_amount_expression()
    income_amount = income_amount_expression()
    income_clause = income_or_tagged_transfer_credit_clause()
    return (
        conn.execute(
            select(
                func.count().label("transaction_count"),
                func.coalesce(
                    func.sum(case((spending_impact_clause(), spending_amount), else_=0)),
                    0,
                ).label("ytd_spending"),
                func.coalesce(
                    func.sum(case((income_clause, income_amount), else_=0)),
                    0,
                ).label("ytd_income"),
                func.coalesce(
                    func.sum(case((category_value == unknown_category, 1), else_=0)),
                    0,
                ).label("uncategorized_count"),
                func.max(transactions_table.c.tx_date).label("latest_tx_date"),
            ).where(
                transactions_table.c.ignored == 0,
                reportable,
                transactions_table.c.tx_date >= start_date,
            )
        )
        .mappings()
        .fetchone()
    )


def fetch_attention_summary(conn: Any, unknown_category: Any) -> Any:
    """Return active ledger counts that should remain visible until resolved."""
    category_value = transaction_category_label_expression(unknown_category)
    return (
        conn.execute(
            select(
                func.coalesce(func.sum(case((category_value == unknown_category, 1), else_=0)), 0).label(
                    "unknown_count"
                ),
                func.coalesce(func.sum(case((transactions_table.c.needs_review == 1, 1), else_=0)), 0).label(
                    "needs_review_count"
                ),
            ).where(
                transactions_table.c.ignored == 0,
                transactions_table.c.transaction_kind.not_in(NON_REPORTABLE_TRANSACTION_KINDS),
            )
        )
        .mappings()
        .fetchone()
    )


def fetch_statement_count(conn: Any) -> Any:
    """Return the total number of uploaded statements."""
    return conn.execute(select(func.count()).select_from(statements_table)).scalar_one()


def fetch_latest_statement(conn: Any) -> Any:
    """Return the most recently uploaded statement with its account label."""
    return conn.execute(latest_statement_query().limit(1)).mappings().fetchone()


def fetch_recent_statements(conn: Any, limit: Any = 2) -> Any:
    """Return recent statement uploads for the activity feed."""
    return conn.execute(latest_statement_query().limit(limit)).mappings().fetchall()


def latest_statement_query() -> Any:
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


def fetch_failed_imports(conn: Any, limit: Any = 3) -> Any:
    """Return failed import count and latest failed statement rows."""
    count = conn.execute(
        select(func.count())
        .select_from(statements_table)
        .where(statements_table.c.import_status == STATEMENT_IMPORT_STATUS_FAILED)
    ).scalar_one()
    rows = (
        conn.execute(
            latest_statement_query()
            .where(statements_table.c.import_status == STATEMENT_IMPORT_STATUS_FAILED)
            .limit(limit)
        )
        .mappings()
        .fetchall()
    )
    return {
        "count": count,
        "latest": rows,
    }


def fetch_rule_suggestion_count(conn: Any) -> Any:
    """Return the number of automatic rules still awaiting approval."""
    return conn.execute(
        select(func.count())
        .select_from(category_rules_table)
        .where(
            category_rules_table.c.source == CATEGORY_RULE_SOURCE_AUTOMATIC,
            category_rules_table.c.ai_approved == 0,
        )
    ).scalar_one()


def build_review_work_summary(conn: Any, unknown_category: Any) -> Any:
    """Return grouped review work for uncategorized merchant action cards."""
    groups = review_groups(conn, unknown_category)
    return review_summary(groups)


def fetch_top_categories(conn: Any, unknown_category: Any, start_date: Any, limit: Any) -> Any:
    """Return top current-year spending categories for compact Home insights."""
    category = transaction_category_label_expression(unknown_category)
    total = func.sum(spending_impact_amount_expression())
    return (
        conn.execute(
            select(
                category.label("category"),
                total.label("total"),
            )
            .where(
                spending_impact_clause(),
                reportable_transaction_clause(),
                transactions_table.c.ignored == 0,
                transactions_table.c.tx_date >= start_date,
            )
            .group_by(category)
            .order_by(total.desc())
            .limit(limit)
        )
        .mappings()
        .fetchall()
    )


def fetch_recent_reviewed_transactions(conn: Any, unknown_category: str, limit: Any = 2) -> Any:
    """Return recently reviewed transactions for the activity feed."""
    return (
        conn.execute(
            select(
                transactions_table.c.id,
                transactions_table.c.tx_date,
                transactions_table.c.description,
                transactions_table.c.amount,
                transaction_category_label_expression(unknown_category).label("category"),
                transactions_table.c.reviewed_at,
            )
            .where(
                transactions_table.c.ignored == 0,
                transactions_table.c.reviewed_at.is_not(None),
            )
            .order_by(transactions_table.c.reviewed_at.desc(), transactions_table.c.id.desc())
            .limit(limit)
        )
        .mappings()
        .fetchall()
    )


def fetch_recent_categorizations(conn: Any, unknown_category: str, limit: Any = 2) -> Any:
    """Return recent categorization events that were not already reviewed."""
    return (
        conn.execute(
            select(
                transactions_table.c.id,
                transactions_table.c.tx_date,
                transactions_table.c.description,
                transactions_table.c.amount,
                transaction_category_label_expression(unknown_category).label("category"),
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
        )
        .mappings()
        .fetchall()
    )


def fetch_recent_rules(conn: Any, unknown_category: str, limit: Any = 2) -> Any:
    """Return recently created category rules for the activity feed."""
    return (
        conn.execute(
            select(
                category_rules_table.c.id,
                category_rules_table.c.keyword,
                category_label_expression(category_rules_table, unknown_category).label("category"),
                category_rules_table.c.source,
                category_rules_table.c.created_at,
                merchants_table.c.merchant_key.label("merchant_name"),
            )
            .select_from(
                category_rules_table.outerjoin(
                    merchants_table,
                    merchants_table.c.id == category_rules_table.c.merchant_id,
                )
            )
            .order_by(category_rules_table.c.created_at.desc(), category_rules_table.c.id.desc())
            .limit(limit)
        )
        .mappings()
        .fetchall()
    )


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
