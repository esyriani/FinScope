"""Read-only rule audit analysis helpers.

Builds diagnostic rule-match sets on top of the shared category rule matcher.
The helpers query transactions and rules through SQLAlchemy Core and do not
mutate application state.
"""

from collections import Counter, defaultdict
from dataclasses import dataclass
from itertools import combinations

from sqlalchemy import and_, select

from finance_app.core.constants import (
    CATEGORY_RULE_SOURCE_MANUAL,
    CATEGORY_SOURCE_MANUAL,
    UNKNOWN_CATEGORY,
)
from finance_app.core.money import money_to_float
from finance_app.database.tables import (
    accounts as accounts_table,
    merchants as merchants_table,
    transactions as transactions_table,
)
from finance_app.modules.categories.service import get_category_rules
from finance_app.modules.categories.taxonomy import get_transaction_tags_by_id
from finance_app.modules.categories.rules_matching import (
    ScoredRuleMatch,
    rule_match_precedence_key,
    rule_specificity,
    score_category_rule_matches,
    select_winning_rule_match,
)
from finance_app.modules.merchants.normalization import normalize_merchant
from finance_app.modules.settings.runtime import get_unknown_category


OVERLAP_HARMLESS = "harmless"
OVERLAP_TAG_DIFFERENCE = "tag_difference"
OVERLAP_CATEGORY_CONFLICT = "category_conflict"
OVERLAP_CRITICAL_CONFLICT = "critical_conflict"
STALE_UNUSED = "unused"
STALE_STALE = "stale"
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
class TransactionRuleAudit:
    """Represent all rule matches for one transaction.

    Attributes:
        transaction: Transaction mapping decorated with normalized merchant
            fields and current tag names.
        matches: All rules matching the transaction under the selected audit
            semantics.
        winning_match: The match selected by the production precedence tuple,
            or None when no rule matches.
        losing_matches: Matching rules that did not win.
    """

    transaction: dict
    matches: tuple[ScoredRuleMatch, ...]
    winning_match: ScoredRuleMatch | None
    losing_matches: tuple[ScoredRuleMatch, ...]


@dataclass(frozen=True)
class RuleAuditData:
    """Represent the reusable match matrix for one rule audit request."""

    rules: tuple[dict, ...]
    transactions: tuple[dict, ...]
    transaction_audits: tuple[TransactionRuleAudit, ...]
    rule_by_id: dict[int, dict]
    matches_by_rule_id: dict[int, tuple[TransactionRuleAudit, ...]]
    wins_by_rule_id: dict[int, tuple[TransactionRuleAudit, ...]]
    losses_by_rule_id: dict[int, tuple[TransactionRuleAudit, ...]]
    stored_applied_by_rule_id: dict[int, tuple[dict, ...]]
    limited: bool = False


@dataclass(frozen=True)
class RuleOverlap:
    """Represent two rules that match at least one same transaction."""

    rule_a: dict
    rule_b: dict
    shared_transaction_audits: tuple[TransactionRuleAudit, ...]
    severity: str
    winning_rule_counts: dict[int, int]
    rule_a_applied_count: int
    rule_b_applied_count: int
    suggested_action: str

    @property
    def shared_count(self):
        """Return the number of shared matching transactions."""
        return len(self.shared_transaction_audits)


@dataclass(frozen=True)
class ShadowedRule:
    """Represent a rule that matches transactions but loses to other rules."""

    rule: dict
    total_matches: int
    total_wins: int
    total_losses: int
    shadowing_rule_counts: dict[int, int]
    most_common_shadowing_rule_id: int | None
    conflicting_loss_count: int
    suggested_action: str


@dataclass(frozen=True)
class StaleRule:
    """Represent an unused or stale rule finding."""

    rule: dict
    status: str
    total_matches: int
    total_wins: int
    stored_applied_count: int
    last_matched_date: object | None
    recent_matches: int | None
    suggested_action: str


