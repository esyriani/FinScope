"""Application orchestration for the rules feature."""

from collections.abc import Iterable, Mapping
from typing import Any

from sqlalchemy import delete, func, select, union_all, update

from finance_app.core.constants import CATEGORY_RULE_SOURCE_AUTOMATIC, CATEGORY_RULE_SOURCE_MANUAL
from finance_app.core.money import money_to_float
from finance_app.database.tables import (
    category_rules as category_rules_table,
)
from finance_app.database.tables import (
    transaction_tags as transaction_tags_table,
)
from finance_app.database.tables import (
    transactions as transactions_table,
)
from finance_app.modules.categories.repository import normalize_optional_merchant_id, resolve_category_id
from finance_app.modules.categories.service import (
    get_category_options,
    normalize_category,
    normalize_merchant_description,
    save_category_rule,
)
from finance_app.modules.categories.taxonomy import (
    get_rule_tags_by_rule_id,
    get_tag_options,
    normalize_tag_names,
    set_rule_tags,
)
from finance_app.modules.rules.forms import parse_amount_bounds, parse_rule_account_id, parse_rule_direction


def create_rule_from_form(conn: Any, form: Any) -> tuple[int, str]:
    """Create a manual rule from form data and return its id and keyword."""
    tag_options = get_tag_options(conn)
    keyword = normalize_merchant_description(form.get("keyword", ""))
    category = normalize_category(form.get("category", ""), get_category_options(conn))
    tags = normalize_tag_names(form.getlist("tags"), tag_options)
    merchant_id = normalize_optional_merchant_id(form.get("merchant_id"))
    account_id = parse_rule_account_id(form.get("account_id"))
    direction = parse_rule_direction(form.get("direction"))
    amount_min, amount_max = parse_amount_bounds(
        form.get("amount_min", ""),
        form.get("amount_max", ""),
    )
    if not keyword or not category:
        raise ValueError("Keyword and category are required.")

    rule_id = save_category_rule(
        conn,
        keyword,
        category,
        source=CATEGORY_RULE_SOURCE_MANUAL,
        amount_min=amount_min,
        amount_max=amount_max,
        tags=tags,
        merchant_id=merchant_id,
        account_id=account_id,
        direction=direction,
    )
    if rule_id is None:
        raise ValueError("Rule could not be saved.")
    return int(rule_id), keyword


def preview_rule_from_form(conn: Any, form: Any) -> dict[str, Any]:
    """Preview rule from form."""
    tag_options = get_tag_options(conn)
    keyword = normalize_merchant_description(form.get("keyword", ""))
    category = normalize_category(form.get("category", ""), get_category_options(conn))
    tags = normalize_tag_names(form.getlist("tags"), tag_options)
    merchant_id = normalize_optional_merchant_id(form.get("merchant_id"))
    account_id = parse_rule_account_id(form.get("account_id"))
    direction = parse_rule_direction(form.get("direction"))
    amount_min, amount_max = parse_amount_bounds(
        form.get("amount_min", ""),
        form.get("amount_max", ""),
    )
    if not keyword or not category:
        raise ValueError("Enter a keyword and category to preview matching transactions.")

    return {
        "keyword": keyword,
        "category": category,
        "tags": tags,
        "amount_min": amount_min,
        "amount_max": amount_max,
        "merchant_id": merchant_id,
        "account_id": account_id,
        "direction": direction,
    }


def update_rule_from_form(conn: Any, rule_id: int, form: Any) -> None:
    """Update rule from form."""
    tag_options = get_tag_options(conn)
    keyword = normalize_merchant_description(form.get("keyword", ""))
    category = normalize_category(form.get("category", ""), get_category_options(conn))
    tags = normalize_tag_names(form.getlist("tags"), tag_options)
    current = fetch_rule_source(conn, rule_id)
    merchant_id = normalize_optional_merchant_id(
        form.get("merchant_id") if "merchant_id" in form else current["merchant_id"] if current else None
    )
    account_id = parse_rule_account_id(
        form.get("account_id") if "account_id" in form else current["account_id"] if current else None
    )
    direction = parse_rule_direction(
        form.get("direction") if "direction" in form else current["direction"] if current else None
    )
    amount_min, amount_max = parse_amount_bounds(
        form.get("amount_min", ""),
        form.get("amount_max", ""),
    )
    if not keyword or not category:
        raise ValueError("Keyword and category are required.")

    source = (
        CATEGORY_RULE_SOURCE_AUTOMATIC
        if current and current["source"] == CATEGORY_RULE_SOURCE_AUTOMATIC
        else CATEGORY_RULE_SOURCE_MANUAL
    )
    ai_approved = 1 if source == CATEGORY_RULE_SOURCE_AUTOMATIC else 0
    conn.execute(
        update(category_rules_table)
        .where(category_rules_table.c.id == rule_id)
        .values(
            merchant_id=merchant_id,
            account_id=account_id,
            keyword=keyword,
            category=category,
            category_id=resolve_category_id(conn, category),
            amount_min=amount_min,
            amount_max=amount_max,
            direction=direction,
            source=source,
            ai_approved=ai_approved,
        )
    )
    set_rule_tags(conn, rule_id, tags)


