"""Ranking and scoring helpers for comparison insight candidates."""

from typing import Any

from finance_app.core.money import money_to_float
from finance_app.modules.comparison.change_metrics import change_state, percentage_change

DEFAULT_INSIGHT_CARD_LIMIT = 7
DEFAULT_RANKED_INSIGHT_MIN_SCORE = 10.0
DEFAULT_RANKED_INSIGHT_MIN_MONEY_CHANGE = 5.0


def select_ranked_insight_candidates(
    candidates: Any,
    *,
    max_count: Any = DEFAULT_INSIGHT_CARD_LIMIT,
    min_score: Any = DEFAULT_RANKED_INSIGHT_MIN_SCORE,
    min_money_change: Any = DEFAULT_RANKED_INSIGHT_MIN_MONEY_CHANGE,
    deduplicate: Any = True,
) -> Any:
    """Return ranked insight candidates after thresholding and deduplication."""
    ranked_candidates = [
        (index, candidate)
        for index, candidate in enumerate(candidates)
        if insight_candidate_passes_thresholds(
            candidate,
            min_score=min_score,
            min_money_change=min_money_change,
        )
    ]
    ranked_candidates.sort(key=lambda item: (-money_to_float(item[1].get("score")), item[0]))

    selected = []
    seen_keys = set()
    for _, candidate in ranked_candidates:
        if deduplicate:
            dedupe_key = insight_candidate_dedupe_key(candidate)
            if dedupe_key in seen_keys:
                continue
            seen_keys.add(dedupe_key)

        selected.append(candidate)
        if len(selected) >= max_count:
            break

    return selected


def insight_candidate_passes_thresholds(candidate: Any, *, min_score: Any, min_money_change: Any) -> Any:
    """Return whether a candidate has enough internal signal for ranked selection."""
    if money_to_float(candidate.get("score")) < min_score:
        return False

    metrics = candidate.get("selection_metrics") or {}
    if metrics.get("metric") == "money" and abs(money_to_float(metrics.get("absolute_change"))) < min_money_change:
        return False

    return True


def insight_candidate_dedupe_key(candidate: Any) -> Any:
    """Return a stable key for similar insight cards."""
    metrics = candidate.get("selection_metrics") or {}
    direction = normalized_insight_direction(
        metrics.get("direction") or insight_direction_family(candidate.get("insight_type"))
    )
    title = str(
        metrics.get("entity_key") or candidate.get("title") or candidate.get("value") or candidate.get("label") or ""
    )
    return (direction, title.casefold())


def normalized_insight_direction(direction: Any) -> Any:
    """Return a broad direction bucket for deduplicating similar cards."""
    direction = str(direction or "")
    if direction in ("up", "high", "increase", "new", "resurrected", "rank_increase"):
        return "increase"
    if direction in ("down", "low", "decrease", "dropped"):
        return "decrease"
    return direction


def insight_direction_family(insight_type: Any) -> Any:
    """Return the broad movement family for an insight type."""
    insight_type = str(insight_type or "")
    if insight_type.endswith("_increase"):
        return "increase"
    if insight_type.endswith("_decrease"):
        return "decrease"
    return insight_type


def build_insight_scoring_context(
    current_summary: Any,
    previous_summary: Any,
    current_amount: Any = None,
    previous_amount: Any = None,
) -> Any:
    """Return period-level values used to score insight candidates."""
    if current_amount is None:
        current_amount = current_summary["spending"]
    if previous_amount is None:
        previous_amount = previous_summary["spending"]
    current_spending = abs(money_to_float(current_amount))
    previous_spending = abs(money_to_float(previous_amount))
    current_count = int(current_summary["transaction_count"] or 0)
    previous_count = int(previous_summary["transaction_count"] or 0)
    return {
        "current_spending": current_spending,
        "previous_spending": previous_spending,
        "spending_baseline": max(current_spending, previous_spending, 1.0),
        "current_transaction_count": current_count,
        "previous_transaction_count": previous_count,
        "activity_confidence": activity_confidence(current_count, previous_count),
    }


def empty_insight_scoring_context() -> Any:
    """Return a neutral scoring context for direct helper calls."""
    return {
        "current_spending": 0.0,
        "previous_spending": 0.0,
        "spending_baseline": 1.0,
        "current_transaction_count": 0,
        "previous_transaction_count": 0,
        "activity_confidence": 0.0,
    }


def score_period_change_insight(row: Any, scoring_context: Any, rank_basis: Any) -> Any:
    """Score a category or merchant period-change insight candidate."""
    score = insight_candidate_score(
        absolute_change=row["abs_change"],
        absolute_baseline=scoring_context["spending_baseline"],
        percent=row["percent"],
        state=row["state"],
        importance_amount=max(abs(row["current"]), abs(row["previous"])),
        importance_baseline=scoring_context["spending_baseline"],
        confidence=scoring_context["activity_confidence"],
    )
    return score["score"], score_rank_reason(rank_basis, score)


