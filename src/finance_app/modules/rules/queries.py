"""Read-side SQL helpers for rule matching and previews.

These helpers narrow candidate transaction rows with SQLAlchemy Core before
the pure rule engine applies final matching semantics.
"""

from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy import and_, false, or_, select

from finance_app.core.builtin_taxonomy import is_income_category_name
from finance_app.core.category_sql import transaction_category_label_expression
from finance_app.core.constants import CATEGORY_RULE_DIRECTION_CREDIT, CATEGORY_RULE_DIRECTION_DEBIT
from finance_app.database.tables import transactions as transactions_table
from finance_app.modules.merchants.normalization import normalize_merchant_description
from finance_app.modules.merchants.sql_filters import (
    description_matches_any_candidate,
    merchant_description_candidates,
)
from finance_app.modules.rules.engine import rule_direction


def fetch_rule_preview_candidates(conn: Any, rule: Mapping[str, Any], keyword: str) -> Any:
    """Return active transaction candidates for a rule preview."""
    return (
        conn.execute(
            select(
                transactions_table.c.id,
                transactions_table.c.merchant_id,
                transactions_table.c.tx_date,
                transactions_table.c.description,
                transactions_table.c.amount,
                transaction_category_label_expression(None).label("category"),
            )
            .where(rule_sql_candidate_condition(conn, rule, keyword))
            .order_by(transactions_table.c.tx_date.desc(), transactions_table.c.id.desc())
        )
        .mappings()
        .fetchall()
    )


def active_transaction_rows(
    conn: Any,
    include_category_state: bool = False,
    rules: Sequence[Mapping[str, Any]] | None = None,
) -> Any:
    """Return non-ignored transaction rows used by rule application workflows."""
    columns: list[Any] = [
        transactions_table.c.id,
        transactions_table.c.account_id,
        transactions_table.c.description,
        transactions_table.c.amount,
        transactions_table.c.merchant_id,
        transactions_table.c.transaction_kind,
    ]
    if include_category_state:
        category_label = transaction_category_label_expression(None)
        columns.extend(
            [
                category_label.label("category"),
                transactions_table.c.category_id,
                transactions_table.c.needs_review,
                transactions_table.c.category_source,
                transactions_table.c.category_confidence,
                transactions_table.c.category_rule_id,
                transactions_table.c.category_metadata,
                transactions_table.c.categorized_at,
                transactions_table.c.reviewed_at,
            ]
        )

    conditions: list[Any] = [transactions_table.c.ignored == 0]
    if rules is not None:
        conditions.append(any_rule_sql_candidate_condition(conn, rules))

    return conn.execute(select(*columns).where(*conditions)).mappings().fetchall()


def any_rule_sql_candidate_condition(conn: Any, rules: Sequence[Mapping[str, Any]]) -> Any:
    """Return a SQL predicate for transactions that may match any rule."""
    conditions: list[Any] = []
    for rule in rules:
        keyword = normalize_merchant_description(rule["keyword"])
        condition = rule_sql_candidate_condition(conn, rule, keyword, include_ignored=False)
        if condition is not None:
            conditions.append(condition)

    return or_(*conditions) if conditions else false()


def rule_sql_candidate_condition(
    conn: Any,
    rule: Mapping[str, Any],
    keyword: str,
    include_ignored: bool = True,
) -> Any:
    """Return simple SQL predicates that narrow rule matching candidates."""
    conditions: list[Any] = []
    if include_ignored:
        conditions.append(transactions_table.c.ignored == 0)

    rule_merchant_id = rule["merchant_id"] if "merchant_id" in rule.keys() else rule.get("merchant_id")
    if rule_merchant_id is not None:
        conditions.append(transactions_table.c.merchant_id == int(rule_merchant_id))
    else:
        candidates = merchant_description_candidates(conn, keyword)
        conditions.append(description_matches_any_candidate(transactions_table.c.description, candidates))

    if is_income_category_name(rule["category"]):
        conditions.append(transactions_table.c.amount < 0)

    rule_account_id = rule["account_id"] if "account_id" in rule.keys() else rule.get("account_id")
    if rule_account_id is not None:
        conditions.append(transactions_table.c.account_id == int(rule_account_id))

    direction = rule_direction(rule)
    if direction == CATEGORY_RULE_DIRECTION_DEBIT:
        conditions.append(transactions_table.c.amount >= 0)
    elif direction == CATEGORY_RULE_DIRECTION_CREDIT:
        conditions.append(transactions_table.c.amount < 0)

    amount_min = rule["amount_min"] if "amount_min" in rule.keys() else None
    amount_max = rule["amount_max"] if "amount_max" in rule.keys() else None
    if amount_min is not None:
        conditions.append(transactions_table.c.amount >= amount_min)
    if amount_max is not None:
        conditions.append(transactions_table.c.amount <= amount_max)

    return and_(*conditions)
