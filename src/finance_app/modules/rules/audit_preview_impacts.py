"""Transaction-level impact helpers for rule-change previews.

The helpers compare current and proposed rule assignments for audited
transactions without mutating persisted rule or transaction state.
"""

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from finance_app.modules.categories.rules_matching import ScoredRuleMatch, select_winning_rule_match
from finance_app.modules.rules.audit import (
    RuleAuditData,
    TransactionRuleAudit,
    get_all_rule_matches_for_transaction,
    rule_id_from_match,
)
from finance_app.modules.rules.audit_preview_types import (
    PREVIEW_APPLY_ALL_RULES,
    PREVIEW_APPLY_WHERE_WINS,
    PREVIEW_CREATE_RULE,
    PREVIEW_DELETE_RULE,
    PREVIEW_EDIT_RULE,
    PREVIEW_FORCE_APPLY_RULE,
    RuleChangeImpact,
)


def preview_rule_change_impacts(
    audit_data: RuleAuditData,
    action: str,
    rule_id: int | None,
    unknown_category: str,
    proposed_rule: Mapping[str, Any] | None = None,
) -> tuple[RuleChangeImpact, ...]:
    """Return transaction-level impacts for a supported preview action."""
    if action == PREVIEW_CREATE_RULE:
        return tuple(
            impact
            for audit in audit_data.transaction_audits
            if (
                impact := preview_create_rule_impact(
                    audit,
                    proposed_rule,
                    unknown_category,
                )
            )
            is not None
        )
    if action == PREVIEW_DELETE_RULE:
        return tuple(
            impact
            for audit in audit_data.transaction_audits
            if (impact := preview_delete_rule_impact(audit, rule_id, unknown_category)) is not None
        )
    if action == PREVIEW_EDIT_RULE:
        return tuple(
            impact
            for audit in audit_data.transaction_audits
            if (
                impact := preview_edit_rule_impact(
                    audit,
                    rule_id,
                    proposed_rule,
                    unknown_category,
                )
            )
            is not None
        )
    if action == PREVIEW_APPLY_ALL_RULES:
        return tuple(
            impact
            for audit in audit_data.transaction_audits
            if (impact := preview_apply_all_rules_impact(audit, unknown_category)) is not None
        )
    if rule_id is None:
        return ()
    if action == PREVIEW_APPLY_WHERE_WINS:
        return tuple(
            impact
            for audit in audit_data.wins_by_rule_id.get(rule_id, ())
            if (impact := preview_apply_rule_impact(audit, rule_id, unknown_category)) is not None
        )
    if action == PREVIEW_FORCE_APPLY_RULE:
        return tuple(
            impact
            for audit in audit_data.matches_by_rule_id.get(rule_id, ())
            if (impact := preview_apply_rule_impact(audit, rule_id, unknown_category)) is not None
        )
    return ()


def preview_create_rule_impact(
    audit: TransactionRuleAudit,
    proposed_rule: Mapping[str, Any] | None,
    unknown_category: str,
) -> RuleChangeImpact | None:
    """Return one transaction impact for a create-rule preview, if relevant."""
    if proposed_rule is None:
        return None

    proposed_rule_audit = get_all_rule_matches_for_transaction(
        audit.transaction,
        (proposed_rule,),
        include_fuzzy=False,
    )
    if not proposed_rule_audit.matches:
        return None

    proposed_matches = audit.matches + proposed_rule_audit.matches
    proposed_winner = select_winning_rule_match(proposed_matches)
    current_category, current_tags = assignment_from_match(audit.winning_match, unknown_category)
    proposed_category, proposed_tags = assignment_from_match(proposed_winner, unknown_category)
    current_rule_id = rule_id_from_match(audit.winning_match)
    proposed_rule_id = rule_id_from_match(proposed_winner)
    return RuleChangeImpact(
        transaction=audit.transaction,
        current_winning_match=audit.winning_match,
        proposed_winning_match=proposed_winner,
        current_rule_id=current_rule_id,
        proposed_rule_id=proposed_rule_id,
        current_category=current_category,
        proposed_category=proposed_category,
        current_tags=current_tags,
        proposed_tags=proposed_tags,
        impact_group=impact_group(
            current_rule_id,
            proposed_rule_id,
            current_category,
            proposed_category,
            current_tags,
            proposed_tags,
        ),
    )


