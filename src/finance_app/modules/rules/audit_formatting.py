"""Rule audit row formatting helpers.

Formats rule audit findings, preview impacts, labels, badges, and explanatory
text for Flask templates. The helpers are presentation-only and do not mutate
database state.
"""

from typing import Any

from flask import url_for

from finance_app.core.constants import (
    CATEGORY_RULE_DIRECTION_ANY,
    CATEGORY_RULE_DIRECTION_LABELS,
    CATEGORY_RULE_SOURCE_AUTOMATIC,
    CATEGORY_RULE_SOURCE_MANUAL,
)
from finance_app.core.i18n import gettext
from finance_app.modules.rules.audit import (
    OVERLAP_CATEGORY_CONFLICT,
    OVERLAP_CRITICAL_CONFLICT,
    OVERLAP_HARMLESS,
    OVERLAP_TAG_DIFFERENCE,
    STALE_STALE,
    STALE_UNUSED,
    compute_rule_specificity_score,
    precedence_win_reason,
    rule_id_from_match,
    transaction_was_manually_reviewed,
)
from finance_app.modules.rules.import_export import (
    RULE_IMPORT_MODE_ADD,
    RULE_IMPORT_MODE_OVERRIDE,
)

SEVERITY_LABELS = {
    OVERLAP_HARMLESS: "Harmless overlap",
    OVERLAP_TAG_DIFFERENCE: "Tag difference",
    OVERLAP_CATEGORY_CONFLICT: "Category conflict",
    OVERLAP_CRITICAL_CONFLICT: "Critical conflict",
}
SEVERITY_BADGE_CLASSES = {
    OVERLAP_HARMLESS: "text-bg-success",
    OVERLAP_TAG_DIFFERENCE: "text-bg-info",
    OVERLAP_CATEGORY_CONFLICT: "text-bg-warning",
    OVERLAP_CRITICAL_CONFLICT: "text-bg-danger",
}
STALE_LABELS = {
    STALE_UNUSED: "Unused",
    STALE_STALE: "Stale",
}
STALE_BADGE_CLASSES = {
    STALE_UNUSED: "text-bg-secondary",
    STALE_STALE: "text-bg-secondary",
}
SUGGESTED_ACTION_LABELS = {
    "mark harmless or merge": "Mark as harmless",
    "inspect tags": "Inspect tag difference",
    "inspect manually": "Review category conflict",
    "edit or narrow": "Edit or narrow rule",
    "delete or narrow": "Review removal impact",
    "inspect overlaps": "Inspect overlapping rules",
    "review or remove": "Review unused rule",
    "review": "Review stale rule",
}
SUGGESTED_ACTION_BADGE_CLASSES = {
    "Mark as harmless": "text-bg-success",
    "Inspect tag difference": "text-bg-info",
    "Review category conflict": "text-bg-warning",
    "Edit or narrow rule": "text-bg-warning",
    "Review removal impact": "text-bg-secondary",
    "Inspect overlapping rules": "text-bg-info",
    "Review unused rule": "text-bg-secondary",
    "Review stale rule": "text-bg-secondary",
}
IMPACT_GROUP_LABELS = {
    "category_change": "Category would change",
    "tags_change": "Tags would change",
    "winning_rule_change": "Applied rule would change",
    "no_material_change": "No material change",
}
IMPACT_GROUP_BADGE_CLASSES = {
    "category_change": "text-bg-warning",
    "tags_change": "text-bg-info",
    "winning_rule_change": "text-bg-secondary",
    "no_material_change": "text-bg-success",
}