@dataclass(frozen=True)
class SpecificityWarning:
    """Represent a broad winning rule beating a more specific rule."""

    broad_rule: dict
    specific_rule: dict
    shared_transaction_audits: tuple[TransactionRuleAudit, ...]
    reason: str
    conflicting_count: int
    suggested_action: str

    @property
    def shared_count(self):
        """Return the number of transactions behind the warning."""
        return len(self.shared_transaction_audits)


@dataclass(frozen=True)
class RuleChangeImpact:
    """Represent one transaction-level result in a read-only rule preview."""

    transaction: dict
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
    rule: dict
    proposed_rule: dict | None
    summary: dict
    impacts: tuple[RuleChangeImpact, ...]
    grouped_impacts: dict[str, tuple[RuleChangeImpact, ...]]
    limited: bool = False


def compute_rule_match_sets(
    conn,
    transaction_limit=None,
    include_unknown=False,
    include_fuzzy=False,
):
    """Compute the read-only rule match matrix for historical transactions.

    Args:
        conn: Open SQLAlchemy Core connection.
        transaction_limit: Optional maximum number of newest transactions to
            analyze. When omitted, all matching historical rows are used.
        include_unknown: Whether UNKNOWN or uncategorized transactions should
            be included. The default excludes them to avoid false conflicts.
        include_fuzzy: Whether to include medium-confidence fuzzy keyword
            matches. The default matches the current apply-all behavior.

    Returns:
        A RuleAuditData instance containing reusable match indexes.
    """
    unknown_category = get_unknown_category(conn) or UNKNOWN_CATEGORY
    rules = tuple(get_category_rules(conn))
    transactions, limited = audit_transaction_rows(
        conn,
        unknown_category,
        transaction_limit=transaction_limit,
        include_unknown=include_unknown,
    )
    transaction_audits = tuple(
        get_all_rule_matches_for_transaction(
            transaction,
            rules,
            conn=conn,
            include_fuzzy=include_fuzzy,
        )
        for transaction in transactions
    )

    return build_rule_audit_data(rules, transactions, transaction_audits, limited=limited)


def audit_transaction_rows(conn, unknown_category, transaction_limit=None, include_unknown=False):
    """Return active historical transaction rows eligible for rule auditing."""
    conditions = [transactions_table.c.ignored == 0]
    if not include_unknown:
        conditions.append(
            and_(
                transactions_table.c.category.is_not(None),
                transactions_table.c.category != unknown_category,
                transactions_table.c.category != UNKNOWN_CATEGORY,
            )
        )

    query = (
        select(
            transactions_table.c.id,
            transactions_table.c.account_id,
            accounts_table.c.name.label("account_name"),
            transactions_table.c.merchant_id,
            merchants_table.c.merchant_key.label("merchant_name"),
            transactions_table.c.tx_date,
            transactions_table.c.description,
            transactions_table.c.amount,
            transactions_table.c.category,
            transactions_table.c.category_source,
            transactions_table.c.category_confidence,
            transactions_table.c.category_rule_id,
            transactions_table.c.reviewed_at,
            transactions_table.c.needs_review,
            transactions_table.c.transaction_kind,
        )
        .select_from(
            transactions_table.outerjoin(
                accounts_table,
                accounts_table.c.id == transactions_table.c.account_id,
            ).outerjoin(
                merchants_table,
                merchants_table.c.id == transactions_table.c.merchant_id,
            )
        )
        .where(*conditions)
        .order_by(transactions_table.c.tx_date.desc(), transactions_table.c.id.desc())
    )
    if transaction_limit is not None:
        query = query.limit(int(transaction_limit) + 1)

    rows = [dict(row) for row in conn.execute(query).mappings().fetchall()]
    limited = transaction_limit is not None and len(rows) > int(transaction_limit)
    if limited:
        rows = rows[: int(transaction_limit)]

    tags_by_transaction_id = get_transaction_tags_by_id(conn, [row["id"] for row in rows])
    for row in rows:
        amount = money_to_float(row["amount"])
        normalized = normalize_merchant(row["description"], conn=conn)
        row["amount"] = amount
        row["tags"] = tags_by_transaction_id.get(row["id"], [])
        row["merchant_key"] = normalized.merchant_key
        row["merchant"] = normalized.merchant_key
        row["normalized_description"] = normalized.merchant_key

    return tuple(rows), limited