def preview_rule_set_change_impact(
    audit: TransactionRuleAudit,
    proposed_rules: Iterable[Mapping[str, Any]],
    unknown_category: str,
) -> RuleChangeImpact | None:
    """Return one impact for a proposed complete rule set, if relevant."""
    proposed_audit = get_all_rule_matches_for_transaction(
        audit.transaction,
        proposed_rules,
        include_fuzzy=False,
    )
    current_category, current_tags = assignment_from_match(audit.winning_match, unknown_category)
    proposed_category, proposed_tags = assignment_from_match(
        proposed_audit.winning_match,
        unknown_category,
    )
    current_rule_id = rule_id_from_match(audit.winning_match)
    proposed_rule_id = rule_id_from_match(proposed_audit.winning_match)
    group = impact_group(
        current_rule_id,
        proposed_rule_id,
        current_category,
        proposed_category,
        current_tags,
        proposed_tags,
    )
    if group == "no_material_change":
        return None

    return RuleChangeImpact(
        transaction=audit.transaction,
        current_winning_match=audit.winning_match,
        proposed_winning_match=proposed_audit.winning_match,
        current_rule_id=current_rule_id,
        proposed_rule_id=proposed_rule_id,
        current_category=current_category,
        proposed_category=proposed_category,
        current_tags=current_tags,
        proposed_tags=proposed_tags,
        impact_group=group,
    )


def preview_delete_rule_impact(
    audit: TransactionRuleAudit,
    rule_id: int | None,
    unknown_category: str,
) -> RuleChangeImpact | None:
    """Return one transaction impact for a delete-rule preview, if relevant."""
    current_rule_ids = {rule_id_from_match(match) for match in audit.matches}
    if rule_id not in current_rule_ids and audit.transaction.get("category_rule_id") != rule_id:
        return None

    proposed_matches = tuple(match for match in audit.matches if rule_id_from_match(match) != rule_id)
    proposed_winner = select_winning_rule_match(proposed_matches)
    current_category, current_tags = assignment_from_match(audit.winning_match, unknown_category)
    proposed_category, proposed_tags = assignment_from_match(proposed_winner, unknown_category)
    current_rule_id = rule_id_from_match(audit.winning_match)
    proposed_rule_id = rule_id_from_match(proposed_winner)
    return RuleChangeImpact(
        transaction=audit.transaction,
        current_winning_match=audit.winning_match,
        proposed_winning_match=proposed_winner,
        current_rule_id=current_rule_id,
        proposed_rule_id=proposed_rule_id,
        current_category=current_category,
        proposed_category=proposed_category,
        current_tags=current_tags,
        proposed_tags=proposed_tags,
        impact_group=impact_group(
            current_rule_id,
            proposed_rule_id,
            current_category,
            proposed_category,
            current_tags,
            proposed_tags,
        ),
    )


def preview_edit_rule_impact(
    audit: TransactionRuleAudit,
    rule_id: int | None,
    proposed_rule: Mapping[str, Any] | None,
    unknown_category: str,
) -> RuleChangeImpact | None:
    """Return one transaction impact for an edit-rule preview, if relevant."""
    if proposed_rule is None:
        return None

    current_rule_ids = {rule_id_from_match(match) for match in audit.matches}
    proposed_rule_audit = get_all_rule_matches_for_transaction(
        audit.transaction,
        (proposed_rule,),
        include_fuzzy=False,
    )
    proposed_rule_matches = proposed_rule_audit.matches
    if (
        rule_id not in current_rule_ids
        and not proposed_rule_matches
        and audit.transaction.get("category_rule_id") != rule_id
    ):
        return None

    proposed_matches = (
        tuple(match for match in audit.matches if rule_id_from_match(match) != rule_id) + proposed_rule_matches
    )
    proposed_winner = select_winning_rule_match(proposed_matches)
    current_category, current_tags = assignment_from_match(audit.winning_match, unknown_category)
    proposed_category, proposed_tags = assignment_from_match(proposed_winner, unknown_category)
    current_rule_id = rule_id_from_match(audit.winning_match)
    proposed_rule_id = rule_id_from_match(proposed_winner)
    return RuleChangeImpact(
        transaction=audit.transaction,
        current_winning_match=audit.winning_match,
        proposed_winning_match=proposed_winner,
        current_rule_id=current_rule_id,
        proposed_rule_id=proposed_rule_id,
        current_category=current_category,
        proposed_category=proposed_category,
        current_tags=current_tags,
        proposed_tags=proposed_tags,
        impact_group=impact_group(
            current_rule_id,
            proposed_rule_id,
            current_category,
            proposed_category,
            current_tags,
            proposed_tags,
        ),
    )


