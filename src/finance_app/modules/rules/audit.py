"""Read-only rule audit analysis helpers.

Builds diagnostic rule-match sets on top of the shared category rule matcher.
The helpers query transactions and rules through SQLAlchemy Core and do not
mutate application state.
"""

from collections import Counter, defaultdict
from collections.abc import Hashable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from itertools import combinations
from typing import Any, TypeAlias, TypeVar

from sqlalchemy import select
from sqlalchemy.engine import Connection

from finance_app.core.category_sql import transaction_category_label_expression
from finance_app.core.constants import (
    CATEGORY_SOURCE_MANUAL,
    UNKNOWN_CATEGORY,
)
from finance_app.core.money import rounded_money_decimal
from finance_app.database.tables import (
    accounts as accounts_table,
)
from finance_app.database.tables import (
    merchants as merchants_table,
)
from finance_app.database.tables import (
    transactions as transactions_table,
)
from finance_app.modules.categories.rules_matching import (
    ScoredRuleMatch,
    rule_specificity,
    score_category_rule_matches,
    select_winning_rule_match,
)
from finance_app.modules.categories.service import get_category_rules
from finance_app.modules.categories.taxonomy import get_transaction_tags_by_id
from finance_app.modules.merchants.normalization import normalize_merchant
from finance_app.modules.settings.runtime import get_unknown_category

OVERLAP_HARMLESS = "harmless"
OVERLAP_TAG_DIFFERENCE = "tag_difference"
OVERLAP_CATEGORY_CONFLICT = "category_conflict"
OVERLAP_CRITICAL_CONFLICT = "critical_conflict"
STALE_UNUSED = "unused"
STALE_STALE = "stale"
RuleRow: TypeAlias = dict[str, Any]
TransactionRow: TypeAlias = dict[str, Any]
RuleAuditIndex: TypeAlias = dict[int, tuple["TransactionRuleAudit", ...]]

K = TypeVar("K", bound=Hashable)
T = TypeVar("T")


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

    transaction: TransactionRow
    matches: tuple[ScoredRuleMatch, ...]
    winning_match: ScoredRuleMatch | None
    losing_matches: tuple[ScoredRuleMatch, ...]


@dataclass(frozen=True)
class RuleAuditData:
    """Represent the reusable match matrix for one rule audit request."""

    rules: tuple[RuleRow, ...]
    transactions: tuple[TransactionRow, ...]
    transaction_audits: tuple[TransactionRuleAudit, ...]
    rule_by_id: dict[int, RuleRow]
    matches_by_rule_id: RuleAuditIndex
    wins_by_rule_id: RuleAuditIndex
    losses_by_rule_id: RuleAuditIndex
    stored_applied_by_rule_id: dict[int, tuple[TransactionRow, ...]]
    limited: bool = False


@dataclass(frozen=True)
class RuleOverlap:
    """Represent two rules that match at least one same transaction."""

    rule_a: RuleRow
    rule_b: RuleRow
    shared_transaction_audits: tuple[TransactionRuleAudit, ...]
    severity: str
    winning_rule_counts: dict[int, int]
    rule_a_applied_count: int
    rule_b_applied_count: int
    suggested_action: str

    @property
    def shared_count(self) -> int:
        """Return the number of shared matching transactions."""
        return len(self.shared_transaction_audits)


@dataclass(frozen=True)
class ShadowedRule:
    """Represent a rule that matches transactions but loses to other rules."""

    rule: RuleRow
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

    rule: RuleRow
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

    broad_rule: RuleRow
    specific_rule: RuleRow
    shared_transaction_audits: tuple[TransactionRuleAudit, ...]
    reason: str
    conflicting_count: int
    suggested_action: str

    @property
    def shared_count(self) -> int:
        """Return the number of transactions behind the warning."""
        return len(self.shared_transaction_audits)


def compute_rule_match_sets(
    conn: Connection,
    transaction_limit: int | None = None,
    include_unknown: bool = False,
    include_fuzzy: bool = False,
) -> RuleAuditData:
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