def present_overlap(overlap: Any, rule_by_id: Any) -> Any:
    """Return a display mapping for one overlapping rule pair."""
    rule_a_id = overlap.rule_a["id"]
    rule_b_id = overlap.rule_b["id"]
    winner_ids = [
        rule_id
        for rule_id, _count in sorted(
            overlap.winning_rule_counts.items(),
            key=lambda item: (-item[1], rule_label(rule_by_id.get(item[0], {}))),
        )
    ]
    loser_ids = [rule_id for rule_id in (rule_a_id, rule_b_id) if rule_id not in winner_ids]
    winning_side_label = overlap_winning_side_label(
        overlap.winning_rule_counts,
        rule_a_id,
        rule_b_id,
    )
    action_label = suggested_action_label(overlap.suggested_action)
    return {
        "rule_a": present_rule(overlap.rule_a),
        "rule_b": present_rule(overlap.rule_b),
        "shared_count": overlap.shared_count,
        "severity": overlap.severity,
        "severity_label": SEVERITY_LABELS.get(overlap.severity, "Unknown"),
        "severity_badge_class": SEVERITY_BADGE_CLASSES.get(
            overlap.severity,
            "text-bg-secondary",
        ),
        "winning_rule_label": joined_rule_count_labels(
            overlap.winning_rule_counts,
            rule_by_id,
        ),
        "winning_rule_side_label": winning_side_label,
        "losing_rule_label": joined_rule_labels(loser_ids, rule_by_id) or "Mixed",
        "rule_a_applied_count": overlap.rule_a_applied_count,
        "rule_b_applied_count": overlap.rule_b_applied_count,
        "suggested_action": overlap.suggested_action,
        "suggested_action_label": action_label,
        "suggested_action_badge_class": suggested_action_badge_class(action_label),
        "suggested_action_reason": overlap_action_reason(overlap),
        "detail_url_rule_a_id": rule_a_id,
        "detail_url_rule_b_id": rule_b_id,
    }


def present_shared_transaction(audit: Any, winning_rule_id: Any, losing_rule_id: Any, rule_by_id: Any) -> Any:
    """Return a display mapping for one shared matching transaction."""
    match_by_rule_id = {rule_id_from_match(match): match for match in audit.matches}
    winning_rule_match = match_by_rule_id[winning_rule_id]
    losing_rule_match = match_by_rule_id[losing_rule_id]
    actual_winning_rule_id = rule_id_from_match(audit.winning_match)
    winner_rule = rule_by_id.get(actual_winning_rule_id, {})
    transaction = audit.transaction
    return {
        "transaction": transaction,
        "current_tag_label": ", ".join(transaction.get("tags") or []) or "-",
        "manual_reviewed": transaction_was_manually_reviewed(transaction),
        "winning_rule_match": present_match(winning_rule_match),
        "losing_rule_match": present_match(losing_rule_match),
        "rule_a_match": present_match(winning_rule_match),
        "rule_b_match": present_match(losing_rule_match),
        "winning_rule_label": rule_label(winner_rule) if winner_rule else "-",
        "winner_agrees_with_current_category": (
            audit.winning_match is not None and audit.winning_match.category == transaction.get("category")
        ),
    }


def present_match(match: Any) -> Any:
    """Return a display mapping for one scored rule match."""
    return {
        "category": match.category,
        "tag_label": ", ".join(match.tags) or "-",
        "confidence": match.confidence,
        "match_score": match.match_score,
        "specificity": match.specificity,
        "specificity_label": specificity_label(match.specificity),
    }


def present_rule_with_specificity_comparison(rule: Any, other_rule: Any) -> Any:
    """Return a presented rule with a human-readable specificity comparison."""
    presented = present_rule(rule)
    other_specificity = compute_rule_specificity_score(other_rule)
    if presented["specificity"] > other_specificity:
        presented["specificity_comparison_label"] = "More precise"
        presented["specificity_comparison_badge_class"] = "text-bg-success"
    elif presented["specificity"] < other_specificity:
        presented["specificity_comparison_label"] = "Less precise"
        presented["specificity_comparison_badge_class"] = "text-bg-secondary"
    else:
        presented["specificity_comparison_label"] = "Same precision"
        presented["specificity_comparison_badge_class"] = "text-bg-info"
    return presented


