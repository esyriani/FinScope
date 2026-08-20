"""Comparison insight card builders and scoring helpers.

Base comparison presenters build rows and period contexts. This module owns the
larger insight subdomain: candidate generation, ranking, card shaping, anomaly
analysis, and unknown-category warnings.
"""

from math import log2, sqrt
from typing import Any

from finance_app.core.i18n import gettext
from finance_app.core.money import money_to_float
from finance_app.modules.comparison.anomaly_insights import (
    robust_anomaly_insight_candidates,
)
from finance_app.modules.comparison.insight_cards import (
    build_stat_insight_card,
    change_insight,
    comparison_bar_width,
    format_money_text,
    format_signed_count,
    format_signed_percent_points,
    largest_change,
    positive_money_float,
)
from finance_app.modules.comparison.insight_scoring import (
    DEFAULT_INSIGHT_CARD_LIMIT,
    activity_selection_metrics,
    build_insight_scoring_context,
    empty_insight_scoring_context,
    money_selection_metrics,
    period_change_selection_metrics,
    score_aggregate_spending_insight,
    score_period_change_insight,
    score_transaction_activity_insight,
    select_ranked_insight_candidates,
)
from finance_app.modules.comparison.merchant_insights import (
    merchant_behavior_insight_candidates,
)

DEFAULT_MIX_SHIFT_MIN_TOTAL_SPENDING = 100.0
DEFAULT_MIX_SHIFT_DISTANCE_THRESHOLD = 0.25
MIX_SHIFT_TOP_CATEGORY_LIMIT = 3


def build_period_insights(
    category_rows: Any,
    merchant_rows: Any,
    current_summary: Any,
    previous_summary: Any,
    *,
    analysis_noun: str = "spending",
    spending_insights: bool = True,
    category_history: Any = None,
    merchant_history: Any = None,
    merchant_activity_history: Any = None,
    ranked: Any = False,
    ranking_options: Any = None,
) -> Any:
    """Build period insights."""
    current_amount = period_row_total(category_rows, "current")
    previous_amount = period_row_total(category_rows, "previous")
    scoring_context = build_insight_scoring_context(
        current_summary,
        previous_summary,
        current_amount,
        previous_amount,
    )
    insights = []
    insights.extend(period_change_insight_candidates(category_rows, merchant_rows, scoring_context))
    if spending_insights:
        insights.extend(new_dropped_spending_candidates(category_rows, merchant_rows, scoring_context))
    insights.append(
        transaction_activity_candidate(
            current_summary,
            previous_summary,
            scoring_context,
            current_amount,
            previous_amount,
            analysis_noun,
        )
    )

    if ranked and spending_insights:
        mix_shift = spending_mix_shift_candidate(category_rows)
        if mix_shift:
            insights.append(mix_shift)
        insights.extend(
            merchant_behavior_insight_candidates(
                merchant_rows,
                merchant_activity_history or {},
            )
        )
        insights.extend(
            robust_anomaly_insight_candidates(
                category_rows,
                merchant_rows,
                category_history or {},
                merchant_history or {},
            )
        )

    if ranked:
        return select_ranked_insight_candidates(insights, **(ranking_options or {}))

    return insights[:DEFAULT_INSIGHT_CARD_LIMIT]


def period_row_total(rows: Any, value_key: str) -> Any:
    """Return the signed total for a period row collection."""
    return sum(money_to_float(row[value_key]) for row in rows)