def get_all_rule_matches_for_transaction(transaction, rules, conn=None, include_fuzzy=False):
    """Return all matching rules and the winning rule for one transaction."""
    merchant_key = transaction.get("merchant_key")
    if not merchant_key:
        normalized = normalize_merchant(transaction.get("description", ""), conn=conn)
        merchant_key = normalized.merchant_key

    matches = tuple(
        score_category_rule_matches(
            merchant_key,
            transaction.get("amount"),
            rules,
            merchant_candidate=merchant_key,
            raw_description=transaction.get("description"),
            merchant_id=transaction.get("merchant_id"),
            account_id=transaction.get("account_id"),
            transaction_kind=transaction.get("transaction_kind"),
            include_fuzzy=include_fuzzy,
        )
    )
    winning_match = select_winning_rule_match(matches)
    losing_matches = tuple(match for match in matches if match is not winning_match)
    return TransactionRuleAudit(
        transaction=dict(transaction),
        matches=matches,
        winning_match=winning_match,
        losing_matches=losing_matches,
    )


def build_rule_audit_data(rules, transactions, transaction_audits, limited=False):
    """Build indexed audit data from rule and transaction match rows."""
    rule_by_id = {rule["id"]: rule for rule in rules}
    matches_by_rule_id = defaultdict(list)
    wins_by_rule_id = defaultdict(list)
    losses_by_rule_id = defaultdict(list)
    stored_applied_by_rule_id = defaultdict(list)

    for transaction in transactions:
        rule_id = transaction.get("category_rule_id")
        if rule_id is not None:
            stored_applied_by_rule_id[int(rule_id)].append(transaction)

    for audit in transaction_audits:
        winning_rule_id = rule_id_from_match(audit.winning_match)
        for match in audit.matches:
            rule_id = rule_id_from_match(match)
            matches_by_rule_id[rule_id].append(audit)
            if rule_id == winning_rule_id:
                wins_by_rule_id[rule_id].append(audit)
            else:
                losses_by_rule_id[rule_id].append(audit)

    return RuleAuditData(
        rules=tuple(rules),
        transactions=tuple(transactions),
        transaction_audits=tuple(transaction_audits),
        rule_by_id=rule_by_id,
        matches_by_rule_id=freeze_index(matches_by_rule_id),
        wins_by_rule_id=freeze_index(wins_by_rule_id),
        losses_by_rule_id=freeze_index(losses_by_rule_id),
        stored_applied_by_rule_id=freeze_index(stored_applied_by_rule_id),
        limited=limited,
    )


def freeze_index(index):
    """Return an ordinary dict whose values are immutable tuples."""
    return {key: tuple(value) for key, value in index.items()}


def analyze_rule_overlaps(audit_data):
    """Return all rule pairs that share at least one matching transaction."""
    overlaps = []
    for rule_a, rule_b in combinations(audit_data.rules, 2):
        rule_a_id = rule_a["id"]
        rule_b_id = rule_b["id"]
        shared_audits = shared_matching_transaction_audits(
            audit_data,
            rule_a_id,
            rule_b_id,
        )
        if not shared_audits:
            continue

        severity = classify_rule_overlap(rule_a, rule_b, shared_audits)
        winning_rule_counts = Counter(
            rule_id_from_match(audit.winning_match)
            for audit in shared_audits
            if audit.winning_match is not None
        )
        overlaps.append(
            RuleOverlap(
                rule_a=rule_a,
                rule_b=rule_b,
                shared_transaction_audits=shared_audits,
                severity=severity,
                winning_rule_counts=dict(winning_rule_counts),
                rule_a_applied_count=count_stored_applied_in_audits(rule_a_id, shared_audits),
                rule_b_applied_count=count_stored_applied_in_audits(rule_b_id, shared_audits),
                suggested_action=suggest_overlap_action(severity),
            )
        )

    return sorted(
        overlaps,
        key=lambda overlap: (
            overlap_severity_rank(overlap.severity),
            -overlap.shared_count,
            overlap.rule_a["keyword"],
            overlap.rule_b["keyword"],
        ),
    )