def present_rule_interactions(interactions: Any, rule_by_id: Any) -> Any:
    """Return display rows for per-rule win/loss interactions."""
    return [
        {
            "rule": present_rule(rule_by_id[rule_id]),
            "shared_count": values["shared_count"],
            "conflicting_count": values["conflicting_count"],
        }
        for rule_id, values in sorted(
            interactions.items(),
            key=lambda item: (-item[1]["shared_count"], rule_label(rule_by_id.get(item[0], {}))),
        )
        if rule_id in rule_by_id
    ]


def present_specificity_warning(warning: Any) -> Any:
    """Return a display mapping for one specificity or precedence warning."""
    action_label = suggested_action_label(warning.suggested_action)
    return {
        "broad_rule": present_rule(warning.broad_rule),
        "specific_rule": present_rule(warning.specific_rule),
        "shared_count": warning.shared_count,
        "reason": warning.reason,
        "conflicting_count": warning.conflicting_count,
        "suggested_action": warning.suggested_action,
        "suggested_action_label": action_label,
        "suggested_action_badge_class": suggested_action_badge_class(action_label),
        "suggested_action_reason": "A broader rule is applied even though another matching rule is more precise.",
    }


def present_impact_group(key: Any, impacts: Any) -> Any:
    """Return a display mapping for a preview impact group."""
    return {
        "key": key,
        "label": IMPACT_GROUP_LABELS.get(key, key),
        "badge_class": IMPACT_GROUP_BADGE_CLASSES.get(key, "text-bg-secondary"),
        "count": len(impacts),
        "impacts": [present_rule_change_impact(impact) for impact in impacts],
    }


def present_rule_change_impact(impact: Any) -> Any:
    """Return a display mapping for one transaction-level preview impact."""
    return {
        "transaction": impact.transaction,
        "current_winner": present_preview_match(impact.current_winning_match),
        "proposed_winner": present_preview_match(impact.proposed_winning_match),
        "current_category": impact.current_category,
        "proposed_category": impact.proposed_category,
        "current_tags": ", ".join(impact.current_tags) or "-",
        "proposed_tags": ", ".join(impact.proposed_tags) or "-",
    }


def present_preview_match(match: Any) -> Any:
    """Return a compact display mapping for a preview winner."""
    if match is None:
        return {
            "rule_id": None,
            "label": gettext("No matching rule"),
            "category": "",
            "tags": "-",
        }

    rule = present_rule(match.rule)
    return {
        "rule_id": rule["id"],
        "label": rule["label"],
        "category": match.category,
        "tags": ", ".join(match.tags) or "-",
    }


def present_shadowed_rule(finding: Any, rule_by_id: Any) -> Any:
    """Return a display mapping for one shadowed-rule finding."""
    shadowing_rule = rule_by_id.get(finding.most_common_shadowing_rule_id, {})
    action_label = suggested_action_label(finding.suggested_action)
    return {
        "rule": present_rule(finding.rule),
        "total_matches": finding.total_matches,
        "total_wins": finding.total_wins,
        "total_losses": finding.total_losses,
        "most_common_shadowing_rule": present_rule(shadowing_rule) if shadowing_rule else None,
        "conflicting_loss_count": finding.conflicting_loss_count,
        "suggested_action": finding.suggested_action,
        "suggested_action_label": action_label,
        "suggested_action_badge_class": suggested_action_badge_class(action_label),
        "suggested_action_reason": shadowed_action_reason(finding, shadowing_rule),
    }


def present_stale_rule(finding: Any) -> Any:
    """Return a display mapping for one stale or unused rule finding."""
    action_label = suggested_action_label(finding.suggested_action)
    return {
        "rule": present_rule(finding.rule),
        "status": finding.status,
        "status_label": STALE_LABELS.get(finding.status, "Stale"),
        "status_badge_class": STALE_BADGE_CLASSES.get(finding.status, "text-bg-secondary"),
        "total_matches": finding.total_matches,
        "total_wins": finding.total_wins,
        "stored_applied_count": finding.stored_applied_count,
        "last_matched_date": finding.last_matched_date,
        "recent_matches": finding.recent_matches,
        "suggested_action": finding.suggested_action,
        "suggested_action_label": action_label,
        "suggested_action_badge_class": suggested_action_badge_class(action_label),
        "suggested_action_reason": stale_action_reason(finding),
    }