def preview_apply_rule_impact(
    audit: TransactionRuleAudit,
    rule_id: int,
    unknown_category: str,
) -> RuleChangeImpact | None:
    """Return one transaction impact for applying a selected rule."""
    selected_match = match_for_rule_id(audit, rule_id)
    if selected_match is None:
        return None

    current_category, current_tags = stored_assignment_from_transaction(
        audit.transaction,
        unknown_category,
    )
    proposed_category, proposed_tags = assignment_from_match(selected_match, unknown_category)
    current_rule_id = audit.transaction.get("category_rule_id")
    proposed_rule_id = rule_id_from_match(selected_match)
    return RuleChangeImpact(
        transaction=audit.transaction,
        current_winning_match=audit.winning_match,
        proposed_winning_match=selected_match,
        current_rule_id=current_rule_id,
        proposed_rule_id=proposed_rule_id,
        current_category=current_category,
        proposed_category=proposed_category,
        current_tags=current_tags,
        proposed_tags=proposed_tags,
        impact_group=impact_group(
            current_rule_id,
            proposed_rule_id,
            current_category,
            proposed_category,
            current_tags,
            proposed_tags,
        ),
    )


def preview_apply_all_rules_impact(audit: TransactionRuleAudit, unknown_category: str) -> RuleChangeImpact | None:
    """Return one transaction impact for applying the normal rule winner."""
    if audit.winning_match is None:
        return None

    current_category, current_tags = stored_assignment_from_transaction(
        audit.transaction,
        unknown_category,
    )
    proposed_category, proposed_tags = assignment_from_match(audit.winning_match, unknown_category)
    current_rule_id = audit.transaction.get("category_rule_id")
    proposed_rule_id = rule_id_from_match(audit.winning_match)
    group = impact_group(
        current_rule_id,
        proposed_rule_id,
        current_category,
        proposed_category,
        current_tags,
        proposed_tags,
    )
    if group == "no_material_change":
        return None

    return RuleChangeImpact(
        transaction=audit.transaction,
        current_winning_match=audit.winning_match,
        proposed_winning_match=audit.winning_match,
        current_rule_id=current_rule_id,
        proposed_rule_id=proposed_rule_id,
        current_category=current_category,
        proposed_category=proposed_category,
        current_tags=current_tags,
        proposed_tags=proposed_tags,
        impact_group=group,
    )


def assignment_from_match(match: ScoredRuleMatch | None, unknown_category: str) -> tuple[str, tuple[str, ...]]:
    """Return the category and tags assigned by a scored match."""
    if match is None:
        return unknown_category, ()
    return match.category, tuple(match.tags)


def stored_assignment_from_transaction(
    transaction: Mapping[str, Any],
    unknown_category: str,
) -> tuple[str, tuple[str, ...]]:
    """Return the persisted category and tags for a transaction audit row."""
    return str(transaction.get("category") or unknown_category), tuple(
        str(tag) for tag in (transaction.get("tags") or ())
    )


def match_for_rule_id(audit: TransactionRuleAudit, rule_id: int | None) -> ScoredRuleMatch | None:
    """Return one scored match for a rule ID inside an audited transaction."""
    return next(
        (match for match in audit.matches if rule_id_from_match(match) == rule_id),
        None,
    )


def impact_group(
    current_rule_id: int | None,
    proposed_rule_id: int | None,
    current_category: str,
    proposed_category: str,
    current_tags: tuple[str, ...],
    proposed_tags: tuple[str, ...],
) -> str:
    """Return the mutually exclusive transaction-level preview group."""
    if current_category != proposed_category:
        return "category_change"
    if current_tags != proposed_tags:
        return "tags_change"
    if current_rule_id != proposed_rule_id:
        return "winning_rule_change"
    return "no_material_change"


def group_rule_change_impacts(impacts: Iterable[RuleChangeImpact]) -> dict[str, tuple[RuleChangeImpact, ...]]:
    """Return transaction preview impacts grouped by display category."""
    grouped: defaultdict[str, list[RuleChangeImpact]] = defaultdict(list)
    for impact in impacts:
        grouped[impact.impact_group].append(impact)
    return {
        key: tuple(grouped.get(key, ()))
        for key in (
            "category_change",
            "tags_change",
            "winning_rule_change",
            "no_material_change",
        )
    }


def rule_change_preview_summary(impacts: Sequence[RuleChangeImpact], unknown_category: str) -> dict[str, int]:
    """Return aggregate counts for a rule change preview."""
    return {
        "total_affected_transactions": len(impacts),
        "winning_rule_changes": sum(1 for impact in impacts if impact.current_rule_id != impact.proposed_rule_id),
        "category_changes": sum(1 for impact in impacts if impact.current_category != impact.proposed_category),
        "tag_changes": sum(1 for impact in impacts if impact.current_tags != impact.proposed_tags),
        "would_become_unknown": sum(1 for impact in impacts if impact.proposed_category == unknown_category),
        "newly_require_review": sum(1 for impact in impacts if impact.proposed_category == unknown_category),
    }