def fetch_rule_source(conn: Any, rule_id: int) -> Mapping[str, Any] | None:
    """Return the merchant scope and source for a category rule."""
    return (
        conn.execute(
            select(
                category_rules_table.c.merchant_id,
                category_rules_table.c.account_id,
                category_rules_table.c.direction,
                category_rules_table.c.source,
            ).where(category_rules_table.c.id == rule_id)
        )
        .mappings()
        .fetchone()
    )


def approve_automatic_rule(conn: Any, rule_id: int) -> tuple[str, bool]:
    """Mark an automatic category rule as approved.

    Args:
        conn: Open SQLAlchemy Core connection.
        rule_id: Category rule primary key to approve.

    Returns:
        A tuple of the rule keyword and whether this call changed the approval
        state.

    Raises:
        ValueError: If the rule does not exist or was not automatic.
    """
    row = fetch_rule_approval(conn, rule_id)
    if row is None:
        raise ValueError("Rule not found.")
    if row["source"] != CATEGORY_RULE_SOURCE_AUTOMATIC:
        raise ValueError("Only automatic rules can be approved.")
    if row["ai_approved"]:
        return row["keyword"], False

    conn.execute(update(category_rules_table).where(category_rules_table.c.id == rule_id).values(ai_approved=1))
    return row["keyword"], True


def fetch_rule_approval(conn: Any, rule_id: int) -> Mapping[str, Any] | None:
    """Return rule fields needed by the approval workflow."""
    return (
        conn.execute(
            select(
                category_rules_table.c.keyword,
                category_rules_table.c.source,
                category_rules_table.c.ai_approved,
            ).where(category_rules_table.c.id == rule_id)
        )
        .mappings()
        .fetchone()
    )


def count_rule_transaction_references(conn: Any, rule_id: int) -> int:
    """Return distinct transactions currently linked to one rule.

    Counts category assignments and rule-applied tags. The caller owns the
    database transaction. Missing rules naturally return zero because only
    transaction-side references are considered.
    """
    return count_rule_transaction_references_by_rule_id(conn, [rule_id]).get(rule_id, 0)


def count_rule_transaction_references_by_rule_id(conn: Any, rule_ids: Iterable[object]) -> dict[int, int]:
    """Return transaction reference counts keyed by category rule id.

    A rule is considered applied when an existing transaction stores the rule
    as its category source or when a transaction tag row still references the
    rule. Counts are distinct per transaction so a tagged category assignment
    is counted once.
    """
    rule_ids = [int(str(rule_id)) for rule_id in rule_ids if rule_id is not None]
    if not rule_ids:
        return {}

    category_refs = select(
        transactions_table.c.category_rule_id.label("rule_id"),
        transactions_table.c.id.label("transaction_id"),
    ).where(transactions_table.c.category_rule_id.in_(rule_ids))
    tag_refs = select(
        transaction_tags_table.c.rule_id.label("rule_id"),
        transaction_tags_table.c.transaction_id.label("transaction_id"),
    ).where(transaction_tags_table.c.rule_id.in_(rule_ids))
    refs = union_all(category_refs, tag_refs).subquery()
    rows = (
        conn.execute(
            select(
                refs.c.rule_id,
                func.count(func.distinct(refs.c.transaction_id)).label("count"),
            )
            .where(refs.c.rule_id.is_not(None))
            .group_by(refs.c.rule_id)
        )
        .mappings()
        .fetchall()
    )
    return {int(row["rule_id"]): int(row["count"]) for row in rows}


def get_rule_for_apply(conn: Any, rule_id: int) -> dict[str, Any] | None:
    """Return rule for apply."""
    row = (
        conn.execute(
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
            ).where(category_rules_table.c.id == rule_id)
        )
        .mappings()
        .fetchone()
    )
    if row is None:
        return None

    rule = dict(row)
    if rule["amount_min"] is not None:
        rule["amount_min"] = money_to_float(rule["amount_min"])
    if rule["amount_max"] is not None:
        rule["amount_max"] = money_to_float(rule["amount_max"])
    rule["tags"] = get_rule_tags_by_rule_id(conn, [rule["id"]]).get(rule["id"], [])
    return rule


def delete_rule(conn: Any, rule_id: int) -> bool:
    """Delete a category rule and return whether a row was removed."""
    result = conn.execute(delete(category_rules_table).where(category_rules_table.c.id == rule_id))
    return (result.rowcount or 0) > 0