def suggested_action_label(action: Any) -> Any:
    """Return clearer user-facing wording for an advisory audit action."""
    return SUGGESTED_ACTION_LABELS.get(action, action)


def suggested_action_badge_class(label: Any) -> Any:
    """Return a consistent badge class for one suggested action label."""
    return SUGGESTED_ACTION_BADGE_CLASSES.get(label, "text-bg-secondary")


def overlap_action_reason(overlap: Any) -> Any:
    """Return a short reason explaining the recommended overlap action."""
    rule_a_category = overlap.rule_a.get("category") or ""
    rule_b_category = overlap.rule_b.get("category") or ""
    rule_a_tags = rule_tags_label(overlap.rule_a)
    rule_b_tags = rule_tags_label(overlap.rule_b)
    if overlap.severity == OVERLAP_HARMLESS:
        return gettext("Both rules assign the same category and tags.")
    if overlap.severity == OVERLAP_TAG_DIFFERENCE:
        return gettext(
            "Both rules assign {category}, but the tag sets differ: {tags_a} versus {tags_b}.",
            category=rule_a_category,
            tags_a=rule_a_tags,
            tags_b=rule_b_tags,
        )
    if overlap.severity == OVERLAP_CRITICAL_CONFLICT:
        return gettext("These rules assign different categories across multiple shared transactions.")
    if overlap.severity == OVERLAP_CATEGORY_CONFLICT:
        return gettext(
            "These rules assign different categories: {category_a} versus {category_b}.",
            category_a=rule_a_category,
            category_b=rule_b_category,
        )
    return gettext("Review the shared transactions before changing either rule.")


def shadowed_action_reason(finding: Any, shadowing_rule: Any) -> Any:
    """Return a short reason explaining a shadowed-rule recommendation."""
    reason = gettext(
        "This rule matched {matches} historical transactions and was applied to {wins}.",
        matches=finding.total_matches,
        wins=finding.total_wins,
    )
    if shadowing_rule:
        reason = gettext(
            "{reason} It is most often skipped for {rule}.",
            reason=reason,
            rule=rule_label(shadowing_rule),
        )
    return reason


def stale_action_reason(finding: Any) -> Any:
    """Return a short reason explaining a stale or unused rule recommendation."""
    if finding.status == STALE_UNUSED:
        return gettext("No historical transaction matches this rule.")
    if finding.last_matched_date:
        return gettext(
            "This rule has not matched since {date}.",
            date=finding.last_matched_date,
        )
    return gettext("This rule has not matched recently.")


def rule_tags_label(rule: Any) -> Any:
    """Return a readable tag label for a raw or presented rule."""
    tags = rule.get("tags") or []
    return ", ".join(tags) or "-"


def recommended_next_step(summary: Any, shadowed_rows: Any) -> Any:
    """Return the highest-priority recommended next step for the audit page."""
    if summary.get("critical_conflict_overlaps", 0):
        return {
            "title": "Recommended next step",
            "headline": "Review critical conflicts first.",
            "detail": "These overlaps assign different categories and affect multiple transactions.",
            "href": url_for(
                "rules.audit_rules",
                overlap_filter=OVERLAP_CRITICAL_CONFLICT,
                overlap_page=1,
                open="rule-overlap-findings",
            ),
            "target": "",
        }
    category_conflicts = summary.get("category_conflict_overlaps", 0)
    if category_conflicts:
        return {
            "title": "Recommended next step",
            "headline": "Review category conflicts.",
            "detail": "These overlaps assign different categories, so only the applied rule is used.",
            "href": url_for(
                "rules.audit_rules",
                overlap_filter=OVERLAP_CATEGORY_CONFLICT,
                overlap_page=1,
                open="rule-overlap-findings",
            ),
            "target": "",
        }
    if any(row.get("conflicting_loss_count", 0) for row in shadowed_rows):
        return {
            "title": "Recommended next step",
            "headline": "Review rules skipped by priority with conflicts.",
            "detail": "These rules match transactions but are skipped for rules that assign different categories.",
            "href": "#shadowed-rule-findings",
            "target": "#shadowed-rule-findings",
        }
    return None


