"""Merchant behavior insight candidates for period comparisons."""

from typing import Any

from finance_app.core.i18n import gettext
from finance_app.modules.comparison.insight_cards import (
    build_stat_insight_card,
    format_money_text,
    format_months_ago,
    format_rank,
)
from finance_app.modules.comparison.insight_scoring import (
    capped_ratio,
    money_selection_metrics,
)

DEFAULT_MERCHANT_BEHAVIOR_MIN_SPENDING = 25.0
MERCHANT_RESURRECTION_MIN_ABSENCE_MONTHS = 3
MERCHANT_RANK_INCREASE_MIN_PLACES = 3


def merchant_behavior_insight_candidates(
    merchant_rows: Any,
    merchant_activity_history: Any = None,
    *,
    min_spending: Any = DEFAULT_MERCHANT_BEHAVIOR_MIN_SPENDING,
    resurrection_min_absence_months: Any = MERCHANT_RESURRECTION_MIN_ABSENCE_MONTHS,
    rank_increase_min_places: Any = MERCHANT_RANK_INCREASE_MIN_PLACES,
) -> Any:
    """Build merchant behavior insight candidates from period rows and history."""
    merchant_activity_history = merchant_activity_history or {}
    insights = []
    resurrected = largest_resurrected_merchant(
        merchant_rows,
        merchant_activity_history,
        min_spending,
        resurrection_min_absence_months,
    )
    resurrected_merchant = resurrected["merchant"] if resurrected else None
    if resurrected:
        insights.append(
            build_resurrected_merchant_candidate(resurrected, merchant_activity_history[resurrected_merchant])
        )

    new_merchant = largest_new_merchant(
        merchant_rows,
        merchant_activity_history,
        min_spending,
        exclude_merchant=resurrected_merchant,
    )
    if new_merchant:
        insights.append(build_new_merchant_candidate(new_merchant))

    dropped_merchant = largest_dropped_merchant(merchant_rows, min_spending)
    if dropped_merchant:
        insights.append(build_dropped_merchant_candidate(dropped_merchant))

    rank_increase = largest_merchant_rank_increase(
        merchant_rows,
        min_spending,
        rank_increase_min_places,
    )
    if rank_increase:
        insights.append(build_merchant_rank_increase_candidate(rank_increase))

    return insights


def largest_new_merchant(
    merchant_rows: Any, merchant_activity_history: Any, min_spending: Any, *, exclude_merchant: Any = None
) -> Any:
    """Return the largest current-period merchant that is new to the available history."""
    candidates = [
        row
        for row in merchant_rows
        if row["state"] == "new"
        and row["current"] >= min_spending
        and row["merchant"] != exclude_merchant
        and not merchant_has_history(merchant_activity_history.get(row["merchant"]))
    ]
    return max(candidates, key=lambda row: (row["current"], row["merchant"]), default=None)


def largest_dropped_merchant(merchant_rows: Any, min_spending: Any) -> Any:
    """Return the largest prior-period merchant missing from the current period."""
    candidates = [row for row in merchant_rows if row["state"] == "dropped" and row["previous"] >= min_spending]
    return max(candidates, key=lambda row: (row["previous"], row["merchant"]), default=None)


def largest_resurrected_merchant(
    merchant_rows: Any,
    merchant_activity_history: Any,
    min_spending: Any,
    resurrection_min_absence_months: Any,
) -> Any:
    """Return the largest current merchant that returned after a long absence."""
    candidates = [
        row
        for row in merchant_rows
        if row["state"] == "new"
        and row["current"] >= min_spending
        and merchant_absence_months(merchant_activity_history.get(row["merchant"])) >= resurrection_min_absence_months
    ]
    return max(candidates, key=lambda row: (row["current"], row["merchant"]), default=None)