def shared_matching_transaction_audits(audit_data, rule_a_id, rule_b_id):
    """Return transaction audits where both rule IDs matched."""
    rule_a_transaction_ids = {
        audit.transaction["id"]
        for audit in audit_data.matches_by_rule_id.get(rule_a_id, ())
    }
    shared = []
    for audit in audit_data.matches_by_rule_id.get(rule_b_id, ()):
        if audit.transaction["id"] in rule_a_transaction_ids:
            shared.append(audit)
    return tuple(shared)


def classify_rule_overlap(rule_a, rule_b, shared_transaction_audits):
    """Classify the severity of an overlapping rule pair."""
    if rule_a.get("category") != rule_b.get("category"):
        if len(shared_transaction_audits) > 1 or any(
            transaction_was_manually_reviewed(audit.transaction)
            for audit in shared_transaction_audits
        ):
            return OVERLAP_CRITICAL_CONFLICT
        return OVERLAP_CATEGORY_CONFLICT

    if normalized_tag_set(rule_a) != normalized_tag_set(rule_b):
        return OVERLAP_TAG_DIFFERENCE

    return OVERLAP_HARMLESS


def analyze_shadowed_rules(audit_data):
    """Return rules that match transactions but lose to other matching rules."""
    findings = []
    for rule in audit_data.rules:
        rule_id = rule["id"]
        matches = audit_data.matches_by_rule_id.get(rule_id, ())
        losses = audit_data.losses_by_rule_id.get(rule_id, ())
        if not matches or not losses:
            continue

        shadowing_rule_counts = Counter(
            rule_id_from_match(audit.winning_match)
            for audit in losses
            if audit.winning_match is not None
        )
        findings.append(
            ShadowedRule(
                rule=rule,
                total_matches=len(matches),
                total_wins=len(audit_data.wins_by_rule_id.get(rule_id, ())),
                total_losses=len(losses),
                shadowing_rule_counts=dict(shadowing_rule_counts),
                most_common_shadowing_rule_id=most_common_counter_key(shadowing_rule_counts),
                conflicting_loss_count=sum(
                    1
                    for audit in losses
                    if audit.winning_match is not None
                    and audit.winning_match.category != rule.get("category")
                ),
                suggested_action=suggest_shadowed_action(
                    rule,
                    matches,
                    audit_data.wins_by_rule_id.get(rule_id, ()),
                    losses,
                ),
            )
        )

    return sorted(
        findings,
        key=lambda finding: (
            finding.total_wins > 0,
            -finding.total_losses,
            finding.rule["keyword"],
        ),
    )


def analyze_stale_rules(audit_data, recent_since=None):
    """Return unused and optionally stale rules from audit match data."""
    findings = []
    for rule in audit_data.rules:
        rule_id = rule["id"]
        matches = audit_data.matches_by_rule_id.get(rule_id, ())
        wins = audit_data.wins_by_rule_id.get(rule_id, ())
        stored_applied = audit_data.stored_applied_by_rule_id.get(rule_id, ())
        if not matches:
            findings.append(
                StaleRule(
                    rule=rule,
                    status=STALE_UNUSED,
                    total_matches=0,
                    total_wins=0,
                    stored_applied_count=len(stored_applied),
                    last_matched_date=None,
                    recent_matches=0 if recent_since is not None else None,
                    suggested_action="review or remove",
                )
            )
            continue

        recent_matches = None
        if recent_since is not None:
            recent_matches = sum(
                1
                for audit in matches
                if audit.transaction.get("tx_date") >= recent_since
            )
            if recent_matches == 0:
                findings.append(
                    StaleRule(
                        rule=rule,
                        status=STALE_STALE,
                        total_matches=len(matches),
                        total_wins=len(wins),
                        stored_applied_count=len(stored_applied),
                        last_matched_date=last_matched_date(matches),
                        recent_matches=recent_matches,
                        suggested_action="review",
                    )
                )

    return sorted(
        findings,
        key=lambda finding: (
            finding.status != STALE_UNUSED,
            finding.rule["keyword"],
        ),
    )