def build_win_explanation(shared_audits: Any, winning_rule_id: Any, losing_rule_id: Any, rule_by_id: Any) -> Any:
    """Return a readable explanation of why the displayed overlap winner wins."""
    for audit in shared_audits:
        match_by_rule_id = {rule_id_from_match(match): match for match in audit.matches}
        winning_match = match_by_rule_id.get(winning_rule_id)
        losing_match = match_by_rule_id.get(losing_rule_id)
        if winning_match is None or losing_match is None:
            continue
        reason = precedence_win_reason(winning_match, losing_match)
        winning_rule = rule_by_id.get(winning_rule_id, {})
        losing_rule = rule_by_id.get(losing_rule_id, {})
        return {
            "winner_label": rule_label(winning_rule),
            "loser_label": rule_label(losing_rule),
            "reason": reason,
            "sentence": win_explanation_sentence(winning_rule, reason),
            "winner_confidence": winning_match.confidence,
            "loser_confidence": losing_match.confidence,
            "winner_match_score": winning_match.match_score,
            "loser_match_score": losing_match.match_score,
            "winner_specificity": specificity_comparison_label(
                winning_match.specificity,
                losing_match.specificity,
            ),
            "loser_specificity": specificity_comparison_label(
                losing_match.specificity,
                winning_match.specificity,
            ),
        }
    return {}


def win_explanation_sentence(winning_rule: Any, reason: Any) -> Any:
    """Return one sentence explaining the winning rule decision."""
    label = rule_label(winning_rule)
    return {
        "Higher confidence": gettext(
            "{label} is applied because it has higher confidence.",
            label=label,
        ),
        "Higher match score": gettext(
            "{label} is applied because it has a higher match score.",
            label=label,
        ),
        "Higher specificity": gettext(
            "{label} is applied because it is more precise.",
            label=label,
        ),
        "Stable precedence": gettext(
            "{label} is applied by the stable deterministic tie-breaker.",
            label=label,
        ),
    }.get(
        reason,
        gettext("{label} is applied under the current priority model.", label=label),
    )


def specificity_comparison_label(left: Any, right: Any) -> Any:
    """Return a readable specificity comparison between two specificity tuples."""
    if left > right:
        return "More precise"
    if left < right:
        return "Less precise"
    return "Same precision"


