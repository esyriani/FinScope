"""Domain engine for the rules feature."""

from collections.abc import Mapping, MutableMapping, Sequence
from typing import Any

from sqlalchemy import and_, false, or_, select, update

from finance_app.core.category_sql import transaction_category_label_expression
from finance_app.core.constants import (
    CATEGORY_RULE_DIRECTION_ANY,
    CATEGORY_RULE_DIRECTION_CREDIT,
    CATEGORY_RULE_DIRECTION_DEBIT,
    TRANSACTION_KIND_EXPENSE,
    TRANSACTION_KIND_INCOME,
    TRANSACTION_KIND_REFUND,
    TRANSACTION_KIND_TRANSFER,
    TRANSFER_CATEGORY,
)
from finance_app.core.filters import format_money
from finance_app.core.money import MoneyValue, money_to_decimal, money_to_float, rounded_money_decimal
from finance_app.database.engine import db_core_transaction
from finance_app.database.tables import transactions as transactions_table
from finance_app.modules.categories.builtins import is_income_category_name
from finance_app.modules.categories.decision import DECISION_SOURCE_RULE
from finance_app.modules.categories.repository import resolve_category_id
from finance_app.modules.categories.service import (
    get_category_options,
    get_category_rules,
    match_category_rule,
    merchant_match_candidates,
    normalize_category,
    normalize_merchant_description,
    rule_amount_matches,
)
from finance_app.modules.categories.sources import (
    CATEGORY_SOURCE_RULE,
    TransactionCategoryChange,
    TransactionCategorySnapshot,
    TransactionCategoryState,
    category_assignment,
)
from finance_app.modules.categories.taxonomy import (
    get_transaction_tag_names,
    set_transaction_tags,
)
from finance_app.modules.merchants.normalization import normalize_merchant
from finance_app.modules.merchants.repository import row_value
from finance_app.modules.merchants.sql_filters import (
    description_matches_any_candidate,
    merchant_description_candidates,
)
from finance_app.modules.settings.runtime import get_unknown_category


def apply_all_rules_job(undo_state: MutableMapping[str, Any]) -> str:
    """Apply all rules job."""
    with db_core_transaction() as conn:
        updated_count, undo_changes = apply_all_rules_to_transactions(conn, capture_undo=True)
        undo_state["changes"] = undo_changes

    return f"Rules applied to {updated_count} existing transaction" f"{'' if updated_count == 1 else 's'}."


def undo_apply_all_rules_job(undo_state: Mapping[str, Any]) -> str:
    """Undo apply all rules job."""
    changes = undo_state.get("changes") or []

    if not changes:
        return "No rule changes needed to be restored."

    restored_count = 0
    skipped_count = 0

    with db_core_transaction() as conn:
        for change in changes:
            cursor = restore_rule_change(conn, change)

            if cursor.rowcount:
                set_transaction_tags(
                    conn,
                    change["transaction_id"],
                    change.get("old_tags", []),
                    source=change["old_category_source"],
                    rule_id=change["old_category_rule_id"],
                )
                restored_count += 1
            else:
                skipped_count += 1

    message = f"Restored previous rule categories for {restored_count} transaction"
    message += "" if restored_count == 1 else "s"
    message += "."

    if skipped_count:
        message += (
            f" Skipped {skipped_count} transaction" f"{'' if skipped_count == 1 else 's'} that changed after the job."
        )

    return message


def preview_rule_matches(conn: Any, rule: Mapping[str, Any], limit: int) -> tuple[int, list[dict[str, Any]]]:
    """Preview rule matches."""
    keyword = normalize_merchant_description(rule["keyword"])
    if not keyword:
        return 0, []

    return preview_rule_matches_core(conn, rule, keyword, limit)