def period_change_insight_candidates(category_rows: Any, merchant_rows: Any, scoring_context: Any = None) -> Any:
    """Build period-over-period category and merchant change insight candidates."""
    scoring_context = scoring_context or empty_insight_scoring_context()
    insights = []
    category_increase = largest_change(category_rows, "up")
    category_decrease = largest_change(category_rows, "down")
    merchant_increase = largest_change(merchant_rows, "up")
    merchant_decrease = largest_change(merchant_rows, "down")

    if category_increase:
        score, rank_reason = score_period_change_insight(
            category_increase,
            scoring_context,
            "largest absolute category increase",
        )
        insights.append(
            change_insight(
                "Largest category increase",
                category_increase,
                "category",
                insight_type="category_increase",
                score=score,
                rank_reason=rank_reason,
                selection_metrics=period_change_selection_metrics(category_increase),
            )
        )
    if category_decrease:
        score, rank_reason = score_period_change_insight(
            category_decrease,
            scoring_context,
            "largest absolute category decrease",
        )
        insights.append(
            change_insight(
                "Largest category decrease",
                category_decrease,
                "category",
                insight_type="category_decrease",
                score=score,
                rank_reason=rank_reason,
                selection_metrics=period_change_selection_metrics(category_decrease),
            )
        )
    if merchant_increase:
        score, rank_reason = score_period_change_insight(
            merchant_increase,
            scoring_context,
            "largest absolute merchant increase",
        )
        insights.append(
            change_insight(
                "Largest merchant increase",
                merchant_increase,
                "merchant",
                insight_type="merchant_increase",
                score=score,
                rank_reason=rank_reason,
                selection_metrics=period_change_selection_metrics(merchant_increase),
            )
        )
    if merchant_decrease:
        score, rank_reason = score_period_change_insight(
            merchant_decrease,
            scoring_context,
            "largest absolute merchant decrease",
        )
        insights.append(
            change_insight(
                "Largest merchant decrease",
                merchant_decrease,
                "merchant",
                insight_type="merchant_decrease",
                score=score,
                rank_reason=rank_reason,
                selection_metrics=period_change_selection_metrics(merchant_decrease),
            )
        )

    return insights


def new_dropped_spending_candidates(category_rows: Any, merchant_rows: Any, scoring_context: Any = None) -> Any:
    """Build insight candidates for new and dropped spending groups."""
    scoring_context = scoring_context or empty_insight_scoring_context()
    insights = []
    new_merchants = [row for row in merchant_rows if row["state"] == "new"]
    new_categories = [row for row in category_rows if row["state"] == "new"]
    if new_merchants or new_categories:
        total_new_spending = sum(row["current"] for row in new_merchants)
        score, rank_reason = score_aggregate_spending_insight(
            total_new_spending,
            scoring_context,
            "new merchant spending total",
            "new",
        )
        new_merchant_label = gettext(
            "{count} new merchant" if len(new_merchants) == 1 else "{count} new merchants",
            count=len(new_merchants),
        )
        new_category_label = gettext(
            "{count} new category" if len(new_categories) == 1 else "{count} new categories",
            count=len(new_categories),
        )
        insights.append(
            build_stat_insight_card(
                label="New spending this period",
                value=f"{new_merchant_label}, {new_category_label}",
                detail=gettext(
                    "{amount} total new spending",
                    amount=format_money_text(total_new_spending),
                ),
                visual="aggregate",
                group="spending",
                tone="danger",
                icon="bi-stars",
                title=gettext("New spending"),
                summary=format_money_text(total_new_spending),
                badge=gettext("New"),
                stat_items=[
                    {"label": "Merchants", "value": str(len(new_merchants))},
                    {"label": "Categories", "value": str(len(new_categories))},
                ],
                insight_type="new_spending",
                score=score,
                rank_reason=rank_reason,
                selection_metrics=money_selection_metrics(
                    total_new_spending,
                    total_new_spending,
                    "new",
                ),
            )
        )

    dropped_merchants = [row for row in merchant_rows if row["state"] == "dropped"]
    dropped_categories = [row for row in category_rows if row["state"] == "dropped"]
    if dropped_merchants or dropped_categories:
        total_dropped_spending = sum(row["previous"] for row in dropped_merchants)
        score, rank_reason = score_aggregate_spending_insight(
            total_dropped_spending,
            scoring_context,
            "dropped merchant spending total",
            "dropped",
        )
        insights.append(
            build_stat_insight_card(
                label="Dropped spending from prior period",
                value=gettext(
                    (
                        "{count} merchant no longer appears"
                        if len(dropped_merchants) == 1
                        else "{count} merchants no longer appear"
                    ),
                    count=len(dropped_merchants),
                ),
                detail=gettext(
                    (
                        "{amount} less spending across {count} category"
                        if len(dropped_categories) == 1
                        else "{amount} less spending across {count} categories"
                    ),
                    amount=format_money_text(total_dropped_spending),
                    count=len(dropped_categories),
                ),
                visual="aggregate",
                group="spending",
                tone="success",
                icon="bi-dash-circle",
                title=gettext("Dropped spending"),
                summary=format_money_text(total_dropped_spending),
                badge=gettext("Lower"),
                stat_items=[
                    {"label": "Merchants", "value": str(len(dropped_merchants))},
                    {"label": "Categories", "value": str(len(dropped_categories))},
                ],
                insight_type="dropped_spending",
                score=score,
                rank_reason=rank_reason,
                selection_metrics=money_selection_metrics(
                    total_dropped_spending,
                    total_dropped_spending,
                    "dropped",
                ),
            )
        )

    return insights