def build_rule_assessment(
    total_matches: Any,
    total_wins: Any,
    total_losses: Any,
    shadowed: Any,
    stale: Any,
    overlaps: Any,
) -> Any:
    """Return summary paragraphs and a recommended action for a rule detail page."""
    category_conflicts = [
        overlap for overlap in overlaps if overlap.severity in {OVERLAP_CATEGORY_CONFLICT, OVERLAP_CRITICAL_CONFLICT}
    ]
    tag_differences = [overlap for overlap in overlaps if overlap.severity == OVERLAP_TAG_DIFFERENCE]
    if category_conflicts:
        return {
            "badge_label": "Category conflict",
            "badge_class": "text-bg-warning",
            "paragraphs": [
                "Category conflict. This rule overlaps with another rule that assigns a different category.",
                "FineScope currently applies the highest-scoring rule.",
            ],
            "recommended_action_label": "Review category conflict",
            "recommended_action_detail": "Inspect the shared transactions and consider narrowing one rule.",
        }
    if tag_differences:
        return {
            "badge_label": "Tag difference",
            "badge_class": "text-bg-info",
            "paragraphs": [
                "Tag difference only. Both rules assign the same category.",
                "The applied rule is the only rule that assigns tags.",
            ],
            "recommended_action_label": "Inspect tag difference",
            "recommended_action_detail": "Check whether the extra tag is intended or mark the overlap as harmless.",
        }
    if shadowed:
        return {
            "badge_label": "Skipped by priority",
            "badge_class": "text-bg-warning",
            "paragraphs": [
                gettext(
                    "This rule matched {matches} historical transactions and was applied to {wins}.",
                    matches=total_matches,
                    wins=total_wins,
                ),
                "It is skipped because another matching rule is applied under the current scoring model.",
            ],
            "recommended_action_label": suggested_action_label(shadowed.suggested_action),
            "recommended_action_detail": "Inspect the overlap, then remove or narrow the rule if it is redundant.",
        }
    if stale:
        return {
            "badge_label": STALE_LABELS.get(stale.status, "Stale"),
            "badge_class": STALE_BADGE_CLASSES.get(stale.status, "text-bg-secondary"),
            "paragraphs": [
                (
                    "This rule has no historical matches."
                    if stale.status == STALE_UNUSED
                    else "This rule has not matched recently."
                ),
            ],
            "recommended_action_label": suggested_action_label(stale.suggested_action),
            "recommended_action_detail": "Review whether this rule is still needed. If not, preview removal impact.",
        }
    if total_losses:
        return {
            "badge_label": "Review",
            "badge_class": "text-bg-secondary",
            "paragraphs": [
                gettext(
                    "This rule matched {matches} historical transactions and was not applied {losses} times.",
                    matches=total_matches,
                    losses=total_losses,
                ),
            ],
            "recommended_action_label": "Inspect overlapping rules",
            "recommended_action_detail": "Review overlaps before changing this rule.",
        }
    return {
        "badge_label": "No specific findings",
        "badge_class": "text-bg-success",
        "paragraphs": [
            "No specific audit findings were detected for this rule.",
        ],
        "recommended_action_label": "",
        "recommended_action_detail": "",
    }


def present_rule(rule: Any) -> Any:
    """Return a display mapping for a category rule."""
    specificity = compute_rule_specificity_score(rule)
    scope_label = rule_scope_label(rule)
    scope_value = rule_scope_value(rule)
    return {
        "id": rule.get("id"),
        "label": rule_label(rule),
        "keyword": rule.get("keyword") or "",
        "merchant_id": rule.get("merchant_id"),
        "account_id": rule.get("account_id"),
        "category": rule.get("category") or "",
        "tags": list(rule.get("tags") or []),
        "tag_label": ", ".join(rule.get("tags") or []) or "-",
        "source": rule.get("source") or "",
        "merchant_name": rule.get("merchant_name") or "",
        "amount_min": rule.get("amount_min"),
        "amount_max": rule.get("amount_max"),
        "amount_label": amount_constraint_label(rule),
        "direction": rule.get("direction") or "any",
        "direction_label": rule_direction_label(rule.get("direction")),
        "scope_label": scope_label,
        "scope_value": scope_value,
        "source_label": rule_source_label(rule.get("source")),
        "source_badge_class": rule_source_badge_class(rule.get("source")),
        "approval_label": rule_approval_label(rule),
        "approval_badge_class": rule_approval_badge_class(rule),
        "status_label": rule_status_label(rule),
        "status_badge_class": rule_status_badge_class(rule),
        "specificity": specificity,
        "specificity_label": specificity_label(specificity),
        "specificity_factors": specificity_factors(specificity),
    }


def attach_rule_action_flags(presented_rules: Any, transaction_reference_counts: Any) -> Any:
    """Attach direct-action safety metadata to presented rule mappings."""
    for rule in presented_rules:
        reference_count = transaction_reference_counts.get(rule.get("id"), 0)
        rule["transaction_reference_count"] = reference_count
        rule["can_delete_without_preview"] = reference_count == 0
    return presented_rules


def import_mode_label(mode: Any) -> Any:
    """Return the display label for a rule import mode."""
    return {
        RULE_IMPORT_MODE_ADD: "Add new rules only",
        RULE_IMPORT_MODE_OVERRIDE: "Override all rules",
    }.get(mode, "Import rules")