def audit_transaction_rows(
    conn: Connection,
    unknown_category: str,
    transaction_limit: int | None = None,
    include_unknown: bool = False,
) -> tuple[tuple[TransactionRow, ...], bool]:
    """Return active historical transaction rows eligible for rule auditing."""
    category_label = transaction_category_label_expression(unknown_category)
    conditions = [transactions_table.c.ignored == 0]
    if not include_unknown:
        conditions.append(category_label != unknown_category)
        conditions.append(category_label != UNKNOWN_CATEGORY)

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
            category_label.label("category"),
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

    rows: list[TransactionRow] = [dict(row) for row in conn.execute(query).mappings().fetchall()]
    limited = False
    if transaction_limit is not None:
        limited = len(rows) > transaction_limit
        if limited:
            rows = rows[:transaction_limit]

    tags_by_transaction_id = get_transaction_tags_by_id(conn, [row["id"] for row in rows])
    for row in rows:
        normalized = normalize_merchant(row["description"], conn=conn)
        row["amount"] = rounded_money_decimal(row["amount"])
        row["tags"] = tags_by_transaction_id.get(row["id"], [])
        row["merchant_key"] = normalized.merchant_key
        row["merchant"] = normalized.merchant_key
        row["normalized_description"] = normalized.merchant_key

    return tuple(rows), limited


def get_all_rule_matches_for_transaction(
    transaction: Mapping[str, Any],
    rules: Iterable[Mapping[str, Any]],
    conn: Connection | None = None,
    include_fuzzy: bool = False,
) -> TransactionRuleAudit:
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


def build_rule_audit_data(
    rules: Iterable[RuleRow],
    transactions: Iterable[TransactionRow],
    transaction_audits: Iterable[TransactionRuleAudit],
    limited: bool = False,
) -> RuleAuditData:
    """Build indexed audit data from rule and transaction match rows."""
    rules_tuple = tuple(rules)
    transactions_tuple = tuple(transactions)
    transaction_audits_tuple = tuple(transaction_audits)
    rule_by_id = {int(rule["id"]): rule for rule in rules_tuple}
    matches_by_rule_id: defaultdict[int, list[TransactionRuleAudit]] = defaultdict(list)
    wins_by_rule_id: defaultdict[int, list[TransactionRuleAudit]] = defaultdict(list)
    losses_by_rule_id: defaultdict[int, list[TransactionRuleAudit]] = defaultdict(list)
    stored_applied_by_rule_id: defaultdict[int, list[TransactionRow]] = defaultdict(list)

    for transaction in transactions_tuple:
        rule_id = transaction.get("category_rule_id")
        if rule_id is not None:
            stored_applied_by_rule_id[int(rule_id)].append(transaction)

    for audit in transaction_audits_tuple:
        winning_rule_id = rule_id_from_match(audit.winning_match)
        for match in audit.matches:
            rule_id = rule_id_from_match(match)
            if rule_id is None:
                continue
            matches_by_rule_id[rule_id].append(audit)
            if rule_id == winning_rule_id:
                wins_by_rule_id[rule_id].append(audit)
            else:
                losses_by_rule_id[rule_id].append(audit)

    return RuleAuditData(
        rules=rules_tuple,
        transactions=transactions_tuple,
        transaction_audits=transaction_audits_tuple,
        rule_by_id=rule_by_id,
        matches_by_rule_id=freeze_index(matches_by_rule_id),
        wins_by_rule_id=freeze_index(wins_by_rule_id),
        losses_by_rule_id=freeze_index(losses_by_rule_id),
        stored_applied_by_rule_id=freeze_index(stored_applied_by_rule_id),
        limited=limited,
    )


def freeze_index(index: Mapping[K, Iterable[T]]) -> dict[K, tuple[T, ...]]:
    """Return an ordinary dict whose values are immutable tuples."""
    return {key: tuple(value) for key, value in index.items()}


