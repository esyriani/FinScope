"""Route-facing workflows for rule management.

The rules package keeps persistence helpers in ``repository.py`` and pure matching
logic in ``engine.py``. This module owns transaction-scoped route use cases that
need to coordinate audit presenters, rule mutations, and background jobs.
"""

from collections.abc import Mapping, MutableMapping
from typing import Any

from finance_app.background.runner import submit_background_job
from finance_app.core.config import settings
from finance_app.core.constants import CATEGORY_SOURCE_RULE
from finance_app.database.engine import db_core_transaction
from finance_app.modules.categories.repository import resolve_category_id
from finance_app.modules.categories.service import (
    get_category_options,
    get_category_rules,
    match_category_rule,
    normalize_category,
)
from finance_app.modules.categories.sources import (
    TransactionCategoryChange,
    TransactionCategorySnapshot,
    TransactionCategoryState,
    category_assignment,
)
from finance_app.modules.categories.taxonomy import get_transaction_tag_names, set_transaction_tags
from finance_app.modules.merchants.normalization import normalize_merchant, normalize_merchant_description
from finance_app.modules.rules.audit_presenter import (
    build_rule_audit_context,
    build_rule_change_preview_context,
    build_rule_detail_context,
    build_rule_import_preview_context,
    build_rule_overlap_detail_context,
)
from finance_app.modules.rules.engine import (
    rule_assignment_metadata,
    rule_matches_transaction,
    rule_preview_matches_transaction,
    rule_transaction_kind,
)
from finance_app.modules.rules.import_export import import_rules_job, undo_import_rules_job
from finance_app.modules.rules.presenter import present_rule_preview_transaction
from finance_app.modules.rules.queries import active_transaction_rows, fetch_rule_preview_candidates
from finance_app.modules.rules.repository import restore_rule_change, update_transaction_state
from finance_app.modules.rules.service import (
    approve_automatic_rule,
    count_rule_transaction_references,
    create_rule_from_form,
    delete_rule,
    get_rule_for_apply,
    preview_rule_from_form,
    update_rule_from_form,
)
from finance_app.modules.settings.runtime import get_int_setting, get_unknown_category


def apply_all_rules_job(undo_state: MutableMapping[str, Any]) -> str:
    """Apply all category rules in a background job and capture undo state."""
    with db_core_transaction() as conn:
        updated_count, undo_changes = apply_all_rules_to_transactions(conn, capture_undo=True)
        undo_state["changes"] = undo_changes

    return f"Rules applied to {updated_count} existing transaction" f"{'' if updated_count == 1 else 's'}."


def undo_apply_all_rules_job(undo_state: Mapping[str, Any]) -> str:
    """Restore categories changed by a previous apply-all-rules job."""
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
    """Return rule preview matches using SQL candidate narrowing and pure rule semantics."""
    keyword = normalize_merchant_description(rule["keyword"])
    if not keyword:
        return 0, []

    rows = fetch_rule_preview_candidates(conn, rule, keyword)
    match_count = 0
    sample: list[dict[str, Any]] = []

    for row in rows:
        if not rule_preview_matches_transaction(rule, row, keyword):
            continue

        match_count += 1
        if len(sample) >= limit:
            continue

        sample.append(present_rule_preview_transaction(row))

    return match_count, sample


def apply_single_rule_to_transactions(conn: Any, rule: Mapping[str, Any]) -> int:
    """Apply a single rule to every active matching transaction."""
    updated_count = 0
    unknown_category = get_unknown_category(conn)
    rows = active_transaction_rows(conn, rules=[rule])

    for row in rows:
        if not rule_matches_transaction(rule, row):
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
        normalized_merchant = normalize_merchant(row["description"])
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
    """Apply all rules to active transactions."""
    rules = get_category_rules(conn)
    if not rules:
        return (0, []) if capture_undo else 0

    unknown_category = get_unknown_category(conn)
    category_options = get_category_options(conn)
    updated_count = 0
    undo_changes: list[dict[str, Any]] = []
    rows = active_transaction_rows(conn, include_category_state=True, rules=rules)

    for row in rows:
        normalized_merchant = normalize_merchant(row["description"])
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


def build_rule_audit_page_context(args: Any) -> dict[str, Any]:
    """Build the rule audit page context inside one database transaction."""
    with db_core_transaction() as conn:
        return build_rule_audit_context(
            conn,
            args,
            transaction_limit=rule_audit_transaction_limit(conn),
        )


def build_rule_overlap_page_context(rule_a_id: int, rule_b_id: int, args: Any) -> dict[str, Any] | None:
    """Build the overlapping-rule detail context inside one transaction."""
    with db_core_transaction() as conn:
        return build_rule_overlap_detail_context(
            conn,
            rule_a_id,
            rule_b_id,
            args,
            transaction_limit=rule_audit_transaction_limit(conn),
        )


def build_rule_detail_page_context(rule_id: int) -> dict[str, Any] | None:
    """Build the one-rule audit detail context inside one transaction."""
    with db_core_transaction() as conn:
        return build_rule_detail_context(
            conn,
            rule_id,
            transaction_limit=rule_audit_transaction_limit(conn),
        )