def preview_rule_matches_core(
    conn: Any,
    rule: Mapping[str, Any],
    keyword: str,
    limit: int,
) -> tuple[int, list[dict[str, Any]]]:
    """Return rule preview matches using portable Core row filtering."""
    rows = (
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
    match_count = 0
    sample: list[dict[str, Any]] = []

    for row in rows:
        if not rule_preview_matches_transaction(rule, row, keyword, conn=conn):
            continue

        match_count += 1
        if len(sample) >= limit:
            continue

        amount = money_to_float(row["amount"])
        sample.append(
            {
                "id": row["id"],
                "tx_date": row["tx_date"],
                "description": row["description"],
                "amount": amount,
                "amount_display": format_money(amount),
                "current_category": row["category"] or "",
            }
        )

    return match_count, sample


def rule_preview_matches_transaction(
    rule: Mapping[str, Any],
    transaction: Mapping[str, Any],
    keyword: str,
    conn: Any = None,
) -> bool:
    """Return whether a transaction matches the current preview filter."""
    if not rule_account_matches_transaction(rule, transaction):
        return False
    if not rule_direction_matches_transaction(rule, transaction["amount"]):
        return False

    rule_merchant_id = rule.get("merchant_id")
    if rule_merchant_id:
        transaction_merchant_id = row_value(transaction, "merchant_id")
        if transaction_merchant_id is None or int(transaction_merchant_id) != int(rule_merchant_id):
            return False
    else:
        normalized_merchant = normalize_merchant(transaction["description"], conn=conn)
        candidates = merchant_match_candidates(
            normalized_merchant.merchant_key,
            normalized_merchant.merchant_key,
            raw_description=transaction["description"],
        )
        if not any(keyword in candidate for candidate in candidates):
            return False

    amount = rounded_money_decimal(transaction["amount"])
    if is_income_category_name(rule["category"]) and amount >= 0:
        return False

    return rule_amount_matches(rule, amount)


def rule_matches_transaction(rule: Mapping[str, Any], transaction: Mapping[str, Any], conn: Any = None) -> bool:
    """Build matches transaction."""
    amount = rounded_money_decimal(transaction["amount"])
    if is_income_category_name(rule["category"]) and amount >= 0:
        return False
    if not rule_account_matches_transaction(rule, transaction):
        return False
    if not rule_direction_matches_transaction(rule, amount):
        return False

    rule_merchant_id = rule["merchant_id"] if "merchant_id" in rule.keys() else rule.get("merchant_id")
    if rule_merchant_id is not None:
        transaction_merchant_id = row_value(transaction, "merchant_id")
        return (
            transaction_merchant_id is not None
            and int(transaction_merchant_id) == int(rule_merchant_id)
            and rule_amount_matches(rule, amount)
        )

    keyword = normalize_merchant_description(rule["keyword"])
    normalized_merchant = normalize_merchant(transaction["description"], conn=conn)
    candidates = merchant_match_candidates(
        normalized_merchant.merchant_key,
        normalized_merchant.merchant_key,
        raw_description=transaction["description"],
    )
    return bool(keyword and any(keyword in candidate for candidate in candidates) and rule_amount_matches(rule, amount))


def apply_single_rule_to_transactions(conn: Any, rule: Mapping[str, Any]) -> int:
    """Apply single rule to transactions."""
    updated_count = 0
    unknown_category = get_unknown_category(conn)
    rows = active_transaction_rows(conn, rules=[rule])

    for row in rows:
        if not rule_matches_transaction(rule, row, conn=conn):
            continue

        rule_id = rule["id"] if "id" in rule.keys() else None
        category_id = (
            rule["category_id"]
            if "category_id" in rule.keys() and rule["category_id"] is not None
            else resolve_category_id(conn, rule["category"])
        )
        state = TransactionCategoryState(
            category=rule["category"],
            category_id=category_id,
            needs_review=0,
            assignment=category_assignment(
                rule["category"],
                unknown_category,
                CATEGORY_SOURCE_RULE,
                confidence=1.0,
                rule_id=rule_id,
                metadata=rule_assignment_metadata(
                    rule,
                    rule["category"],
                    rule.get("tags") or (),
                    confidence=1.0,
                    reason="single_rule_application",
                ),
            ),
            tags=tuple(rule.get("tags") or ()),
        )
        update_transaction_state(
            conn,
            row["id"],
            state,
            rule_transaction_kind(rule["category"], row["amount"], row["transaction_kind"]),
        )
        set_transaction_tags(
            conn,
            row["id"],
            list(state.tags),
            source=state.assignment.category_source,
            rule_id=state.assignment.category_rule_id,
        )
        updated_count += 1

    return updated_count


def apply_rule_where_it_wins_to_transactions(conn: Any, rule: Mapping[str, Any]) -> int:
    """Apply one rule only to transactions where it wins normal precedence.

    Uses the current category rule matcher to select the normal winning rule
    for each active transaction, then updates only rows where the selected
    rule is that winner. The caller owns the database transaction and receives
    the number of changed transaction rows.
    """
    selected_rule_id = rule["id"] if "id" in rule.keys() else rule.get("id")
    updated_count = 0
    unknown_category = get_unknown_category(conn)
    rules = get_category_rules(conn)
    rows = active_transaction_rows(conn, rules=rules)

    for row in rows:
        normalized_merchant = normalize_merchant(row["description"], conn=conn)
        winning_rule = match_category_rule(
            normalized_merchant.merchant_key,
            row["amount"],
            rules,
            merchant_candidate=normalized_merchant.merchant_key,
            raw_description=row["description"],
            merchant_id=row["merchant_id"],
            account_id=row["account_id"],
            transaction_kind=row["transaction_kind"],
        )
        winning_rule_id = winning_rule["id"] if winning_rule and "id" in winning_rule.keys() else None
        if winning_rule_id != selected_rule_id:
            continue

        category_id = (
            rule["category_id"]
            if "category_id" in rule.keys() and rule["category_id"] is not None
            else resolve_category_id(conn, rule["category"])
        )
        state = TransactionCategoryState(
            category=rule["category"],
            category_id=category_id,
            needs_review=0,
            assignment=category_assignment(
                rule["category"],
                unknown_category,
                CATEGORY_SOURCE_RULE,
                confidence=1.0,
                rule_id=selected_rule_id,
                metadata=rule_assignment_metadata(
                    rule,
                    rule["category"],
                    rule.get("tags") or (),
                    confidence=1.0,
                    reason="single_rule_winning_application",
                ),
            ),
            tags=tuple(rule.get("tags") or ()),
        )
        update_transaction_state(
            conn,
            row["id"],
            state,
            rule_transaction_kind(rule["category"], row["amount"], row["transaction_kind"]),
        )
        set_transaction_tags(
            conn,
            row["id"],
            list(state.tags),
            source=state.assignment.category_source,
            rule_id=state.assignment.category_rule_id,
        )
        updated_count += 1

    return updated_count


def apply_all_rules_to_transactions(conn: Any, capture_undo: bool = False) -> Any:
    """Apply all rules to transactions."""
    rules = get_category_rules(conn)
    if not rules:
        return (0, []) if capture_undo else 0

    unknown_category = get_unknown_category(conn)
    category_options = get_category_options(conn)
    updated_count = 0
    undo_changes: list[dict[str, Any]] = []
    rows = active_transaction_rows(conn, include_category_state=True, rules=rules)

    for row in rows:
        normalized_merchant = normalize_merchant(row["description"], conn=conn)
        rule = match_category_rule(
            normalized_merchant.merchant_key,
            row["amount"],
            rules,
            merchant_candidate=normalized_merchant.merchant_key,
            raw_description=row["description"],
            merchant_id=row["merchant_id"],
            account_id=row["account_id"],
            transaction_kind=row["transaction_kind"],
        )
        if rule is None:
            continue
        category = normalize_category(rule["category"], category_options)
        if category == unknown_category:
            continue

        rule_id = rule["id"] if "id" in rule.keys() else None
        category_id = (
            rule["category_id"]
            if "category_id" in rule.keys() and rule["category_id"] is not None
            else resolve_category_id(conn, category)
        )
        new_tags = list(rule.get("tags") or [])
        old_tags = get_transaction_tag_names(conn, row["id"])
        if (
            row["category"] == category
            and row["needs_review"] == 0
            and row["category_source"] == CATEGORY_SOURCE_RULE
            and row["category_rule_id"] == rule_id
            and old_tags == new_tags
        ):
            continue

        transaction_kind = rule_transaction_kind(category, row["amount"], row["transaction_kind"])
        old_state = TransactionCategorySnapshot.from_row(row, old_tags)
        state = TransactionCategoryState(
            category=category,
            category_id=category_id,
            needs_review=0,
            assignment=category_assignment(
                category,
                unknown_category,
                CATEGORY_SOURCE_RULE,
                confidence=1.0,
                rule_id=rule_id,
                metadata=rule_assignment_metadata(
                    rule,
                    category,
                    new_tags,
                    confidence=1.0,
                    reason="apply_all_rules",
                ),
            ),
            tags=tuple(new_tags),
        )
        new_state = TransactionCategorySnapshot(
            category=state.category,
            needs_review=state.needs_review,
            assignment=state.assignment,
            transaction_kind=transaction_kind,
            tags=state.tags,
            category_id=state.category_id,
        )
        if capture_undo:
            undo_changes.append(TransactionCategoryChange(row["id"], old_state, new_state).to_undo_record())

        update_transaction_state(conn, row["id"], state, transaction_kind)
        set_transaction_tags(
            conn,
            row["id"],
            list(state.tags),
            source=state.assignment.category_source,
            rule_id=state.assignment.category_rule_id,
        )
        updated_count += 1

    if capture_undo:
        return updated_count, undo_changes

    return updated_count


def active_transaction_rows(
    conn: Any,
    include_category_state: bool = False,
    rules: Sequence[Mapping[str, Any]] | None = None,
) -> Any:
    """Return non-ignored transaction rows used by rule matching."""
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


def rule_account_matches_transaction(rule: Mapping[str, Any], transaction: Mapping[str, Any]) -> bool:
    """Return whether a transaction satisfies a rule account constraint."""
    rule_account_id = rule["account_id"] if "account_id" in rule.keys() else rule.get("account_id")
    if rule_account_id is None:
        return True
    transaction_account_id = (
        transaction["account_id"] if "account_id" in transaction.keys() else transaction.get("account_id")
    )
    if transaction_account_id is None:
        return False
    return int(rule_account_id) == int(transaction_account_id)


def rule_direction(rule: Mapping[str, Any]) -> str:
    """Return the normalized direction constraint for a rule."""
    direction = rule["direction"] if "direction" in rule.keys() else rule.get("direction")
    direction = str(direction or CATEGORY_RULE_DIRECTION_ANY).strip().lower()
    if direction in {CATEGORY_RULE_DIRECTION_ANY, CATEGORY_RULE_DIRECTION_DEBIT, CATEGORY_RULE_DIRECTION_CREDIT}:
        return direction
    return CATEGORY_RULE_DIRECTION_ANY


def rule_direction_matches_transaction(rule: Mapping[str, Any], amount: MoneyValue | None) -> bool:
    """Return whether a transaction amount satisfies a rule direction."""
    direction = rule_direction(rule)
    if direction == CATEGORY_RULE_DIRECTION_ANY:
        return True
    if amount is None:
        return False
    amount = money_to_decimal(amount)
    if direction == CATEGORY_RULE_DIRECTION_DEBIT:
        return amount >= 0
    if direction == CATEGORY_RULE_DIRECTION_CREDIT:
        return amount < 0
    return True


def rule_assignment_metadata(
    rule: Mapping[str, Any],
    category: str,
    tags: Sequence[str],
    confidence: float,
    reason: str,
) -> dict[str, Any]:
    """Return persisted audit metadata for rule-application workflows."""
    rule_id = rule["id"] if "id" in rule.keys() else rule.get("id")
    amount_min = rule["amount_min"] if "amount_min" in rule.keys() else rule.get("amount_min")
    amount_max = rule["amount_max"] if "amount_max" in rule.keys() else rule.get("amount_max")
    return {
        "decision_source": DECISION_SOURCE_RULE,
        "reason": reason,
        "final_category": category,
        "final_tags": list(tags or ()),
        "final_confidence": confidence,
        "review_required": False,
        "matched_rule_id": rule_id,
        "rule_confidence": confidence,
        "rule": {
            "rule_id": rule_id,
            "keyword": rule.get("keyword"),
            "category": category,
            "tags": list(tags or ()),
            "confidence": confidence,
            "amount_min": money_to_float(amount_min) if amount_min is not None else None,
            "amount_max": money_to_float(amount_max) if amount_max is not None else None,
            "account_id": rule.get("account_id"),
            "direction": rule_direction(rule),
            "source": rule.get("source"),
        },
    }


def update_transaction_state(
    conn: Any,
    transaction_id: int,
    state: TransactionCategoryState,
    transaction_kind: str,
) -> None:
    """Persist a rule-assigned transaction category state."""
    conn.execute(
        update(transactions_table)
        .where(transactions_table.c.id == transaction_id)
        .values(
            category=state.category,
            category_id=(
                state.category_id if state.category_id is not None else resolve_category_id(conn, state.category)
            ),
            needs_review=0,
            category_source=state.assignment.category_source,
            category_confidence=state.assignment.category_confidence,
            category_rule_id=state.assignment.category_rule_id,
            category_metadata=state.assignment.category_metadata,
            categorized_at=state.assignment.categorized_at,
            reviewed_at=state.assignment.reviewed_at,
            transaction_kind=transaction_kind,
        )
    )


def restore_rule_change(conn: Any, change: Mapping[str, Any]) -> Any:
    """Restore one transaction if it still has the state written by a rule job."""
    old_category_id = change.get("old_category_id")
    if old_category_id is None:
        old_category_id = resolve_category_id(conn, change["old_category"])

    return conn.execute(
        update(transactions_table)
        .where(
            transactions_table.c.id == change["transaction_id"],
            nullable_equals(transactions_table.c.category, change["new_category"]),
            transactions_table.c.needs_review == change["new_needs_review"],
            transactions_table.c.category_source == change["new_category_source"],
            nullable_equals(transactions_table.c.category_rule_id, change["new_category_rule_id"]),
            nullable_equals(transactions_table.c.category_metadata, change["new_category_metadata"]),
            transactions_table.c.categorized_at == change["new_categorized_at"],
        )
        .values(
            category=change["old_category"],
            category_id=old_category_id,
            needs_review=change["old_needs_review"],
            category_source=change["old_category_source"],
            category_confidence=change["old_category_confidence"],
            category_rule_id=change["old_category_rule_id"],
            category_metadata=change["old_category_metadata"],
            categorized_at=change["old_categorized_at"],
            reviewed_at=change["old_reviewed_at"],
            transaction_kind=change.get("old_transaction_kind", TRANSACTION_KIND_EXPENSE),
        ),
    )


def nullable_equals(column: Any, value: object) -> Any:
    """Return a SQLAlchemy condition that treats None as SQL NULL equality."""
    return column.is_(None) if value is None else column == value


def rule_transaction_kind(category: str, amount: MoneyValue | None, current_kind: str | None = None) -> str:
    """Return transaction kind implied by a rule category and amount direction."""
    if category == TRANSFER_CATEGORY:
        return TRANSACTION_KIND_TRANSFER
    if current_kind == TRANSACTION_KIND_REFUND:
        return TRANSACTION_KIND_REFUND
    return TRANSACTION_KIND_INCOME if money_to_decimal(amount) < 0 else TRANSACTION_KIND_EXPENSE
