"""Robust anomaly insight candidates for period comparisons."""

from typing import Any

from finance_app.core.i18n import gettext
from finance_app.core.money import money_to_float
from finance_app.modules.comparison.insight_cards import (
    build_insight_card,
    comparison_bar_width,
    format_money_text,
    format_signed_money_text,
)
from finance_app.modules.comparison.insight_scoring import (
    capped_ratio,
    money_selection_metrics,
)
from finance_app.modules.comparison.statistics import robust_anomaly_score

MIN_ROBUST_ANOMALY_HISTORY_PERIODS = 5
DEFAULT_ROBUST_ANOMALY_MIN_ABSOLUTE_DIFFERENCE = 25.0


def robust_anomaly_insight_candidates(
    category_rows: Any,
    merchant_rows: Any,
    category_history: Any,
    merchant_history: Any,
    *,
    min_history_periods: Any = MIN_ROBUST_ANOMALY_HISTORY_PERIODS,
    min_absolute_difference: Any = DEFAULT_ROBUST_ANOMALY_MIN_ABSOLUTE_DIFFERENCE,
) -> Any:
    """Build robust anomaly insight candidates for ranked insight selection."""
    insights = []
    for row in category_rows:
        candidate = robust_anomaly_insight_candidate(
            row,
            "category",
            category_history.get(row["category"], []),
            min_history_periods=min_history_periods,
            min_absolute_difference=min_absolute_difference,
        )
        if candidate:
            insights.append(candidate)

    for row in merchant_rows:
        candidate = robust_anomaly_insight_candidate(
            row,
            "merchant",
            merchant_history.get(row["merchant"], []),
            min_history_periods=min_history_periods,
            min_absolute_difference=min_absolute_difference,
        )
        if candidate:
            insights.append(candidate)

    return insights


def robust_anomaly_insight_candidate(
    row: Any,
    label_key: Any,
    history: Any,
    *,
    min_history_periods: Any,
    min_absolute_difference: Any,
) -> Any:
    """Build one robust anomaly candidate when current spending is unusual."""
    result = robust_anomaly_score(row["current"], history)
    if not robust_anomaly_is_candidate(
        result,
        min_history_periods=min_history_periods,
        min_absolute_difference=min_absolute_difference,
    ):
        return None

    direction = result["direction"]
    high = direction == "high"
    name = row[label_key]
    label = robust_anomaly_label(label_key, direction)
    value = gettext(
        "{name}: higher than usual" if high else "{name}: lower than usual",
        name=name,
    )
    median = result["median"]
    current = result["current"]
    difference = result["difference"]
    return build_insight_card(
        label=label,
        value=value,
        detail=gettext(
            "{name} is {current} this period; typical recent spending is {typical}.",
            name=name,
            current=format_money_text(current),
            typical=format_money_text(median),
        ),
        visual="comparison",
        group="categories" if label_key == "category" else "merchants",
        tone="danger" if high else "success",
        icon="bi-graph-up-arrow" if high else "bi-graph-down-arrow",
        title=value,
        summary=format_signed_money_text(difference),
        badge=gettext("Higher than usual" if high else "Lower than usual"),
        insight_type=f"{label_key}_spending_{direction}_anomaly",
        score=robust_anomaly_candidate_score(result),
        rank_reason=robust_anomaly_rank_reason(result),
        robust_anomaly=result,
        selection_metrics=money_selection_metrics(
            abs(money_to_float(difference)),
            max(abs(money_to_float(current)), abs(money_to_float(median))),
            direction,
            entity_key=name,
        ),
        previous_label=format_money_text(median),
        current_label=format_money_text(current),
        previous_width=comparison_bar_width(median, current),
        current_width=comparison_bar_width(current, median),
    )


def robust_anomaly_is_candidate(result: Any, *, min_history_periods: Any, min_absolute_difference: Any) -> Any:
    """Return whether robust anomaly metadata is strong enough for a card."""
    difference = result.get("difference")
    return (
        result.get("is_anomaly") is True
        and result.get("history_count", 0) >= min_history_periods
        and result.get("direction") in ("high", "low")
        and abs(money_to_float(difference)) >= min_absolute_difference
    )


def robust_anomaly_label(label_key: Any, direction: Any) -> Any:
    """Return the static label id for a robust anomaly card."""
    labels = {
        ("category", "high"): "Unusually high category spending",
        ("category", "low"): "Unusually low category spending",
        ("merchant", "high"): "Unusually high merchant spending",
        ("merchant", "low"): "Unusually low merchant spending",
    }
    return labels[(label_key, direction)]


def robust_anomaly_candidate_score(result: Any) -> Any:
    """Return a deterministic ranking score for a robust anomaly candidate."""
    threshold = result["threshold"] or 1
    robust_component = capped_ratio(result["score"], threshold * 3)
    difference = abs(money_to_float(result["difference"]))
    baseline = max(abs(money_to_float(result["current"])), abs(money_to_float(result["median"])), 1.0)
    absolute_component = capped_ratio(difference, baseline)
    return round(((robust_component * 0.7) + (absolute_component * 0.3)) * 100, 2)


def robust_anomaly_rank_reason(result: Any) -> Any:
    """Return internal ranking metadata for robust anomaly candidates."""
    difference = abs(money_to_float(result["difference"]))
    return (
        "robust anomaly; "
        f"score={result['score']:.2f}; "
        f"abs={difference:.2f}; "
        f"history={result['history_count']}"
    )