def transaction_activity_candidate(
    current_summary: Any,
    previous_summary: Any,
    scoring_context: Any = None,
    current_amount: Any = None,
    previous_amount: Any = None,
    analysis_noun: str = "spending",
) -> Any:
    """Build the transaction activity insight candidate."""
    if current_amount is None:
        current_amount = current_summary["spending"]
    if previous_amount is None:
        previous_amount = previous_summary["spending"]
    scoring_context = scoring_context or build_insight_scoring_context(
        current_summary,
        previous_summary,
        current_amount,
        previous_amount,
    )
    current_count = current_summary["transaction_count"] or 0
    previous_count = previous_summary["transaction_count"] or 0
    count_change = current_count - previous_count
    average = money_to_float(current_amount) / current_count if current_count else 0
    score, rank_reason = score_transaction_activity_insight(
        current_count,
        previous_count,
        current_amount,
        previous_amount,
        scoring_context,
    )
    return build_stat_insight_card(
        label="Transaction activity",
        value=gettext(
            "{count} transaction" if current_count == 1 else "{count} transactions",
            count=current_count,
        ),
        detail=gettext(
            "{change} versus prior period. Average {analysis}: {amount}",
            change=format_signed_count(count_change),
            analysis=gettext(analysis_noun),
            amount=format_money_text(average),
        ),
        visual="activity",
        group="spending",
        tone="accent",
        icon="bi-activity",
        title="Transactions",
        summary=f"{current_count}",
        badge=format_signed_count(count_change),
        stat_items=[
            {"label": "Current", "value": f"{current_count}"},
            {"label": "Prior", "value": f"{previous_count}"},
            {"label": "Average", "value": format_money_text(average)},
        ],
        insight_type="transaction_activity",
        score=score,
        rank_reason=rank_reason,
        selection_metrics=activity_selection_metrics(
            current_count,
            previous_count,
            current_amount,
            previous_amount,
        ),
        current_width=comparison_bar_width(current_count, previous_count),
        previous_width=comparison_bar_width(previous_count, current_count),
    )