def score_aggregate_spending_insight(amount: Any, scoring_context: Any, rank_basis: Any, state: Any) -> Any:
    """Score an aggregate new or dropped spending insight candidate."""
    score = insight_candidate_score(
        absolute_change=abs(amount),
        absolute_baseline=scoring_context["spending_baseline"],
        percent=None,
        state=state,
        importance_amount=abs(amount),
        importance_baseline=scoring_context["spending_baseline"],
        confidence=scoring_context["activity_confidence"],
    )
    return score["score"], score_rank_reason(rank_basis, score)


def score_transaction_activity_insight(
    current_count: Any,
    previous_count: Any,
    current_spending: Any,
    previous_spending: Any,
    scoring_context: Any,
) -> Any:
    """Score the transaction activity insight candidate."""
    score = insight_candidate_score(
        absolute_change=abs(current_count - previous_count),
        absolute_baseline=max(current_count, previous_count, 1),
        percent=percentage_change(current_count, previous_count),
        state=change_state(current_count, previous_count),
        importance_amount=max(
            abs(money_to_float(current_spending)),
            abs(money_to_float(previous_spending)),
        ),
        importance_baseline=scoring_context["spending_baseline"],
        confidence=scoring_context["activity_confidence"],
    )
    return score["score"], score_rank_reason("transaction count change", score)


def period_change_selection_metrics(row: Any) -> Any:
    """Return non-rendered selection metadata for period change cards."""
    return money_selection_metrics(
        row["abs_change"],
        max(abs(row["current"]), abs(row["previous"])),
        row["direction"],
        percent=row["percent"],
    )


def money_selection_metrics(
    absolute_change: Any, importance_amount: Any, direction: Any, percent: Any = None, entity_key: Any = None
) -> Any:
    """Return non-rendered selection metadata for money movement cards."""
    return {
        "metric": "money",
        "absolute_change": money_to_float(abs(absolute_change)),
        "percent_change": abs(money_to_float(percent)) if percent is not None else None,
        "importance_amount": money_to_float(abs(importance_amount)),
        "direction": direction,
        "entity_key": entity_key,
    }


def activity_selection_metrics(
    current_count: Any, previous_count: Any, current_spending: Any, previous_spending: Any
) -> Any:
    """Return non-rendered selection metadata for transaction activity cards."""
    return {
        "metric": "activity",
        "absolute_change": abs(int(current_count or 0) - int(previous_count or 0)),
        "percent_change": percentage_change(current_count, previous_count),
        "importance_amount": max(
            abs(money_to_float(current_spending)),
            abs(money_to_float(previous_spending)),
        ),
        "direction": "up" if current_count > previous_count else "down" if current_count < previous_count else "flat",
    }


def insight_candidate_score(
    *,
    absolute_change: Any,
    absolute_baseline: Any,
    percent: Any,
    state: Any,
    importance_amount: Any,
    importance_baseline: Any,
    confidence: Any,
) -> Any:
    """Return deterministic score components for an insight candidate."""
    absolute_component = capped_ratio(abs(absolute_change), absolute_baseline)
    percent_component = percentage_score_component(percent, state)
    importance_component = capped_ratio(abs(importance_amount), importance_baseline)
    confidence_component = capped_ratio(confidence, 1.0)
    change_component = (absolute_component * 0.6) + (percent_component * 0.4)
    importance_multiplier = 0.75 + (importance_component * 0.25)
    confidence_multiplier = 0.5 + (confidence_component * 0.5)
    return {
        "score": round(change_component * importance_multiplier * confidence_multiplier * 100, 2),
        "absolute_component": absolute_component,
        "percent_component": percent_component,
        "importance_component": importance_component,
        "confidence_component": confidence_component,
    }


def score_rank_reason(rank_basis: Any, score: Any) -> Any:
    """Return internal score metadata for future candidate ranking."""
    return (
        f"{rank_basis}; "
        f"abs={score['absolute_component']:.1%}; "
        f"percent={score['percent_component']:.1%}; "
        f"importance={score['importance_component']:.1%}; "
        f"confidence={score['confidence_component']:.1%}"
    )


def percentage_score_component(percent: Any, state: Any) -> Any:
    """Return a normalized percentage-change scoring component."""
    if percent is None:
        return 1.0 if state in ("new", "dropped") else 0.0
    return capped_ratio(abs(percent), 100.0)


def activity_confidence(current_count: Any, previous_count: Any) -> Any:
    """Return confidence based on available transaction activity."""
    current_count = int(current_count or 0)
    previous_count = int(previous_count or 0)
    total_count = current_count + previous_count
    if total_count <= 0:
        return 0.0

    sample_component = capped_ratio(total_count, 10)
    largest_count = max(current_count, previous_count)
    balance_component = min(current_count, previous_count) / largest_count if largest_count else 0.0
    return round((sample_component * 0.7) + (balance_component * 0.3), 4)


def capped_ratio(value: Any, baseline: Any) -> Any:
    """Return a 0..1 ratio, guarding missing and zero baselines."""
    value = abs(float(value or 0))
    baseline = abs(float(baseline or 0))
    if baseline == 0:
        return 0.0
    return min(value / baseline, 1.0)
