"""Application orchestration for the home feature."""

from datetime import date
from typing import Any

from flask import has_request_context

from finance_app.background.runner import list_background_jobs
from finance_app.core.config import settings
from finance_app.core.constants import UNKNOWN_CATEGORY
from finance_app.core.money import rounded_money_float
from finance_app.database.engine import db_core_transaction
from finance_app.modules.home.insights import build_quick_insights, fetch_ranked_comparison_quick_insights
from finance_app.modules.home.permissions import home_permissions
from finance_app.modules.home.presenter import (
    build_attention_items,
    build_financial_pulse,
    build_primary_action,
    build_pulse_kpis,
    build_recent_activity,
    build_suggested_actions,
    visible_home_attention_counts,
)
from finance_app.modules.home.queries import (
    fetch_attention_summary,
    fetch_failed_imports,
    fetch_home_overview,
    fetch_latest_statement,
    fetch_recent_categorizations,
    fetch_recent_reviewed_transactions,
    fetch_recent_rules,
    fetch_recent_statements,
    fetch_rule_suggestion_count,
    fetch_statement_count,
    fetch_top_categories,
)
from finance_app.modules.home.sharing import build_home_greeting, build_user_sharing_context
from finance_app.modules.recurring.activity import build_recurring_activity_context
from finance_app.modules.recurring.service import build_recurring_summary
from finance_app.modules.review.service import review_groups, review_summary
from finance_app.modules.settings.runtime import get_int_setting, get_unknown_category


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


def build_review_work_summary(conn: Any, unknown_category: Any) -> Any:
    """Return grouped review work for uncategorized merchant action cards."""
    groups = review_groups(conn, unknown_category)
    return review_summary(groups)
