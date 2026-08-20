"""Shared types and action constants for rule-change previews."""

from dataclasses import dataclass

from finance_app.modules.categories.rules_matching import ScoredRuleMatch
from finance_app.modules.rules.audit import RuleRow, TransactionRow

PREVIEW_REMOVE_RULE = "remove_rule"
PREVIEW_CREATE_RULE = "create_rule"
PREVIEW_DELETE_RULE = "delete_rule"
PREVIEW_EDIT_RULE = "edit_rule"
PREVIEW_APPROVE_RULE = "approve_rule"
PREVIEW_APPLY_WHERE_WINS = "apply_where_wins"
PREVIEW_FORCE_APPLY_RULE = "force_apply_rule"
PREVIEW_APPLY_ALL_RULES = "apply_all_rules"
PREVIEW_CREATED_RULE_ID = -1


@dataclass(frozen=True)
class RuleChangeImpact:
    """Represent one transaction-level result in a read-only rule preview."""

    transaction: TransactionRow
    current_winning_match: ScoredRuleMatch | None
    proposed_winning_match: ScoredRuleMatch | None
    current_rule_id: int | None
    proposed_rule_id: int | None
    current_category: str
    proposed_category: str
    current_tags: tuple[str, ...]
    proposed_tags: tuple[str, ...]
    impact_group: str


@dataclass(frozen=True)
class RuleChangePreview:
    """Represent aggregate and transaction-level impact for a proposed rule change."""

    action: str
    rule: RuleRow
    proposed_rule: RuleRow | None
    summary: dict[str, int]
    impacts: tuple[RuleChangeImpact, ...]
    grouped_impacts: dict[str, tuple[RuleChangeImpact, ...]]
    limited: bool = False