def analyze_specificity_warnings(audit_data):
    """Return warnings where a less specific rule wins over a more specific match."""
    grouped_audits = defaultdict(list)
    grouped_reasons = defaultdict(Counter)
    for audit in audit_data.transaction_audits:
        winning_match = audit.winning_match
        if winning_match is None:
            continue

        winning_rule_id = rule_id_from_match(winning_match)
        for losing_match in audit.losing_matches:
            losing_rule_id = rule_id_from_match(losing_match)
            if losing_match.specificity <= winning_match.specificity:
                continue
            grouped_audits[(winning_rule_id, losing_rule_id)].append(audit)
            grouped_reasons[(winning_rule_id, losing_rule_id)][
                precedence_win_reason(winning_match, losing_match)
            ] += 1

    warnings = []
    for (winning_rule_id, losing_rule_id), audits in grouped_audits.items():
        broad_rule = audit_data.rule_by_id.get(winning_rule_id)
        specific_rule = audit_data.rule_by_id.get(losing_rule_id)
        if broad_rule is None or specific_rule is None:
            continue

        conflicting_count = sum(
            1
            for audit in audits
            if matched_rule_category(audit, losing_rule_id) != audit.winning_match.category
        )
        warnings.append(
            SpecificityWarning(
                broad_rule=broad_rule,
                specific_rule=specific_rule,
                shared_transaction_audits=tuple(audits),
                reason=most_common_counter_key(grouped_reasons[(winning_rule_id, losing_rule_id)]),
                conflicting_count=conflicting_count,
                suggested_action="edit or narrow" if conflicting_count else "inspect overlaps",
            )
        )

    return sorted(
        warnings,
        key=lambda warning: (
            -warning.conflicting_count,
            -warning.shared_count,
            warning.broad_rule["keyword"],
            warning.specific_rule["keyword"],
        ),
    )


def preview_rule_change(conn, proposed_change, transaction_limit=None):
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
    proposed_rule = None
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


def preview_rule_set_change(conn, action, proposed_rules, transaction_limit=None):
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
        if (impact := preview_rule_set_change_impact(
                audit,
                proposed_rules,
                unknown_category,
            )) is not None
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


def normalize_preview_action(action):
    """Return the canonical preview action identifier."""
    action = str(action or "").strip()
    if action == PREVIEW_REMOVE_RULE:
        return PREVIEW_DELETE_RULE
    return action


def preview_rule_id(proposed_change):
    """Return an optional target rule ID from a proposed change mapping."""
    value = proposed_change.get("rule_id")
    if value in (None, ""):
        return None
    return int(value)


def preview_create_rule(proposed_rule):
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


def preview_edit_rule(current_rule, proposed_rule):
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


def preview_rule_change_impacts(audit_data, action, rule_id, unknown_category, proposed_rule=None):
    """Return transaction-level impacts for a supported preview action."""
    if action == PREVIEW_CREATE_RULE:
        return tuple(
            impact
            for audit in audit_data.transaction_audits
            if (impact := preview_create_rule_impact(
                    audit,
                    proposed_rule,
                    unknown_category,
                )) is not None
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
            if (impact := preview_edit_rule_impact(
                    audit,
                    rule_id,
                    proposed_rule,
                    unknown_category,
                )) is not None
        )
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
    if action == PREVIEW_APPLY_ALL_RULES:
        return tuple(
            impact
            for audit in audit_data.transaction_audits
            if (impact := preview_apply_all_rules_impact(audit, unknown_category)) is not None
        )
    return ()


def preview_create_rule_impact(audit, proposed_rule, unknown_category):
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


def preview_rule_set_change_impact(audit, proposed_rules, unknown_category):
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