def rule_label(rule: Any) -> Any:
    """Return the primary display label for a rule."""
    merchant_name = rule.get("merchant_name")
    if merchant_name:
        return merchant_name
    return rule.get("keyword") or f"Rule {rule.get('id')}"


def rule_scope_label(rule: Any) -> Any:
    """Return the primary match scope label for a rule."""
    if rule.get("merchant_id"):
        return "Merchant"
    return "Keyword"


def rule_scope_value(rule: Any) -> Any:
    """Return the display value that explains the rule's primary match scope."""
    if rule.get("merchant_id"):
        return rule.get("merchant_name") or rule_label(rule)
    return rule.get("keyword") or rule_label(rule)


def specificity_label(specificity: Any) -> Any:
    """Return a compact display label for a matcher specificity tuple."""
    return " / ".join(str(part) for part in specificity)


def specificity_factors(specificity: Any) -> Any:
    """Return readable rule-level specificity factors for display."""
    return [
        {"label": "Merchant bound", "value": "Yes" if specificity[0] else "No"},
        {"label": "Account bound", "value": "Yes" if specificity[1] else "No"},
        {"label": "Direction bound", "value": "Yes" if specificity[2] else "No"},
        {"label": "Amount bound", "value": "Yes" if specificity[3] else "No"},
        {"label": "Keyword length", "value": specificity[4]},
    ]


def win_interactions_for_rule(rule_id: Any, wins: Any) -> Any:
    """Return rules that lost on transactions won by the selected rule."""
    interactions: dict[Any, dict[str, int]] = {}
    for audit in wins:
        winning_match = audit.winning_match
        for losing_match in audit.losing_matches:
            losing_rule_id = rule_id_from_match(losing_match)
            if losing_rule_id == rule_id:
                continue
            interaction = interactions.setdefault(
                losing_rule_id,
                {"shared_count": 0, "conflicting_count": 0},
            )
            interaction["shared_count"] += 1
            if winning_match is not None and losing_match.category != winning_match.category:
                interaction["conflicting_count"] += 1
    return interactions


def loss_interactions_for_rule(rule_id: Any, losses: Any) -> Any:
    """Return rules that beat the selected rule on shared transactions."""
    interactions: dict[Any, dict[str, int]] = {}
    for audit in losses:
        winning_rule_id = rule_id_from_match(audit.winning_match)
        if winning_rule_id is None or winning_rule_id == rule_id:
            continue
        selected_match = next(
            (match for match in audit.matches if rule_id_from_match(match) == rule_id),
            None,
        )
        interaction = interactions.setdefault(
            winning_rule_id,
            {"shared_count": 0, "conflicting_count": 0},
        )
        interaction["shared_count"] += 1
        if (
            selected_match is not None
            and audit.winning_match is not None
            and selected_match.category != audit.winning_match.category
        ):
            interaction["conflicting_count"] += 1
    return interactions


def win_rate_label(wins: Any, matches: Any) -> Any:
    """Return a percentage label for rule win rate."""
    if not matches:
        return "-"
    return f"{(wins / matches) * 100:.0f}%"


def amount_constraint_label(rule: Any) -> Any:
    """Return a human-friendly amount constraint label for a rule."""
    amount_min = rule.get("amount_min")
    amount_max = rule.get("amount_max")
    if amount_min is None and amount_max is None:
        return gettext("Any amount")
    if amount_min is not None and amount_max is not None and amount_min == amount_max:
        return gettext("Exact amount: {amount}", amount=f"{amount_min:.2f}")
    if amount_min is None:
        return gettext("Up to {amount}", amount=f"{amount_max:.2f}")
    if amount_max is None:
        return gettext("From {amount}", amount=f"{amount_min:.2f}")
    return gettext(
        "From {minimum} to {maximum}",
        minimum=f"{amount_min:.2f}",
        maximum=f"{amount_max:.2f}",
    )