def largest_merchant_rank_increase(merchant_rows: Any, min_spending: Any, rank_increase_min_places: Any) -> Any:
    """Return the merchant with the largest meaningful rank increase."""
    current_ranks = merchant_spending_ranks(merchant_rows, "current")
    previous_ranks = merchant_spending_ranks(merchant_rows, "previous")
    candidates = []
    for row in merchant_rows:
        merchant = row["merchant"]
        if row["current"] < min_spending or row["previous"] <= 0:
            continue
        if merchant not in current_ranks or merchant not in previous_ranks:
            continue
        rank_change = previous_ranks[merchant] - current_ranks[merchant]
        if rank_change >= rank_increase_min_places:
            candidates.append(
                {
                    **row,
                    "current_rank": current_ranks[merchant],
                    "previous_rank": previous_ranks[merchant],
                    "rank_change": rank_change,
                }
            )
    return max(candidates, key=lambda row: (row["rank_change"], row["current"], row["merchant"]), default=None)


def merchant_spending_ranks(merchant_rows: Any, value_key: Any) -> Any:
    """Return 1-based merchant ranks by spending for the selected value key."""
    ranked = sorted(
        [row for row in merchant_rows if row[value_key] > 0],
        key=lambda row: (-row[value_key], row["merchant"].casefold()),
    )
    return {row["merchant"]: index for index, row in enumerate(ranked, start=1)}


def merchant_has_history(activity: Any) -> Any:
    """Return whether activity metadata contains prior merchant activity."""
    return bool(activity and activity.get("history_count", 0) > 0)


def merchant_absence_months(activity: Any) -> Any:
    """Return months since last merchant activity, or zero when unknown."""
    if not merchant_has_history(activity):
        return 0
    return int(activity.get("last_activity_months_ago") or 0)


def build_new_merchant_candidate(row: Any) -> Any:
    """Build a candidate for a merchant newly appearing in the current period."""
    merchant = row["merchant"]
    return build_merchant_behavior_card(
        row,
        label="New merchant activity",
        value=gettext("{merchant}: new this period", merchant=merchant),
        detail=gettext(
            "{merchant} has {amount} in current-period spending and did not appear in the prior period.",
            merchant=merchant,
            amount=format_money_text(row["current"]),
        ),
        tone="danger",
        icon="bi-plus-circle",
        title="New merchant",
        summary=format_money_text(row["current"]),
        badge=gettext("New"),
        insight_type="merchant_new",
        score=merchant_behavior_score(row["current"], 1.0),
        rank_reason=f"new merchant; current={row['current']:.2f}",
        metadata={"behavior": "new"},
        stat_items=[
            {"label": "Merchant", "value": merchant},
            {"label": "Current", "value": format_money_text(row["current"])},
            {"label": "Prior", "value": format_money_text(row["previous"])},
        ],
    )


def build_dropped_merchant_candidate(row: Any) -> Any:
    """Build a candidate for a merchant missing from the current period."""
    merchant = row["merchant"]
    return build_merchant_behavior_card(
        row,
        label="Missing merchant activity",
        value=gettext("{merchant}: missing this period", merchant=merchant),
        detail=gettext(
            "{merchant} had {amount} in prior-period spending and is missing from the current period.",
            merchant=merchant,
            amount=format_money_text(row["previous"]),
        ),
        tone="success",
        icon="bi-dash-circle",
        title="Missing merchant",
        summary=format_money_text(row["previous"]),
        badge=gettext("Missing"),
        insight_type="merchant_dropped",
        score=merchant_behavior_score(row["previous"], 1.0),
        rank_reason=f"missing merchant; previous={row['previous']:.2f}",
        metadata={"behavior": "dropped"},
        stat_items=[
            {"label": "Merchant", "value": merchant},
            {"label": "Current", "value": format_money_text(row["current"])},
            {"label": "Prior", "value": format_money_text(row["previous"])},
        ],
    )


