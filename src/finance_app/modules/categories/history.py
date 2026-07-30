"""Historical transaction categorization evidence.

Retrieves bounded SQL candidate pools from persisted transactions and scores
them in Python using merchant normalization, amount direction, account, review
status, and recency. The categorization workflow uses this module before
falling back to LLM classification.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any

from sqlalchemy import and_, case, or_, select

from finance_app.core.category_sql import transaction_category_label_expression
from finance_app.core.constants import (
    CATEGORY_SOURCE_AI,
    CATEGORY_SOURCE_MANUAL,
    CATEGORY_SOURCE_RULE,
)
from finance_app.core.money import MoneyValue, optional_money_to_float
from finance_app.database.dates import coerce_date
from finance_app.database.tables import transactions as transactions_table
from finance_app.modules.categories.decision import (
    HIGH_CONFIDENCE_THRESHOLD,
    MEDIUM_CONFIDENCE_THRESHOLD,
)
from finance_app.modules.categories.taxonomy import get_transaction_tags_by_id
from finance_app.modules.merchants.normalization import normalize_merchant
from finance_app.modules.merchants.sql_filters import (
    description_matches_any_candidate,
    merchant_description_candidates,
)

CANDIDATE_POOL_LIMIT = 120
EVIDENCE_LIMIT = 8
MIN_CANDIDATE_SCORE = 0.55
STRONG_CANDIDATE_SCORE = 0.78


@dataclass(frozen=True)
class HistoricalCandidate:
    """Represent one previously categorized transaction used as evidence.

    Attributes:
        transaction_id: Persisted transaction identifier.
        tx_date: Persisted transaction date.
        description: Persisted transaction description.
        amount: Persisted signed amount as a float.
        transaction_kind: Persisted transaction kind.
        source: Persisted category source.
        category: Persisted category label.
        tags: Persisted tag labels.
        score: Similarity score between 0 and 1.
        authority: Reliability multiplier based on manual/reviewed/source data.
    """

    transaction_id: int
    tx_date: str | None
    description: str
    amount: float | None
    transaction_kind: str | None
    source: str
    category: str
    tags: tuple[str, ...]
    score: float
    authority: float


@dataclass(frozen=True)
class HistoricalDecision:
    """Represent a retrieval-based categorization decision.

    Attributes:
        category: Winning category label, if historical evidence is sufficient
            to make a proposal.
        tags: Supported tag labels for the winning category.
        confidence: Retrieval confidence between 0 and 1.
        evidence_ids: Transaction IDs that contributed to the winning category.
        candidates: Scored top candidates considered by the decision policy.
    """

    category: str | None
    tags: tuple[str, ...]
    confidence: float
    evidence_ids: tuple[int, ...]
    candidates: tuple[HistoricalCandidate, ...]

    @property
    def is_high_confidence(self) -> bool:
        """Return whether the decision can be applied without review."""
        return self.confidence >= HIGH_CONFIDENCE_THRESHOLD

    @property
    def is_medium_confidence(self) -> bool:
        """Return whether the decision is usable but should be reviewed."""
        return self.confidence >= MEDIUM_CONFIDENCE_THRESHOLD


def retrieve_historical_decision(
    conn: Any, transaction: Mapping[str, Any], unknown_category: str
) -> HistoricalDecision:
    """Return historical categorization evidence for one transaction.

    The query deliberately retrieves only a bounded candidate pool. Full
    similarity scoring stays in Python so the database work remains portable
    across SQLAlchemy-supported engines.
    """
    rows = historical_candidate_rows(conn, transaction, unknown_category)
    if not rows:
        return HistoricalDecision(None, (), 0.0, (), ())

    tag_map = get_transaction_tags_by_id(conn, [row["id"] for row in rows])
    candidates = sorted(
        (
            candidate
            for candidate in (
                score_historical_candidate(conn, transaction, row, tag_map.get(row["id"], ())) for row in rows
            )
            if candidate.score >= MIN_CANDIDATE_SCORE
        ),
        key=lambda candidate: (candidate.score, candidate.authority, candidate.transaction_id),
        reverse=True,
    )[:EVIDENCE_LIMIT]
    return historical_decision_from_candidates(candidates)


def historical_candidate_rows(conn: Any, transaction: Mapping[str, Any], unknown_category: str) -> list[Any]:
    """Return a bounded SQL candidate pool for historical similarity scoring."""
    category_label = transaction_category_label_expression(unknown_category)
    conditions = [
        transactions_table.c.ignored == 0,
        category_label != unknown_category,
    ]
    transaction_id = transaction.get("id")
    if transaction_id is not None:
        conditions.append(transactions_table.c.id != int(transaction_id))

    candidate_filter = candidate_pool_filter(conn, transaction)
    if candidate_filter is None:
        return []

    return (
        conn.execute(
            select(
                transactions_table.c.id,
                transactions_table.c.account_id,
                transactions_table.c.merchant_id,
                transactions_table.c.tx_date,
                transactions_table.c.description,
                transactions_table.c.amount,
                category_label.label("category"),
                transactions_table.c.category_source,
                transactions_table.c.category_confidence,
                transactions_table.c.reviewed_at,
                transactions_table.c.transaction_kind,
            )
            .where(*conditions, candidate_filter)
            .order_by(
                case((transactions_table.c.reviewed_at.is_not(None), 0), else_=1),
                transactions_table.c.tx_date.desc(),
                transactions_table.c.id.desc(),
            )
            .limit(CANDIDATE_POOL_LIMIT)
        )
        .mappings()
        .fetchall()
    )


def candidate_pool_filter(conn: Any, transaction: Mapping[str, Any]) -> Any:
    """Return coarse SQL filters that keep historical retrieval CPU-friendly."""
    filters: list[Any] = []
    merchant_id = transaction.get("merchant_id")
    if merchant_id is not None:
        filters.append(transactions_table.c.merchant_id == int(merchant_id))

    merchant_key = transaction.get("merchant_key") or transaction.get("description")
    description_candidates = merchant_description_candidates(conn, merchant_key)
    if description_candidates:
        filters.append(description_matches_any_candidate(transactions_table.c.description, description_candidates))

    account_amount_filter = same_account_amount_window_filter(transaction)
    if account_amount_filter is not None:
        filters.append(account_amount_filter)

    return or_(*filters) if filters else None


def same_account_amount_window_filter(transaction: Mapping[str, Any]) -> Any:
    """Return a coarse same-account amount window filter when available."""
    account_id = transaction.get("account_id")
    amount = optional_money_to_float(transaction.get("amount"))
    if account_id is None or amount is None:
        return None

    absolute_amount = abs(amount)
    lower = max(0.0, absolute_amount * 0.50)
    upper = absolute_amount * 1.50 + 0.01
    amount_conditions = (
        (
            transactions_table.c.amount >= lower,
            transactions_table.c.amount <= upper,
        )
        if amount >= 0
        else (
            transactions_table.c.amount <= -lower,
            transactions_table.c.amount >= -upper,
        )
    )
    return and_(transactions_table.c.account_id == int(account_id), *amount_conditions)


def score_historical_candidate(
    conn: Any,
    transaction: Mapping[str, Any],
    row: Mapping[str, Any],
    tags: Iterable[str],
) -> HistoricalCandidate:
    """Return a scored historical candidate for one database row."""
    normalized_current = current_merchant_identity(transaction)
    normalized_candidate = normalize_merchant(row["description"], conn=conn)
    merchant_score = merchant_similarity(transaction, row, normalized_current, normalized_candidate)
    description_score = text_similarity(
        normalized_current,
        normalized_candidate.cleaned_key or row["description"],
    )
    amount_score = amount_similarity(transaction.get("amount"), row["amount"])
    direction_score = 1.0 if same_direction(transaction.get("amount"), row["amount"]) else 0.0
    account_score = 1.0 if same_optional_value(transaction.get("account_id"), row["account_id"]) else 0.0
    recency_score = date_similarity(transaction.get("tx_date"), row["tx_date"])
    authority = candidate_authority(row)

    score = (
        merchant_score * 0.42
        + description_score * 0.20
        + amount_score * 0.13
        + direction_score * 0.08
        + account_score * 0.05
        + min(authority, 1.0) * 0.08
        + recency_score * 0.04
    )
    if merchant_score < 0.65 and description_score < 0.65:
        score *= 0.75

    return HistoricalCandidate(
        transaction_id=row["id"],
        tx_date=row["tx_date"],
        description=row["description"],
        amount=optional_money_to_float(row["amount"]),
        transaction_kind=row["transaction_kind"],
        source=row["category_source"],
        category=row["category"],
        tags=tuple(tags or ()),
        score=round(max(0.0, min(1.0, score)), 4),
        authority=authority,
    )


def current_merchant_identity(transaction: Mapping[str, Any]) -> str:
    """Return the best available normalized merchant text for a transaction."""
    return str(transaction.get("merchant_key") or transaction.get("description") or "").strip()


def merchant_similarity(
    transaction: Mapping[str, Any],
    row: Mapping[str, Any],
    current_merchant: str,
    candidate_merchant: Any,
) -> float:
    """Return similarity for merchant identity fields."""
    merchant_id = transaction.get("merchant_id")
    if merchant_id is not None and row["merchant_id"] is not None and int(merchant_id) == int(row["merchant_id"]):
        return 1.0

    candidate_names = [candidate_merchant.merchant_key, row["description"]]
    return max(text_similarity(current_merchant, candidate) for candidate in candidate_names)


def text_similarity(left: object, right: object) -> float:
    """Return normalized string similarity for short merchant descriptions."""
    left = str(left or "").strip().casefold()
    right = str(right or "").strip().casefold()
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    if left in right or right in left:
        return min(len(left), len(right)) / max(len(left), len(right))
    return SequenceMatcher(None, left, right).ratio()


def amount_similarity(left: MoneyValue | None, right: MoneyValue | None) -> float:
    """Return absolute amount similarity while respecting transaction direction."""
    left_amount = optional_money_to_float(left)
    right_amount = optional_money_to_float(right)
    if left_amount is None or right_amount is None or not same_direction(left_amount, right_amount):
        return 0.0

    left_abs = abs(left_amount)
    right_abs = abs(right_amount)
    denominator = max(left_abs, right_abs, 1.0)
    return max(0.0, 1.0 - min(abs(left_abs - right_abs) / denominator, 1.0))


def same_direction(left: MoneyValue | None, right: MoneyValue | None) -> bool:
    """Return whether two signed amounts have the same debit/credit direction."""
    left_amount = optional_money_to_float(left)
    right_amount = optional_money_to_float(right)
    if left_amount is None or right_amount is None:
        return False
    return (left_amount < 0) == (right_amount < 0)


def same_optional_value(left: object, right: object) -> bool:
    """Return whether two optional integer-like values are both present and equal."""
    if left is None or right is None:
        return False
    return int(str(left)) == int(str(right))


def date_similarity(left: object, right: object) -> float:
    """Return a small recency signal relative to the current transaction date."""
    try:
        left_date = coerce_date(left)
        right_date = coerce_date(right)
    except (TypeError, ValueError):
        return 0.0
    if left_date is None or right_date is None:
        return 0.0

    days = abs((left_date - right_date).days)
    return 1.0 / (1.0 + days / 365.0)


def candidate_authority(row: Mapping[str, Any]) -> float:
    """Return a reliability multiplier for a historical transaction row."""
    if row["category_source"] == CATEGORY_SOURCE_MANUAL:
        return 1.15
    if row["reviewed_at"]:
        return 1.05
    if row["category_source"] == CATEGORY_SOURCE_RULE:
        return 0.95
    if row["category_source"] == CATEGORY_SOURCE_AI:
        confidence = optional_money_to_float(row["category_confidence"])
        return max(0.65, min(0.90, confidence or 0.70))
    return 0.70


def historical_decision_from_candidates(candidates: list[HistoricalCandidate]) -> HistoricalDecision:
    """Return a category decision from scored historical candidates."""
    if not candidates:
        return HistoricalDecision(None, (), 0.0, (), ())

    vote_totals: dict[str, float] = {}
    for candidate in candidates:
        vote_totals[candidate.category] = (
            vote_totals.get(candidate.category, 0.0) + candidate.score * candidate.authority
        )

    category, winning_weight = max(vote_totals.items(), key=lambda item: item[1])
    total_weight = sum(vote_totals.values())
    winning_candidates = [candidate for candidate in candidates if candidate.category == category]
    strong_candidates = [candidate for candidate in winning_candidates if candidate.score >= STRONG_CANDIDATE_SCORE]
    winner_share = winning_weight / total_weight if total_weight else 0.0
    top_score = winning_candidates[0].score if winning_candidates else 0.0
    exceptional_single = (
        len(winning_candidates) == 1
        and winning_candidates[0].authority >= 1.10
        and top_score >= 0.95
        and winner_share >= 0.98
    )

    if winner_share >= 0.88 and (len(strong_candidates) >= 2 or exceptional_single):
        confidence = min(0.99, 0.95 + (winner_share - 0.88) * 0.15 + max(0.0, top_score - 0.88) * 0.10)
    elif winner_share >= 0.70 and strong_candidates:
        confidence = min(0.94, 0.85 + (winner_share - 0.70) * 0.20 + max(0.0, top_score - 0.78) * 0.10)
    else:
        confidence = min(0.84, 0.55 + winner_share * 0.20 + top_score * 0.10)

    return HistoricalDecision(
        category=category,
        tags=tuple(supported_tags(winning_candidates, winning_weight)),
        confidence=round(confidence, 4),
        evidence_ids=tuple(candidate.transaction_id for candidate in winning_candidates),
        candidates=tuple(candidates),
    )


def supported_tags(winning_candidates: Iterable[HistoricalCandidate], winning_weight: float) -> list[str]:
    """Return tags sufficiently supported by the winning historical category."""
    if not winning_candidates or winning_weight <= 0:
        return []

    tag_votes: dict[str, float] = {}
    tag_counts: dict[str, int] = {}
    for candidate in winning_candidates:
        weight = candidate.score * candidate.authority
        for tag in candidate.tags:
            tag_votes[tag] = tag_votes.get(tag, 0.0) + weight
            tag_counts[tag] = tag_counts.get(tag, 0) + 1

    supported = []
    for tag, weight in tag_votes.items():
        if weight / winning_weight >= 0.55 and (
            tag_counts[tag] >= 2
            or any(candidate.score >= 0.88 and tag in candidate.tags for candidate in winning_candidates)
        ):
            supported.append(tag)

    return sorted(supported, key=str.casefold)