def rule_direction_label(direction: Any) -> Any:
    """Return the display label for a rule direction constraint."""
    return CATEGORY_RULE_DIRECTION_LABELS.get(
        direction or CATEGORY_RULE_DIRECTION_ANY,
        CATEGORY_RULE_DIRECTION_LABELS[CATEGORY_RULE_DIRECTION_ANY],
    )


def rule_source_label(source: Any) -> Any:
    """Return the display label for a rule source."""
    return {
        CATEGORY_RULE_SOURCE_MANUAL: "Manual",
        CATEGORY_RULE_SOURCE_AUTOMATIC: "Automatic",
    }.get(source, str(source or "").strip() or "Unknown")


def rule_source_badge_class(source: Any) -> Any:
    """Return the Bootstrap badge class for a rule source."""
    return {
        CATEGORY_RULE_SOURCE_MANUAL: "text-bg-primary",
        CATEGORY_RULE_SOURCE_AUTOMATIC: "text-bg-info",
    }.get(source, "text-bg-secondary")


def rule_status_label(rule: Any) -> Any:
    """Return the user-facing audit status for a rule."""
    if rule.get("source") != CATEGORY_RULE_SOURCE_AUTOMATIC:
        return "Manual"
    return "Approved" if rule.get("ai_approved") else "Suggested"


def rule_status_badge_class(rule: Any) -> Any:
    """Return the Bootstrap badge class for a rule audit status."""
    if rule.get("source") != CATEGORY_RULE_SOURCE_AUTOMATIC:
        return "text-bg-primary"
    return "text-bg-success" if rule.get("ai_approved") else "text-bg-warning"


def rule_approval_label(rule: Any) -> Any:
    """Return the approval label for a rule when approval applies."""
    if rule.get("source") != CATEGORY_RULE_SOURCE_AUTOMATIC:
        return "-"
    return "Approved" if rule.get("ai_approved") else "Suggested"


def rule_approval_badge_class(rule: Any) -> Any:
    """Return the Bootstrap badge class for an approval label."""
    if rule.get("source") != CATEGORY_RULE_SOURCE_AUTOMATIC:
        return "text-bg-secondary"
    return "text-bg-success" if rule.get("ai_approved") else "text-bg-warning"


def joined_rule_count_labels(rule_counts: Any, rule_by_id: Any) -> Any:
    """Return comma-separated rule labels with transaction counts."""
    labels = []
    for rule_id, count in sorted(
        rule_counts.items(),
        key=lambda item: (-item[1], rule_label(rule_by_id.get(item[0], {}))),
    ):
        rule = rule_by_id.get(rule_id)
        if rule:
            labels.append(f"{rule_label(rule)} ({count})")
    return ", ".join(labels) or "-"


def overlap_winning_side_label(rule_counts: Any, rule_a_id: Any, rule_b_id: Any) -> Any:
    """Return whether Rule A, Rule B, or a mixed set wins an overlap."""
    winner_ids = {rule_id for rule_id, count in rule_counts.items() if count > 0}
    if winner_ids == {rule_a_id}:
        return "Rule A"
    if winner_ids == {rule_b_id}:
        return "Rule B"
    return "Mixed"


def overlap_display_rule_ids(overlap: Any, rule_a_id: Any, rule_b_id: Any) -> Any:
    """Return rule IDs ordered as dominant winner then losing rule for detail pages."""
    winner_ids = [
        rule_id
        for rule_id, count in sorted(
            overlap.winning_rule_counts.items(),
            key=lambda item: (-item[1], item[0]),
        )
        if rule_id in {rule_a_id, rule_b_id} and count > 0
    ]
    winning_rule_id = winner_ids[0] if winner_ids else rule_a_id
    losing_rule_id = rule_b_id if winning_rule_id == rule_a_id else rule_a_id
    return winning_rule_id, losing_rule_id


def joined_rule_labels(rule_ids: Any, rule_by_id: Any) -> Any:
    """Return comma-separated rule labels for rule IDs."""
    labels = [rule_label(rule_by_id[rule_id]) for rule_id in rule_ids if rule_id in rule_by_id]
    return ", ".join(labels)