def build_resurrected_merchant_candidate(row: Any, activity: Any) -> Any:
    """Build a candidate for a merchant returning after a long absence."""
    merchant = row["merchant"]
    months_ago = merchant_absence_months(activity)
    return build_merchant_behavior_card(
        row,
        label="Merchant returned",
        value=gettext("{merchant}: returned after a gap", merchant=merchant),
        detail=gettext(
            (
                "{merchant} returned with {amount} after {count} month without spending."
                if months_ago == 1
                else "{merchant} returned with {amount} after {count} months without spending."
            ),
            merchant=merchant,
            amount=format_money_text(row["current"]),
            count=months_ago,
        ),
        tone="accent",
        icon="bi-arrow-clockwise",
        title="Merchant returned",
        summary=format_money_text(row["current"]),
        badge=gettext("Returned"),
        insight_type="merchant_resurrected",
        score=merchant_behavior_score(row["current"], min(months_ago / MERCHANT_RESURRECTION_MIN_ABSENCE_MONTHS, 2.0)),
        rank_reason=f"merchant returned; current={row['current']:.2f}; months_absent={months_ago}",
        metadata={
            "behavior": "resurrected",
            "last_activity_months_ago": months_ago,
            "history_count": activity.get("history_count", 0),
            "last_activity_label": activity.get("last_activity_label"),
        },
        stat_items=[
            {"label": "Merchant", "value": merchant},
            {"label": "Current", "value": format_money_text(row["current"])},
            {"label": "Last seen", "value": format_months_ago(months_ago)},
        ],
    )


def build_merchant_rank_increase_candidate(row: Any) -> Any:
    """Build a candidate for a merchant moving up materially by spending rank."""
    merchant = row["merchant"]
    return build_merchant_behavior_card(
        row,
        label="Merchant moved up",
        value=gettext("{merchant}: rank increased", merchant=merchant),
        detail=gettext(
            "{merchant} moved from rank {previous_rank} to {current_rank} by spending.",
            merchant=merchant,
            previous_rank=format_rank(row["previous_rank"]),
            current_rank=format_rank(row["current_rank"]),
        ),
        tone="danger",
        icon="bi-arrow-up-right-circle",
        title="Merchant rank increased",
        summary=gettext(
            "{count} place" if row["rank_change"] == 1 else "{count} places",
            count=row["rank_change"],
        ),
        badge=gettext("Moved up"),
        insight_type="merchant_rank_increase",
        score=merchant_behavior_score(row["current"], row["rank_change"]),
        rank_reason=(
            "merchant rank increase; "
            f"current_rank={row['current_rank']}; "
            f"previous_rank={row['previous_rank']}; "
            f"rank_change={row['rank_change']}"
        ),
        metadata={
            "behavior": "rank_increase",
            "current_rank": row["current_rank"],
            "previous_rank": row["previous_rank"],
            "rank_change": row["rank_change"],
        },
        stat_items=[
            {"label": "Merchant", "value": merchant},
            {"label": "Current rank", "value": format_rank(row["current_rank"])},
            {"label": "Prior rank", "value": format_rank(row["previous_rank"])},
            {"label": "Current", "value": format_money_text(row["current"])},
        ],
    )


def build_merchant_behavior_card(
    row: Any,
    *,
    label: Any,
    value: Any,
    detail: Any,
    tone: Any,
    icon: Any,
    title: Any,
    summary: Any,
    badge: Any,
    insight_type: Any,
    score: Any,
    rank_reason: Any,
    metadata: Any,
    stat_items: Any,
) -> Any:
    """Build a merchant behavior insight card."""
    merchant = row["merchant"]
    return build_stat_insight_card(
        label=label,
        value=value,
        detail=detail,
        visual="aggregate",
        group="merchants",
        tone=tone,
        icon=icon,
        title=gettext(title),
        summary=summary,
        badge=badge,
        stat_items=stat_items,
        insight_type=insight_type,
        score=score,
        rank_reason=rank_reason,
        merchant_behavior={
            **metadata,
            "merchant": merchant,
            "current": row["current"],
            "previous": row["previous"],
        },
        selection_metrics=money_selection_metrics(
            max(row["current"], row["previous"]),
            max(row["current"], row["previous"]),
            metadata["behavior"],
            entity_key=merchant,
        ),
    )


def merchant_behavior_score(amount: Any, multiplier: Any) -> Any:
    """Return a deterministic merchant behavior score."""
    return round(min(100.0, capped_ratio(amount, 500.0) * 70.0 + min(multiplier, 5.0) * 6.0), 2)