def preview_delete_rule_impact(audit, rule_id, unknown_category):
    """Return one transaction impact for a delete-rule preview, if relevant."""
    current_rule_ids = {rule_id_from_match(match) for match in audit.matches}
    if rule_id not in current_rule_ids and audit.transaction.get("category_rule_id") != rule_id:
        return None

    proposed_matches = tuple(
        match
        for match in audit.matches
        if rule_id_from_match(match) != rule_id
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


def preview_edit_rule_impact(audit, rule_id, proposed_rule, unknown_category):
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

    proposed_matches = tuple(
        match
        for match in audit.matches
        if rule_id_from_match(match) != rule_id
    ) + proposed_rule_matches
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


def preview_apply_rule_impact(audit, rule_id, unknown_category):
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


def preview_apply_all_rules_impact(audit, unknown_category):
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


def assignment_from_match(match, unknown_category):
    """Return the category and tags assigned by a scored match."""
    if match is None:
        return unknown_category, ()
    return match.category, tuple(match.tags)


def stored_assignment_from_transaction(transaction, unknown_category):
    """Return the persisted category and tags for a transaction audit row."""
    return transaction.get("category") or unknown_category, tuple(transaction.get("tags") or ())


def match_for_rule_id(audit, rule_id):
    """Return one scored match for a rule ID inside an audited transaction."""
    return next(
        (match for match in audit.matches if rule_id_from_match(match) == rule_id),
        None,
    )


def impact_group(current_rule_id, proposed_rule_id, current_category, proposed_category, current_tags, proposed_tags):
    """Return the mutually exclusive transaction-level preview group."""
    if current_category != proposed_category:
        return "category_change"
    if current_tags != proposed_tags:
        return "tags_change"
    if current_rule_id != proposed_rule_id:
        return "winning_rule_change"
    return "no_material_change"


def group_rule_change_impacts(impacts):
    """Return transaction preview impacts grouped by display category."""
    grouped = defaultdict(list)
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


def rule_change_preview_summary(impacts, unknown_category):
    """Return aggregate counts for a rule change preview."""
    return {
        "total_affected_transactions": len(impacts),
        "winning_rule_changes": sum(
            1
            for impact in impacts
            if impact.current_rule_id != impact.proposed_rule_id
        ),
        "category_changes": sum(
            1
            for impact in impacts
            if impact.current_category != impact.proposed_category
        ),
        "tag_changes": sum(
            1
            for impact in impacts
            if impact.current_tags != impact.proposed_tags
        ),
        "would_become_unknown": sum(
            1
            for impact in impacts
            if impact.proposed_category == unknown_category
        ),
        "newly_require_review": sum(
            1
            for impact in impacts
            if impact.proposed_category == unknown_category
        ),
    }


def get_rule_audit_summary(audit_data):
    """Return aggregate diagnostics derived from the audit match matrix."""
    overlaps = analyze_rule_overlaps(audit_data)
    shadowed = analyze_shadowed_rules(audit_data)
    stale = analyze_stale_rules(audit_data)
    specificity_warnings = analyze_specificity_warnings(audit_data)
    rules_with_matches = {
        rule_id
        for rule_id, matches in audit_data.matches_by_rule_id.items()
        if matches
    }
    rules_with_stored_application = {
        rule_id
        for rule_id, transactions in audit_data.stored_applied_by_rule_id.items()
        if transactions
    }
    return {
        "total_active_rules": len(audit_data.rules),
        "rules_with_zero_historical_matches": len(audit_data.rules) - len(rules_with_matches),
        "rules_with_historical_matches_but_zero_applied": len(
            rules_with_matches - rules_with_stored_application
        ),
        "overlapping_rule_pairs": len(overlaps),
        "harmless_overlaps": count_overlaps_by_severity(overlaps, OVERLAP_HARMLESS),
        "category_conflict_overlaps": count_overlaps_by_severity(
            overlaps,
            OVERLAP_CATEGORY_CONFLICT,
        )
        + count_overlaps_by_severity(overlaps, OVERLAP_CRITICAL_CONFLICT),
        "tag_difference_overlaps": count_overlaps_by_severity(
            overlaps,
            OVERLAP_TAG_DIFFERENCE,
        ),
        "shadowed_rules": len(shadowed),
        "stale_rules": len(stale),
        "specificity_warnings": len(specificity_warnings),
        "limited": audit_data.limited,
    }


def count_overlaps_by_severity(overlaps, severity):
    """Return how many overlaps have a given severity."""
    return sum(1 for overlap in overlaps if overlap.severity == severity)


def rule_id_from_match(match):
    """Return the category rule ID for a scored match."""
    if match is None:
        return None
    return match.rule["id"] if "id" in match.rule.keys() else match.rule.get("id")


def normalized_tag_set(rule):
    """Return rule tags as a normalized set for severity comparison."""
    return frozenset(str(tag) for tag in (rule.get("tags") or ()))


def transaction_was_manually_reviewed(transaction):
    """Return whether a transaction has evidence of manual review."""
    return bool(
        transaction.get("reviewed_at")
        or transaction.get("category_source") == CATEGORY_SOURCE_MANUAL
    )


def count_stored_applied_in_audits(rule_id, transaction_audits):
    """Return how many shared transactions currently reference a rule ID."""
    return sum(
        1
        for audit in transaction_audits
        if audit.transaction.get("category_rule_id") == rule_id
    )


def most_common_counter_key(counter):
    """Return the most common key from a Counter, or None."""
    if not counter:
        return None
    return counter.most_common(1)[0][0]


def matched_rule_category(audit, rule_id):
    """Return the category assigned by a rule match inside one transaction audit."""
    match = next(
        (match for match in audit.matches if rule_id_from_match(match) == rule_id),
        None,
    )
    return match.category if match is not None else None


def last_matched_date(matches):
    """Return the latest transaction date among rule matches."""
    dates = [audit.transaction.get("tx_date") for audit in matches if audit.transaction.get("tx_date")]
    return max(dates) if dates else None


def suggest_overlap_action(severity):
    """Return an advisory action label for an overlap severity."""
    return {
        OVERLAP_HARMLESS: "mark harmless or merge",
        OVERLAP_TAG_DIFFERENCE: "inspect tags",
        OVERLAP_CATEGORY_CONFLICT: "inspect manually",
        OVERLAP_CRITICAL_CONFLICT: "edit or narrow",
    }.get(severity, "inspect manually")


def suggest_shadowed_action(rule, matches, wins, losses):
    """Return an advisory action label for a shadowed rule."""
    del rule, matches
    if not wins:
        return "delete or narrow"
    if losses:
        return "inspect overlaps"
    return "review"


def overlap_severity_rank(severity):
    """Return a sort rank that places dangerous overlap findings first."""
    return {
        OVERLAP_CRITICAL_CONFLICT: 0,
        OVERLAP_CATEGORY_CONFLICT: 1,
        OVERLAP_TAG_DIFFERENCE: 2,
        OVERLAP_HARMLESS: 3,
    }.get(severity, 4)


def precedence_win_reason(winning_match, losing_match):
    """Return the first precedence component that made one match beat another."""
    if winning_match.confidence > losing_match.confidence:
        return "Higher confidence"
    if winning_match.match_score > losing_match.match_score:
        return "Higher match score"
    if winning_match.specificity > losing_match.specificity:
        return "Higher specificity"
    return "Stable precedence"


def compute_rule_specificity_score(rule):
    """Return the deterministic specificity score tuple for a rule."""
    return rule_specificity(rule)


def explain_rule_win(winning_match, losing_matches):
    """Return a structured explanation of why a rule won over alternatives."""
    if winning_match is None:
        return {}

    return {
        "winning_rule_id": rule_id_from_match(winning_match),
        "winning_precedence": rule_match_precedence_key(winning_match),
        "losing_rules": [
            {
                "rule_id": rule_id_from_match(match),
                "precedence": rule_match_precedence_key(match),
                "same_category": match.category == winning_match.category,
                "same_tags": match.tags == winning_match.tags,
            }
            for match in losing_matches
        ],
    }
