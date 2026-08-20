"""Read-only orchestration for category rule-change previews.

The helpers compare proposed rule changes against the reusable rule audit match
matrix without mutating rules or transactions.
"""

from collections.abc import Iterable, Mapping
from typing import Any

from sqlalchemy.engine import Connection

from finance_app.core.constants import CATEGORY_RULE_SOURCE_MANUAL, UNKNOWN_CATEGORY
from finance_app.modules.rules.audit import RuleRow, compute_rule_match_sets
from finance_app.modules.rules.audit_preview_impacts import (
    group_rule_change_impacts,
    preview_rule_change_impacts,
    preview_rule_set_change_impact,
    rule_change_preview_summary,
)
from finance_app.modules.rules.audit_preview_types import (
    PREVIEW_APPLY_ALL_RULES,
    PREVIEW_APPLY_WHERE_WINS,
    PREVIEW_APPROVE_RULE,
    PREVIEW_CREATE_RULE,
    PREVIEW_CREATED_RULE_ID,
    PREVIEW_DELETE_RULE,
    PREVIEW_EDIT_RULE,
    PREVIEW_FORCE_APPLY_RULE,
    PREVIEW_REMOVE_RULE,
    RuleChangePreview,
)
from finance_app.modules.settings.runtime import get_unknown_category


def preview_rule_change(
    conn: Connection,
    proposed_change: Mapping[str, Any],
    transaction_limit: int | None = None,
) -> RuleChangePreview | None:
    """Return a read-only impact preview for a supported rule change.

    Args:
        conn: Open SQLAlchemy Core connection.
        proposed_change: Mapping with ``type`` plus either ``rule_id`` for
            existing-rule actions or ``proposed_rule`` for create/edit previews.
        transaction_limit: Optional maximum number of newest transactions.

    Returns:
        A RuleChangePreview instance, or None when the target rule is unknown.

    Raises:
        ValueError: If the proposed change type is not supported.
    """
    action = normalize_preview_action(proposed_change.get("type"))
    supported_actions = {
        PREVIEW_CREATE_RULE,
        PREVIEW_DELETE_RULE,
        PREVIEW_EDIT_RULE,
        PREVIEW_APPROVE_RULE,
        PREVIEW_APPLY_WHERE_WINS,
        PREVIEW_FORCE_APPLY_RULE,
        PREVIEW_APPLY_ALL_RULES,
    }
    if action not in supported_actions:
        raise ValueError("Unsupported preview action.")

    audit_data = compute_rule_match_sets(
        conn,
        transaction_limit=transaction_limit,
        include_unknown=True,
    )
    rule_id = preview_rule_id(proposed_change)
    rule: RuleRow | None
    if action == PREVIEW_APPLY_ALL_RULES:
        rule = {}
    elif action == PREVIEW_CREATE_RULE:
        rule = {}
    elif rule_id is not None:
        rule = audit_data.rule_by_id.get(rule_id)
    else:
        return None
    if action not in {PREVIEW_APPLY_ALL_RULES, PREVIEW_CREATE_RULE} and rule is None:
        return None
    assert rule is not None
    proposed_rule: RuleRow | None = None
    if action == PREVIEW_CREATE_RULE:
        proposed_rule = preview_create_rule(proposed_change.get("proposed_rule"))
    elif action == PREVIEW_EDIT_RULE:
        proposed_rule = preview_edit_rule(rule, proposed_change.get("proposed_rule"))

    unknown_category = get_unknown_category(conn) or UNKNOWN_CATEGORY
    impacts = preview_rule_change_impacts(
        audit_data,
        action,
        rule_id,
        unknown_category,
        proposed_rule=proposed_rule,
    )
    grouped_impacts = group_rule_change_impacts(impacts)
    return RuleChangePreview(
        action=action,
        rule=rule,
        proposed_rule=proposed_rule,
        summary=rule_change_preview_summary(impacts, unknown_category),
        impacts=impacts,
        grouped_impacts=grouped_impacts,
        limited=audit_data.limited,
    )


def preview_rule_set_change(
    conn: Connection,
    action: str,
    proposed_rules: Iterable[Mapping[str, Any]],
    transaction_limit: int | None = None,
) -> RuleChangePreview:
    """Return a read-only impact preview for replacing the active rule set.

    Args:
        conn: Open SQLAlchemy Core connection.
        action: Preview action label used by the caller.
        proposed_rules: Complete set of rules that should be evaluated under
            the current matcher semantics.
        transaction_limit: Optional maximum number of newest transactions.

    Returns:
        A RuleChangePreview comparing current winners against proposed winners
        without mutating rules or transactions.
    """
    audit_data = compute_rule_match_sets(
        conn,
        transaction_limit=transaction_limit,
        include_unknown=True,
    )
    unknown_category = get_unknown_category(conn) or UNKNOWN_CATEGORY
    impacts = tuple(
        impact
        for audit in audit_data.transaction_audits
        if (
            impact := preview_rule_set_change_impact(
                audit,
                proposed_rules,
                unknown_category,
            )
        )
        is not None
    )
    return RuleChangePreview(
        action=action,
        rule={},
        proposed_rule=None,
        summary=rule_change_preview_summary(impacts, unknown_category),
        impacts=impacts,
        grouped_impacts=group_rule_change_impacts(impacts),
        limited=audit_data.limited,
    )


def normalize_preview_action(action: object) -> str:
    """Return the canonical preview action identifier."""
    action = str(action or "").strip()
    if action == PREVIEW_REMOVE_RULE:
        return PREVIEW_DELETE_RULE
    return action


def preview_rule_id(proposed_change: Mapping[str, Any]) -> int | None:
    """Return an optional target rule ID from a proposed change mapping."""
    value = proposed_change.get("rule_id")
    if value is None or value == "":
        return None
    return int(str(value))


def preview_create_rule(proposed_rule: object) -> RuleRow:
    """Return a proposed new rule mapping for create previews.

    Args:
        proposed_rule: Mapping parsed from the create-rule form.

    Returns:
        A rule mapping with a synthetic ID that can participate in normal rule
        matching without being persisted.

    Raises:
        ValueError: If no proposed rule payload was supplied.
    """
    if not isinstance(proposed_rule, dict):
        raise ValueError("Proposed rule data is required.")
    result = dict(proposed_rule)
    result["id"] = PREVIEW_CREATED_RULE_ID
    result["source"] = CATEGORY_RULE_SOURCE_MANUAL
    result["ai_approved"] = 0
    return result


def preview_edit_rule(current_rule: Mapping[str, Any], proposed_rule: object) -> RuleRow:
    """Return a proposed rule mapping for edit previews.

    Args:
        current_rule: Persisted rule mapping being changed.
        proposed_rule: Mapping parsed from the edit form.

    Returns:
        A rule mapping with the original rule id and unchanged metadata merged
        with the proposed match and assignment fields.

    Raises:
        ValueError: If no proposed rule payload was supplied.
    """
    if not isinstance(proposed_rule, dict):
        raise ValueError("Proposed rule data is required.")
    result = dict(current_rule)
    result.update(proposed_rule)
    result["id"] = current_rule["id"]
    return result