def analyze_rule_overlaps(audit_data: RuleAuditData) -> list[RuleOverlap]:
    """Return all rule pairs that share at least one matching transaction."""
    overlaps = []
    for (rule_a_id, rule_b_id), shared_audits in shared_rule_pair_audits(audit_data).items():
        rule_a = audit_data.rule_by_id.get(rule_a_id)
        rule_b = audit_data.rule_by_id.get(rule_b_id)
        if rule_a is None or rule_b is None:
            continue
        if not shared_audits:
            continue

        severity = classify_rule_overlap(rule_a, rule_b, shared_audits)
        winning_rule_counts: Counter[int] = Counter(
            rule_id
            for audit in shared_audits
            if audit.winning_match is not None and (rule_id := rule_id_from_match(audit.winning_match)) is not None
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


def shared_rule_pair_audits(audit_data: RuleAuditData) -> dict[tuple[int, int], tuple[TransactionRuleAudit, ...]]:
    """Return transaction audits keyed by rule pairs that actually co-match."""
    shared_pairs = defaultdict(list)
    for audit in audit_data.transaction_audits:
        matched_rule_ids = sorted(
            {rule_id for match in audit.matches if (rule_id := rule_id_from_match(match)) is not None}
        )
        for rule_a_id, rule_b_id in combinations(matched_rule_ids, 2):
            shared_pairs[(rule_a_id, rule_b_id)].append(audit)
    return freeze_index(shared_pairs)


def shared_matching_transaction_audits(
    audit_data: RuleAuditData,
    rule_a_id: int,
    rule_b_id: int,
) -> tuple[TransactionRuleAudit, ...]:
    """Return transaction audits where both rule IDs matched."""
    rule_a_transaction_ids = {audit.transaction["id"] for audit in audit_data.matches_by_rule_id.get(rule_a_id, ())}
    shared: list[TransactionRuleAudit] = []
    for audit in audit_data.matches_by_rule_id.get(rule_b_id, ()):
        if audit.transaction["id"] in rule_a_transaction_ids:
            shared.append(audit)
    return tuple(shared)


def classify_rule_overlap(
    rule_a: Mapping[str, Any],
    rule_b: Mapping[str, Any],
    shared_transaction_audits: Sequence[TransactionRuleAudit],
) -> str:
    """Classify the severity of an overlapping rule pair."""
    if rule_a.get("category") != rule_b.get("category"):
        if len(shared_transaction_audits) > 1 or any(
            transaction_was_manually_reviewed(audit.transaction) for audit in shared_transaction_audits
        ):
            return OVERLAP_CRITICAL_CONFLICT
        return OVERLAP_CATEGORY_CONFLICT

    if normalized_tag_set(rule_a) != normalized_tag_set(rule_b):
        return OVERLAP_TAG_DIFFERENCE

    return OVERLAP_HARMLESS


def analyze_shadowed_rules(audit_data: RuleAuditData) -> list[ShadowedRule]:
    """Return rules that match transactions but lose to other matching rules."""
    findings = []
    for rule in audit_data.rules:
        rule_id = rule["id"]
        matches = audit_data.matches_by_rule_id.get(rule_id, ())
        losses = audit_data.losses_by_rule_id.get(rule_id, ())
        if not matches or not losses:
            continue

        shadowing_rule_counts: Counter[int] = Counter(
            shadowing_rule_id
            for audit in losses
            if audit.winning_match is not None
            and (shadowing_rule_id := rule_id_from_match(audit.winning_match)) is not None
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
                    if audit.winning_match is not None and audit.winning_match.category != rule.get("category")
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


def analyze_stale_rules(audit_data: RuleAuditData, recent_since: Any | None = None) -> list[StaleRule]:
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
            recent_matches = sum(1 for audit in matches if audit.transaction.get("tx_date") >= recent_since)
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


def analyze_specificity_warnings(audit_data: RuleAuditData) -> list[SpecificityWarning]:
    """Return warnings where a less specific rule wins over a more specific match."""
    grouped_audits: defaultdict[tuple[int, int], list[TransactionRuleAudit]] = defaultdict(list)
    grouped_reasons: defaultdict[tuple[int, int], Counter[str]] = defaultdict(Counter)
    for audit in audit_data.transaction_audits:
        winning_match = audit.winning_match
        if winning_match is None:
            continue

        winning_rule_id = rule_id_from_match(winning_match)
        if winning_rule_id is None:
            continue
        for losing_match in audit.losing_matches:
            losing_rule_id = rule_id_from_match(losing_match)
            if losing_rule_id is None:
                continue
            if losing_match.specificity <= winning_match.specificity:
                continue
            grouped_audits[(winning_rule_id, losing_rule_id)].append(audit)
            grouped_reasons[(winning_rule_id, losing_rule_id)][precedence_win_reason(winning_match, losing_match)] += 1

    warnings = []
    for (winning_rule_id, losing_rule_id), audits in grouped_audits.items():
        broad_rule = audit_data.rule_by_id.get(winning_rule_id)
        specific_rule = audit_data.rule_by_id.get(losing_rule_id)
        if broad_rule is None or specific_rule is None:
            continue

        conflicting_count = sum(
            1
            for audit in audits
            if audit.winning_match is not None
            and matched_rule_category(audit, losing_rule_id) != audit.winning_match.category
        )
        warnings.append(
            SpecificityWarning(
                broad_rule=broad_rule,
                specific_rule=specific_rule,
                shared_transaction_audits=tuple(audits),
                reason=most_common_counter_key(grouped_reasons[(winning_rule_id, losing_rule_id)])
                or "Stable precedence",
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


def get_rule_audit_summary(audit_data: RuleAuditData) -> dict[str, int | bool]:
    """Return aggregate diagnostics derived from the audit match matrix."""
    overlaps = analyze_rule_overlaps(audit_data)
    shadowed = analyze_shadowed_rules(audit_data)
    stale = analyze_stale_rules(audit_data)
    specificity_warnings = analyze_specificity_warnings(audit_data)
    rules_with_matches = {rule_id for rule_id, matches in audit_data.matches_by_rule_id.items() if matches}
    rules_with_stored_application = {
        rule_id for rule_id, transactions in audit_data.stored_applied_by_rule_id.items() if transactions
    }
    return {
        "total_active_rules": len(audit_data.rules),
        "rules_with_zero_historical_matches": len(audit_data.rules) - len(rules_with_matches),
        "rules_with_historical_matches_but_zero_applied": len(rules_with_matches - rules_with_stored_application),
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


def count_overlaps_by_severity(overlaps: Iterable[RuleOverlap], severity: str) -> int:
    """Return how many overlaps have a given severity."""
    return sum(1 for overlap in overlaps if overlap.severity == severity)


def rule_id_from_match(match: ScoredRuleMatch | None) -> int | None:
    """Return the category rule ID for a scored match."""
    if match is None:
        return None
    value = match.rule["id"] if "id" in match.rule.keys() else match.rule.get("id")
    return int(value) if value is not None else None


def normalized_tag_set(rule: Mapping[str, Any]) -> frozenset[str]:
    """Return rule tags as a normalized set for severity comparison."""
    return frozenset(str(tag) for tag in (rule.get("tags") or ()))


def transaction_was_manually_reviewed(transaction: Mapping[str, Any]) -> bool:
    """Return whether a transaction has evidence of manual review."""
    return bool(transaction.get("reviewed_at") or transaction.get("category_source") == CATEGORY_SOURCE_MANUAL)


def count_stored_applied_in_audits(rule_id: int, transaction_audits: Iterable[TransactionRuleAudit]) -> int:
    """Return how many shared transactions currently reference a rule ID."""
    return sum(1 for audit in transaction_audits if audit.transaction.get("category_rule_id") == rule_id)


def most_common_counter_key(counter: Counter[K]) -> K | None:
    """Return the most common key from a Counter, or None."""
    if not counter:
        return None
    return counter.most_common(1)[0][0]


def matched_rule_category(audit: TransactionRuleAudit, rule_id: int | None) -> str | None:
    """Return the category assigned by a rule match inside one transaction audit."""
    match = next(
        (match for match in audit.matches if rule_id_from_match(match) == rule_id),
        None,
    )
    return match.category if match is not None else None


def last_matched_date(matches: Iterable[TransactionRuleAudit]) -> Any | None:
    """Return the latest transaction date among rule matches."""
    dates: list[Any] = [
        transaction_date for audit in matches if (transaction_date := audit.transaction.get("tx_date")) is not None
    ]
    return max(dates) if dates else None


def suggest_overlap_action(severity: str) -> str:
    """Return an advisory action label for an overlap severity."""
    return {
        OVERLAP_HARMLESS: "mark harmless or merge",
        OVERLAP_TAG_DIFFERENCE: "inspect tags",
        OVERLAP_CATEGORY_CONFLICT: "inspect manually",
        OVERLAP_CRITICAL_CONFLICT: "edit or narrow",
    }.get(severity, "inspect manually")


def suggest_shadowed_action(
    rule: Mapping[str, Any],
    matches: Sequence[TransactionRuleAudit],
    wins: Sequence[TransactionRuleAudit],
    losses: Sequence[TransactionRuleAudit],
) -> str:
    """Return an advisory action label for a shadowed rule."""
    del rule, matches
    if not wins:
        return "delete or narrow"
    if losses:
        return "inspect overlaps"
    return "review"


def overlap_severity_rank(severity: str) -> int:
    """Return a sort rank that places dangerous overlap findings first."""
    return {
        OVERLAP_CRITICAL_CONFLICT: 0,
        OVERLAP_CATEGORY_CONFLICT: 1,
        OVERLAP_TAG_DIFFERENCE: 2,
        OVERLAP_HARMLESS: 3,
    }.get(severity, 4)


def precedence_win_reason(winning_match: ScoredRuleMatch, losing_match: ScoredRuleMatch) -> str:
    """Return the first precedence component that made one match beat another."""
    if winning_match.confidence > losing_match.confidence:
        return "Higher confidence"
    if winning_match.match_score > losing_match.match_score:
        return "Higher match score"
    if winning_match.specificity > losing_match.specificity:
        return "Higher specificity"
    return "Stable precedence"


def compute_rule_specificity_score(rule: Mapping[str, Any]) -> tuple[int, int, int, int, int]:
    """Return the deterministic specificity score tuple for a rule."""
    return rule_specificity(rule)
