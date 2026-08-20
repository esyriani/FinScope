"""Route-facing workflows for rule management.

The rules package keeps persistence helpers in ``service.py`` and pure matching
logic in ``engine.py``. This module owns transaction-scoped route use cases that
need to coordinate audit presenters, rule mutations, and background jobs.
"""

from typing import Any

from finance_app.background.runner import submit_background_job
from finance_app.core.config import settings
from finance_app.database.engine import db_core_transaction
from finance_app.modules.rules.audit_presenter import (
    build_rule_audit_context,
    build_rule_change_preview_context,
    build_rule_detail_context,
    build_rule_import_preview_context,
    build_rule_overlap_detail_context,
)
from finance_app.modules.rules.engine import (
    apply_all_rules_job,
    apply_rule_where_it_wins_to_transactions,
    apply_single_rule_to_transactions,
    preview_rule_matches,
    undo_apply_all_rules_job,
)
from finance_app.modules.rules.import_export import import_rules_job, undo_import_rules_job
from finance_app.modules.rules.service import (
    approve_automatic_rule,
    count_rule_transaction_references,
    create_rule_from_form,
    delete_rule,
    get_rule_for_apply,
    preview_rule_from_form,
    update_rule_from_form,
)
from finance_app.modules.settings.runtime import get_int_setting


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