def spending_mix_shift_candidate(
    category_rows: Any,
    *,
    min_total_spending: Any = DEFAULT_MIX_SHIFT_MIN_TOTAL_SPENDING,
    distance_threshold: Any = DEFAULT_MIX_SHIFT_DISTANCE_THRESHOLD,
    top_category_limit: Any = MIX_SHIFT_TOP_CATEGORY_LIMIT,
) -> Any:
    """Build a spending mix shift candidate from period category rows."""
    mix_shift = spending_mix_shift(category_rows)
    if (
        mix_shift["current_total"] < min_total_spending
        or mix_shift["previous_total"] < min_total_spending
        or mix_shift["js_distance"] < distance_threshold
    ):
        return None

    top_changes = mix_shift["share_changes"][:top_category_limit]
    return build_stat_insight_card(
        label="Spending mix changed",
        value=gettext("Spending moved across categories"),
        detail=gettext(
            "The largest category share changes are shown below.",
        ),
        visual="aggregate",
        group="spending",
        tone="accent",
        icon="bi-pie-chart",
        title="Category mix changed",
        summary=gettext(
            "{count} category shifted" if len(top_changes) == 1 else "{count} categories shifted",
            count=len(top_changes),
        ),
        badge=gettext("Mix shift"),
        stat_items=[
            {
                "label": "Category",
                "value": gettext(
                    "{category}: {change} points",
                    category=change["category"],
                    change=format_signed_percent_points(change["change_points"]),
                ),
            }
            for change in top_changes
        ],
        insight_type="spending_mix_shift",
        score=round(mix_shift["js_distance"] * 100, 2),
        rank_reason=(
            "spending mix shift; "
            f"jsd={mix_shift['js_distance']:.3f}; "
            f"current={mix_shift['current_total']:.2f}; "
            f"previous={mix_shift['previous_total']:.2f}"
        ),
        mix_shift=mix_shift,
        selection_metrics={
            "metric": "mix",
            "absolute_change": mix_shift["js_distance"],
            "direction": "mix_shift",
        },
    )


def spending_mix_shift(category_rows: Any) -> Any:
    """Return spending share movement metadata for period category rows."""
    current_total = sum(positive_money_float(row["current"]) for row in category_rows)
    previous_total = sum(positive_money_float(row["previous"]) for row in category_rows)
    categories = sorted(
        {
            row["category"]
            for row in category_rows
            if positive_money_float(row["current"]) > 0 or positive_money_float(row["previous"]) > 0
        },
        key=lambda category: str(category).casefold(),
    )
    current_distribution = spending_share_distribution(category_rows, categories, "current", current_total)
    previous_distribution = spending_share_distribution(category_rows, categories, "previous", previous_total)
    share_changes = [
        {
            "category": category,
            "current_share": current_distribution[index],
            "previous_share": previous_distribution[index],
            "change_points": (current_distribution[index] - previous_distribution[index]) * 100,
            "abs_change_points": abs(current_distribution[index] - previous_distribution[index]) * 100,
        }
        for index, category in enumerate(categories)
    ]
    share_changes.sort(
        key=lambda change: (
            -change["abs_change_points"],
            change["category"].casefold(),
        )
    )
    return {
        "js_distance": jensen_shannon_distance(current_distribution, previous_distribution),
        "current_total": round(current_total, 2),
        "previous_total": round(previous_total, 2),
        "share_changes": share_changes,
    }


def spending_share_distribution(category_rows: Any, categories: Any, value_key: Any, total: Any) -> Any:
    """Return category spending shares in a stable category order."""
    values_by_category = {row["category"]: positive_money_float(row[value_key]) for row in category_rows}
    if total <= 0:
        return [0.0 for _category in categories]
    return [values_by_category.get(category, 0.0) / total for category in categories]


def jensen_shannon_distance(current_distribution: Any, previous_distribution: Any) -> Any:
    """Return Jensen-Shannon distance for two probability distributions."""
    if not current_distribution or not previous_distribution:
        return 0.0

    midpoint = [(current + previous) / 2 for current, previous in zip(current_distribution, previous_distribution)]
    divergence = (
        kullback_leibler_divergence(current_distribution, midpoint)
        + kullback_leibler_divergence(previous_distribution, midpoint)
    ) / 2
    return sqrt(divergence)


def kullback_leibler_divergence(distribution: Any, baseline: Any) -> Any:
    """Return KL divergence using log base 2, ignoring zero-probability terms."""
    return sum(
        value * log2(value / baseline[index])
        for index, value in enumerate(distribution)
        if value > 0 and baseline[index] > 0
    )