def build_rule_audit_preview_page_context(action: str, rule_id: int | None, form: Any) -> dict[str, Any] | None:
    """Build a read-only impact preview for a pending rule action."""
    with db_core_transaction() as conn:
        proposed_rule = preview_rule_from_form(conn, form) if action in {"create_rule", "edit_rule"} else None
        return build_rule_change_preview_context(
            conn,
            action,
            rule_id,
            proposed_rule=proposed_rule,
            transaction_limit=rule_audit_transaction_limit(conn),
        )


def create_rule_action(form: Any) -> tuple[int, str]:
    """Create a manual rule from submitted form data."""
    with db_core_transaction() as conn:
        return create_rule_from_form(conn, form)


def preview_rule_action(form: Any) -> dict[str, Any]:
    """Return a JSON-ready preview for submitted rule fields."""
    with db_core_transaction() as conn:
        rule = preview_rule_from_form(conn, form)
        preview_limit = get_int_setting(conn, "rule_preview_limit", settings.default_rule_preview_limit)
        match_count, sample = preview_rule_matches(conn, rule, limit=preview_limit)

    return {
        "ok": True,
        "keyword": rule["keyword"],
        "category": rule["category"],
        "match_count": match_count,
        "transactions": sample,
    }


def build_rules_import_preview(raw_text: str, mode: str, filename: str) -> dict[str, Any]:
    """Build the import preview for a rules CSV."""
    with db_core_transaction() as conn:
        return build_rule_import_preview_context(
            conn,
            raw_text,
            mode,
            filename,
            transaction_limit=rule_audit_transaction_limit(conn),
        )


def queue_rules_import(raw_text: str, mode: str, filename: str) -> str:
    """Validate and queue a rules CSV import job."""
    build_rules_import_preview(raw_text, mode, filename)
    undo_state: dict[str, Any] = {}
    return submit_background_job(
        f"Import rules from {filename}",
        import_rules_job,
        raw_text,
        mode,
        undo_state,
        undo_handler=undo_import_rules_job,
        undo_args=(undo_state,),
    )


def update_rule_action(rule_id: int, form: Any) -> None:
    """Update a rule from submitted form data."""
    with db_core_transaction() as conn:
        update_rule_from_form(conn, rule_id, form)


def approve_rule_action(rule_id: int) -> tuple[str, bool]:
    """Approve an automatic rule."""
    with db_core_transaction() as conn:
        return approve_automatic_rule(conn, rule_id)


def apply_rule_action(rule_id: int, confirmed: bool, mode_value: object) -> dict[str, Any]:
    """Apply one rule according to the submitted confirmation and mode."""
    with db_core_transaction() as conn:
        rule = get_rule_for_apply(conn, rule_id)
        if rule is None:
            return {"ok": False, "status": 404, "message": "Rule not found."}

        if not confirmed:
            return {"ok": False, "status": 400, "message": "Preview apply before applying a rule."}

        mode = str(mode_value or "apply_where_wins").strip() or "apply_where_wins"
        if mode == "force_apply_rule":
            updated_count = apply_single_rule_to_transactions(conn, rule)
            message = "Rule force-applied to {count} existing transactions."
        elif mode == "apply_where_wins":
            updated_count = apply_rule_where_it_wins_to_transactions(conn, rule)
            message = "Rule applied where it has priority to {count} existing transactions."
        else:
            return {"ok": False, "status": 400, "message": "Unsupported apply mode."}

    return {
        "ok": True,
        "action": "apply",
        "rule_id": rule_id,
        "mode": mode,
        "updated_count": updated_count,
        "message": message,
        "params": {"count": updated_count},
    }


def queue_apply_all_rules(confirmed: bool) -> dict[str, Any]:
    """Queue apply-all-rules work after the preview confirmation."""
    if not confirmed:
        return {"ok": False, "message": "Preview apply before applying all rules."}

    undo_state: dict[str, Any] = {}
    job_id = submit_background_job(
        "Apply all category rules",
        apply_all_rules_job,
        undo_state,
        undo_handler=undo_apply_all_rules_job,
        undo_args=(undo_state,),
    )
    return {"ok": True, "job_id": job_id}


def delete_rule_action(rule_id: int, confirmed: bool) -> dict[str, Any]:
    """Delete one rule when confirmation requirements are satisfied."""
    with db_core_transaction() as conn:
        reference_count = count_rule_transaction_references(conn, rule_id)
        if not confirmed and reference_count:
            return {"ok": False, "status": 400, "message": "Preview deletion before deleting a rule."}

        deleted = delete_rule(conn, rule_id)

    return {
        "ok": deleted,
        "action": "delete",
        "rule_id": rule_id,
        "status": 200 if deleted else 404,
        "message": "Rule deleted." if deleted else "Rule not found.",
    }


def rule_audit_transaction_limit(conn: Any) -> int:
    """Return the configured newest-transaction cap for rule audit analysis."""
    return get_int_setting(
        conn,
        "rule_audit_transaction_limit",
        settings.default_rule_audit_transaction_limit,
    )
