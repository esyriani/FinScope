"""SQL query helpers for the review feature."""

from sqlalchemy import case, select

from finance_app.core.constants import CATEGORY_RULE_DIRECTION_ANY, CATEGORY_RULE_SOURCE_MANUAL
from finance_app.database.tables import (
    accounts as accounts_table,
    category_rules as category_rules_table,
    transactions as transactions_table,
)
from finance_app.modules.categories.taxonomy import get_rule_tags_by_rule_id
from finance_app.modules.merchants.sql_filters import (
    description_matches_any_candidate,
    merchant_description_candidates,
)


def review_candidate_rows(conn, unknown_category, merchant_candidate=""):
    """Render candidate rows."""
    filters = [
        transactions_table.c.ignored == 0,
        (
            (transactions_table.c.needs_review == 1)
            | transactions_table.c.category.is_(None)
            | (transactions_table.c.category == unknown_category)
        ),
    ]
    if merchant_candidate:
        filters.append(
            description_matches_any_candidate(
                transactions_table.c.description,
                merchant_description_candidates(conn, merchant_candidate),
            )
        )

    return conn.execute(
        select(
            transactions_table.c.id,
            transactions_table.c.tx_date,
            transactions_table.c.description,
            transactions_table.c.amount,
            transactions_table.c.category,
            transactions_table.c.category_id,
            transactions_table.c.needs_review,
            transactions_table.c.category_source,
            transactions_table.c.category_confidence,
            transactions_table.c.category_rule_id,
            transactions_table.c.category_metadata,
            transactions_table.c.categorized_at,
            transactions_table.c.reviewed_at,
            transactions_table.c.transaction_kind,
            accounts_table.c.name.label("account_name"),
        )
        .select_from(
            transactions_table.outerjoin(
                accounts_table,
                accounts_table.c.id == transactions_table.c.account_id,
            )
        )
        .where(*filters)
        .order_by(transactions_table.c.tx_date.desc(), transactions_table.c.id.desc())
    ).mappings().fetchall()


def find_review_rule(conn, merchant_key, amount_min=None, amount_max=None):
    """Find review rule."""
    row = conn.execute(
        select(category_rules_table.c.id)
        .where(
            category_rules_table.c.keyword == merchant_key,
            category_rules_table.c.merchant_id.is_(None),
            category_rules_table.c.account_id.is_(None),
            category_rules_table.c.direction == CATEGORY_RULE_DIRECTION_ANY,
            optional_amount_condition(category_rules_table.c.amount_min, amount_min),
            optional_amount_condition(category_rules_table.c.amount_max, amount_max),
        )
        .order_by(
            case((category_rules_table.c.source == CATEGORY_RULE_SOURCE_MANUAL, 0), else_=1),
            category_rules_table.c.id,
        )
        .limit(1)
    ).mappings().fetchone()
    return rule_snapshot(conn, row["id"]) if row else None


def rule_snapshot(conn, rule_id):
    """Build snapshot."""
    row = conn.execute(
        select(
            category_rules_table.c.id,
            category_rules_table.c.account_id,
            category_rules_table.c.merchant_id,
            category_rules_table.c.keyword,
            category_rules_table.c.category,
            category_rules_table.c.category_id,
            category_rules_table.c.amount_min,
            category_rules_table.c.amount_max,
            category_rules_table.c.direction,
            category_rules_table.c.source,
            category_rules_table.c.ai_approved,
            category_rules_table.c.created_at,
        ).where(category_rules_table.c.id == rule_id)
    ).mappings().fetchone()
    if not row:
        return None

    snapshot = dict(row)
    snapshot["tags"] = get_rule_tags_by_rule_id(conn, [rule_id]).get(rule_id, [])
    return snapshot


def optional_amount_condition(column, value):
    """Return a Core predicate for nullable amount-bound comparisons."""
    return column.is_(None) if value is None else column == value
