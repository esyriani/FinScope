"""View-model builders for the comparison feature."""

from calendar import monthrange
from datetime import date
from math import log2, sqrt
from typing import Any

from finance_app.core.i18n import format_month_year, gettext
from finance_app.core.money import format_money_display, money_to_float, rounded_money_float
from finance_app.modules.comparison.constants import UNKNOWN_WARNING_THRESHOLD
from finance_app.modules.comparison.statistics import build_descriptive_statistics, robust_anomaly_score
from finance_app.modules.merchants.normalization import normalize_merchant

DEFAULT_INSIGHT_CARD_LIMIT = 7
DEFAULT_RANKED_INSIGHT_MIN_SCORE = 10.0
DEFAULT_RANKED_INSIGHT_MIN_MONEY_CHANGE = 5.0
MIN_ROBUST_ANOMALY_HISTORY_PERIODS = 5
DEFAULT_ROBUST_ANOMALY_MIN_ABSOLUTE_DIFFERENCE = 25.0
DEFAULT_MIX_SHIFT_MIN_TOTAL_SPENDING = 100.0
DEFAULT_MIX_SHIFT_DISTANCE_THRESHOLD = 0.25
MIX_SHIFT_TOP_CATEGORY_LIMIT = 3
DEFAULT_MERCHANT_BEHAVIOR_MIN_SPENDING = 25.0
MERCHANT_RESURRECTION_MIN_ABSENCE_MONTHS = 3
MERCHANT_RANK_INCREASE_MIN_PLACES = 3


def build_monthly_spending(years: Any, rows: Any) -> Any:
    """Build monthly spending."""
    by_year = {year: [0.0 for _ in range(12)] for year in years}
    for row in rows:
        if row["year"] in by_year and 1 <= row["month"] <= 12:
            by_year[row["year"]][row["month"] - 1] = rounded_money_float(row["spending"])
    return by_year


def build_monthly_spending_statistics(years: Any, rows: Any) -> Any:
    """Build descriptive statistics for observed monthly spending totals by year.

    The comparison chart keeps its existing zero-filled twelve-month shape.
    Statistics intentionally use only fetched monthly rows so months outside
    the imported data, especially future months in the current year, are not
    treated as real zero-spending periods.
    """
    values_by_year: dict[Any, list[Any]] = {year: [] for year in years}
    for row in rows:
        year = row["year"]
        if year in values_by_year:
            values_by_year[year].append(row["spending"])

    result = []
    for year in years:
        statistics = build_descriptive_statistics(values_by_year[year])
        result.append(
            {
                "year": year,
                "statistics": statistics,
                "boxplot": statistics["boxplot"],
            }
        )
    return result


def build_category_comparison(years: Any, rows: Any, baseline_year: Any = None) -> Any:
    """Build category comparison."""
    categories: dict[Any, dict[Any, Any]] = {}
    for row in rows:
        category = row["category"]
        categories.setdefault(category, {year: 0 for year in years})
        if row["year"] in categories[category]:
            categories[category][row["year"]] = rounded_money_float(row["spending"])

    result = []
    for category, totals in categories.items():
        result.append(
            {
                "category": category,
                "totals": totals,
                "changes": build_year_changes(years, totals, baseline_year),
                "total": rounded_money_float(sum(totals.values())),
            }
        )

    return sorted(result, key=lambda row: row["total"], reverse=True)


def build_year_changes(years: Any, totals: Any, baseline_year: Any = None) -> Any:
    """Build year changes."""
    changes: dict[Any, Any] = {}
    for year in years:
        if baseline_year:
            compare_year = baseline_year if year != baseline_year else None
        else:
            compare_year = year - 1 if year - 1 in totals else None

        if compare_year is None:
            changes[year] = None
            continue

        current = totals.get(year, 0)
        previous = totals.get(compare_year, 0)
        percent = percentage_change(current, previous)
        change = round(current - previous, 2)
        changes[year] = {
            "baseline_year": compare_year,
            "change": change,
            "abs_change": abs(change),
            "percent": percent,
            "amount_label": format_signed_money_text(change),
            "percent_label": format_change_label(current, previous, percent),
            "direction": "up" if change > 0 else "down" if change < 0 else "flat",
            "state": change_state(current, previous),
        }

    return changes


def period_comparison_ranges(comparison_key: Any, today: Any) -> Any:
    """Build comparison ranges."""
    month_start = today.replace(day=1)

    if comparison_key == "month_last_year":
        previous_start = safe_date(today.year - 1, today.month, 1)
        previous_end = safe_date(today.year - 1, today.month, today.day)
        return {
            "current_start": month_start.isoformat(),
            "current_end": today.isoformat(),
            "previous_start": previous_start.isoformat(),
            "previous_end": previous_end.isoformat(),
            "current_label": gettext("{month} to date", month=format_month_year(today)),
            "previous_label": gettext("{month} to same day", month=format_month_year(previous_start)),
            "previous_short_label": gettext("same month last year"),
        }

    if comparison_key == "ytd_last_year":
        previous_end = safe_date(today.year - 1, today.month, today.day)
        return {
            "current_start": date(today.year, 1, 1).isoformat(),
            "current_end": today.isoformat(),
            "previous_start": date(today.year - 1, 1, 1).isoformat(),
            "previous_end": previous_end.isoformat(),
            "current_label": gettext("{year} year to date", year=today.year),
            "previous_label": gettext("{year} same period", year=today.year - 1),
            "previous_short_label": gettext("same period last year"),
        }

    previous_month_year = today.year if today.month > 1 else today.year - 1
    previous_month = today.month - 1 if today.month > 1 else 12
    previous_start = date(previous_month_year, previous_month, 1)
    previous_end = safe_date(previous_month_year, previous_month, today.day)
    return {
        "current_start": month_start.isoformat(),
        "current_end": today.isoformat(),
        "previous_start": previous_start.isoformat(),
        "previous_end": previous_end.isoformat(),
        "current_label": gettext("{month} to date", month=format_month_year(today)),
        "previous_label": gettext("{month} to same day", month=format_month_year(previous_start)),
        "previous_short_label": gettext("last month"),
    }


def safe_date(year: Any, month: Any, day: Any) -> Any:
    """Return a safe date."""
    return date(year, month, min(day, monthrange(year, month)[1]))


def build_period_category_rows(current_rows: Any, previous_rows: Any) -> Any:
    """Build period category rows."""
    current = {row["category"]: money_to_float(row["spending"]) for row in current_rows}
    previous = {row["category"]: money_to_float(row["spending"]) for row in previous_rows}
    return build_period_rows("category", sorted(set(current) | set(previous)), current, previous)


def build_period_merchant_rows(current_rows: Any, previous_rows: Any, conn: Any) -> Any:
    """Build period merchant rows."""
    current = build_merchant_period_totals(current_rows, conn)
    previous = build_merchant_period_totals(previous_rows, conn)
    rows = build_period_rows("merchant", sorted(set(current) | set(previous)), current, previous)

    category_by_merchant = build_merchant_primary_categories(conn, current_rows, previous_rows)
    for row in rows:
        row["category"] = category_by_merchant.get(row["merchant"], "n/a")

    return rows


def build_merchant_period_totals(rows: Any, conn: Any) -> Any:
    """Build merchant period totals."""
    totals: dict[Any, Any] = {}
    for row in rows:
        merchant = normalize_merchant(row["description"], conn=conn).merchant_key
        if not merchant:
            continue
        totals[merchant] = totals.get(merchant, 0) + money_to_float(row["amount"])
    return totals


def build_merchant_primary_categories(conn: Any, *row_groups: Any) -> Any:
    """Build merchant primary categories."""
    category_totals: dict[Any, dict[Any, Any]] = {}
    for rows in row_groups:
        for row in rows:
            merchant = normalize_merchant(row["description"], conn=conn).merchant_key
            if not merchant:
                continue
            totals = category_totals.setdefault(merchant, {})
            totals[row["category"]] = totals.get(row["category"], 0) + money_to_float(row["amount"])

    result = {}
    for merchant, totals in category_totals.items():
        result[merchant] = max(totals, key=lambda category: (totals[category], category))
    return result


def build_period_category_history(rows: Any) -> Any:
    """Build category history values from historical monthly spending rows."""
    history: dict[Any, list[Any]] = {}
    for row in rows:
        history.setdefault(row["category"], []).append(row["spending"])
    return history


def build_period_merchant_history(rows: Any, conn: Any) -> Any:
    """Build merchant history values from historical monthly transaction rows."""
    monthly_totals: dict[Any, Any] = {}
    for row in rows:
        merchant = normalize_merchant(row["description"], conn=conn).merchant_key
        if not merchant:
            continue
        key = (merchant, row["year"], row["month"])
        monthly_totals[key] = monthly_totals.get(key, 0) + money_to_float(row["amount"])

    history: dict[Any, list[Any]] = {}
    for (merchant, _year, _month), total in monthly_totals.items():
        history.setdefault(merchant, []).append(total)
    return history


def build_period_merchant_activity_history(rows: Any, conn: Any, current_start: Any) -> Any:
    """Build merchant monthly activity metadata from historical transaction rows."""
    current_period_index = month_index_from_date(current_start)
    monthly_totals: dict[Any, Any] = {}
    for row in rows:
        merchant = normalize_merchant(row["description"], conn=conn).merchant_key
        if not merchant:
            continue
        key = (merchant, row["year"], row["month"])
        monthly_totals[key] = monthly_totals.get(key, 0) + money_to_float(row["amount"])

    by_merchant: dict[Any, list[dict[str, Any]]] = {}
    for (merchant, year, month), total in monthly_totals.items():
        period_index = month_index(year, month)
        by_merchant.setdefault(merchant, []).append(
            {
                "year": year,
                "month": month,
                "period_index": period_index,
                "total": rounded_money_float(total),
            }
        )

    result = {}
    for merchant, periods in by_merchant.items():
        periods.sort(key=lambda period: period["period_index"], reverse=True)
        last_period = periods[0]
        result[merchant] = {
            "periods": periods,
            "history_count": len(periods),
            "last_activity_months_ago": current_period_index - last_period["period_index"],
            "last_activity_label": f"{last_period['year']}-{last_period['month']:02d}",
        }
    return result


def month_index_from_date(value: Any) -> Any:
    """Return a comparable month index for a date or ISO date string."""
    if isinstance(value, str):
        value = date.fromisoformat(value)
    return month_index(value.year, value.month)


def month_index(year: Any, month: Any) -> Any:
    """Return a comparable month index."""
    return (int(year) * 12) + int(month)


def build_period_rows(label_key: Any, labels: Any, current: Any, previous: Any) -> Any:
    """Build period rows."""
    rows = []
    for label in labels:
        metric = build_period_metric(
            label,
            current.get(label, 0),
            previous.get(label, 0),
            "spending",
            "",
        )
        rows.append(
            {
                label_key: label,
                **metric,
            }
        )

    rows.sort(key=lambda row: abs(row["change"]), reverse=True)
    return rows


def build_period_metric(
    label: Any, current: Any, previous: Any, noun: Any, previous_label: Any, value_type: Any = "money"
) -> Any:
    """Build period metric."""
    if value_type == "count":
        current = round(current or 0, 2)
        previous = round(previous or 0, 2)
        change = round(current - previous, 2)
    else:
        current = rounded_money_float(current)
        previous = rounded_money_float(previous)
        change = rounded_money_float(current - previous)
    percent = percentage_change(current, previous)
    direction = "up" if change > 0 else "down" if change < 0 else "flat"
    state = change_state(current, previous)
    return {
        "label": label,
        "value_type": value_type,
        "current": current,
        "previous": previous,
        "change": change,
        "abs_change": abs(change),
        "percent": percent,
        "amount_label": format_signed_count(change) if value_type == "count" else format_signed_money_text(change),
        "percent_label": format_change_label(current, previous, percent),
        "direction": direction,
        "state": state,
        "sentence": period_change_sentence(label, noun, change, percent, previous, current, previous_label),
    }


def percentage_change(current: Any, previous: Any) -> Any:
    """Handle percentage change."""
    if previous == 0:
        return None
    return round(((current - previous) / previous) * 100, 1)


def change_state(current: Any, previous: Any) -> Any:
    """Build state."""
    if current == 0 and previous == 0:
        return "no_activity"
    if current > 0 and previous == 0:
        return "new"
    if current == 0 and previous > 0:
        return "dropped"
    if current == previous:
        return "no_change"
    return "changed"


def format_change_label(current: Any, previous: Any, percent: Any) -> Any:
    """Format change label."""
    state = change_state(current, previous)
    labels = {
        "no_activity": "No activity",
        "new": "New",
        "dropped": "Dropped",
        "no_change": "No change",
    }
    if state in labels:
        return gettext(labels[state])
    return f"{percent:+.1f}%" if percent is not None else "n/a"


def period_change_sentence(
    label: Any, noun: Any, change: Any, percent: Any, previous: Any, current: Any, previous_label: Any
) -> Any:
    """Build change sentence."""
    if previous == 0 and current == 0:
        return gettext(
            "{label} {noun} is unchanged versus {period}.",
            label=label,
            noun=noun,
            period=previous_label,
        )
    if previous == 0:
        return gettext(
            "{label} {noun} is new versus {period}.",
            label=label,
            noun=noun,
            period=previous_label,
        )

    direction = "up" if change > 0 else "down" if change < 0 else "unchanged"
    if direction == "unchanged":
        return gettext(
            "{label} {noun} is unchanged versus {period}.",
            label=label,
            noun=noun,
            period=previous_label,
        )

    return gettext(
        "{label} {noun} is {direction} {amount}, or {percent}%, versus {period}.",
        label=label,
        noun=noun,
        direction=gettext(direction),
        amount=format_money_text(abs(change)),
        percent=abs(percent),
        period=previous_label,
    )


def build_period_insights(
    category_rows: Any,
    merchant_rows: Any,
    current_summary: Any,
    previous_summary: Any,
    *,
    category_history: Any = None,
    merchant_history: Any = None,
    merchant_activity_history: Any = None,
    ranked: Any = False,
    ranking_options: Any = None,
) -> Any:
    """Build period insights."""
    scoring_context = build_insight_scoring_context(current_summary, previous_summary)
    insights = []
    insights.extend(period_change_insight_candidates(category_rows, merchant_rows, scoring_context))
    insights.extend(new_dropped_spending_candidates(category_rows, merchant_rows, scoring_context))
    insights.append(transaction_activity_candidate(current_summary, previous_summary, scoring_context))

    if ranked:
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
        return select_ranked_insight_candidates(insights, **(ranking_options or {}))

    return insights[:DEFAULT_INSIGHT_CARD_LIMIT]


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


def transaction_activity_candidate(current_summary: Any, previous_summary: Any, scoring_context: Any = None) -> Any:
    """Build the transaction activity insight candidate."""
    scoring_context = scoring_context or build_insight_scoring_context(
        current_summary,
        previous_summary,
    )
    current_count = current_summary["transaction_count"] or 0
    previous_count = previous_summary["transaction_count"] or 0
    count_change = current_count - previous_count
    average = money_to_float(current_summary["spending"]) / current_count if current_count else 0
    score, rank_reason = score_transaction_activity_insight(
        current_count,
        previous_count,
        current_summary["spending"],
        previous_summary["spending"],
        scoring_context,
    )
    return build_stat_insight_card(
        label="Transaction activity",
        value=gettext(
            "{count} transaction" if current_count == 1 else "{count} transactions",
            count=current_count,
        ),
        detail=gettext(
            "{change} versus prior period. Average transaction: {amount}",
            change=format_signed_count(count_change),
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
            current_summary["spending"],
            previous_summary["spending"],
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


def build_insight_scoring_context(current_summary: Any, previous_summary: Any) -> Any:
    """Return period-level values used to score insight candidates."""
    current_spending = abs(money_to_float(current_summary["spending"]))
    previous_spending = abs(money_to_float(previous_summary["spending"]))
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


def largest_change(rows: Any, direction: Any) -> Any:
    """Handle largest change."""
    candidates = [row for row in rows if row["direction"] == direction]
    return max(candidates, key=lambda row: row["abs_change"], default=None)


def build_insight_card(
    *,
    label: Any,
    value: Any,
    detail: Any,
    visual: Any,
    group: Any,
    tone: Any,
    icon: Any,
    title: Any,
    summary: Any,
    badge: Any,
    insight_type: Any,
    score: Any,
    rank_reason: Any,
    **extra_fields: Any,
) -> Any:
    """Build the common insight-card view model."""
    card = {
        "label": label,
        "value": value,
        "detail": detail,
        "visual": visual,
        "group": group,
        "tone": tone,
        "icon": icon,
        "title": title,
        "summary": summary,
        "badge": badge,
        "insight_type": insight_type,
        "score": score,
        "rank_reason": rank_reason,
    }
    card.update(extra_fields)
    return card


def build_comparison_insight_card(
    label: Any,
    row: Any,
    label_key: Any,
    value: Any,
    insight_type: Any,
    score: Any,
    rank_reason: Any,
    **extra_fields: Any,
) -> Any:
    """Build an insight card that compares prior and current values."""
    return build_insight_card(
        label=label,
        value=value,
        detail=gettext(
            "Prior: {prior}. Current: {current}",
            prior=format_money_text(row["previous"]),
            current=format_money_text(row["current"]),
        ),
        visual="comparison",
        group="categories" if label_key == "category" else "merchants",
        tone=change_insight_tone(row),
        icon=change_insight_icon(row),
        title=row[label_key],
        summary=row["amount_label"],
        badge=gettext(row["percent_label"]),
        insight_type=insight_type,
        score=score,
        rank_reason=rank_reason,
        previous_label=format_money_text(row["previous"]),
        current_label=format_money_text(row["current"]),
        previous_width=comparison_bar_width(row["previous"], row["current"]),
        current_width=comparison_bar_width(row["current"], row["previous"]),
        **extra_fields,
    )


def build_stat_insight_card(*, stat_items: Any, **fields: Any) -> Any:
    """Build an insight card whose body renders compact stat items."""
    return build_insight_card(stat_items=stat_items, **fields)


def change_insight(
    label: Any,
    row: Any,
    label_key: Any,
    insight_type: Any = "",
    score: Any = 0.0,
    rank_reason: Any = "",
    **extra_fields: Any,
) -> Any:
    """Build insight."""
    name = row[label_key]
    if row["state"] == "new":
        value = gettext("{name}: new spending this period", name=name)
    elif row["state"] == "dropped":
        value = gettext("{name}: dropped from current period", name=name)
    else:
        value = f"{name} {format_signed_money_text(row['change'])} ({row['percent_label']})"

    return build_comparison_insight_card(
        label,
        row,
        label_key,
        value,
        insight_type,
        score,
        rank_reason,
        **extra_fields,
    )


def change_insight_tone(row: Any) -> Any:
    """Return the visual tone for a period insight row."""
    return "danger" if row["direction"] == "up" else "success" if row["direction"] == "down" else "muted"


def change_insight_icon(row: Any) -> Any:
    """Return a Bootstrap icon class for a period insight row."""
    if row["state"] == "new":
        return "bi-plus-circle"
    if row["state"] == "dropped":
        return "bi-dash-circle"
    if row["direction"] == "down":
        return "bi-graph-down-arrow"
    return "bi-graph-up-arrow"


def comparison_bar_width(value: Any, comparison_value: Any) -> Any:
    """Return a percent width for comparing two non-negative visual bars."""
    maximum = max(abs(value or 0), abs(comparison_value or 0))
    if maximum == 0:
        return 0
    return round((abs(value or 0) / maximum) * 100, 1)


def build_period_insight_groups(insights: Any) -> Any:
    """Group period insights into carousel sections."""
    grouped: dict[str, dict[str, Any]] = {
        "categories": {
            "key": "categories",
            "label": "Categories",
            "insights": [],
        },
        "merchants": {
            "key": "merchants",
            "label": "Merchants",
            "insights": [],
        },
        "spending": {
            "key": "spending",
            "label": "Spending and transactions",
            "insights": [],
        },
    }

    for insight in insights:
        group_key = insight.get("group", "spending")
        if group_key not in grouped:
            group_key = "spending"
        grouped[group_key]["insights"].append(insight)

    return [group for group in grouped.values() if group["insights"]]


def build_period_unknown_warning(
    category_rows: Any, current_spending: Any, previous_spending: Any, unknown_category: Any
) -> Any:
    """Build period unknown warning."""
    unknown = next((row for row in category_rows if row["category"] == unknown_category), None)
    if not unknown:
        return None

    current_share = percentage_share(unknown["current"], current_spending)
    previous_share = percentage_share(unknown["previous"], previous_spending)
    largest_share = max(current_share, previous_share)
    if largest_share < UNKNOWN_WARNING_THRESHOLD:
        return None

    return build_unknown_warning_message(
        "Category insights may be incomplete because {category} accounts for {share}% of selected spending.",
        unknown_category,
        largest_share,
    )


def build_year_unknown_warning(category_comparison: Any, unknown_category: Any) -> Any:
    """Build year unknown warning."""
    total = sum(row["total"] for row in category_comparison)
    unknown = next((row for row in category_comparison if row["category"] == unknown_category), None)
    if not unknown or not total:
        return None

    share = percentage_share(unknown["total"], total)
    largest_category = max(category_comparison, key=lambda row: row["total"], default=None)
    if share < UNKNOWN_WARNING_THRESHOLD and largest_category != unknown:
        return None

    return build_unknown_warning_message(
        "Category comparison may be unreliable because {category} accounts for {share}% of selected spending.",
        unknown_category,
        share,
    )


def build_unknown_warning_message(source: Any, category: Any, share: Any) -> Any:
    """Build a translatable warning message with its interpolation values."""
    values = {
        "category": category,
        "share": f"{share:.1f}",
    }
    return {
        "source": source,
        "values": values,
        "text": gettext(source, **values),
    }


def percentage_share(value: Any, total: Any) -> Any:
    """Handle percentage share."""
    value = money_to_float(value)
    total = money_to_float(total)
    return round((value / total) * 100, 1) if total else 0


def format_signed_count(value: Any) -> Any:
    """Format signed count."""
    value = int(round(value or 0))
    return f"{value:+d}" if value else "0"


def format_signed_percent_points(value: Any) -> Any:
    """Format a signed percentage-point value for compact stat items."""
    value = round(money_to_float(value), 1)
    prefix = "+" if value > 0 else "-" if value < 0 else ""
    return f"{prefix}{abs(value):.1f}"


def format_months_ago(months: Any) -> Any:
    """Format a month-count label."""
    months = int(months or 0)
    return gettext("{count} month ago" if months == 1 else "{count} months ago", count=months)


def format_rank(rank: Any) -> Any:
    """Format a 1-based rank label."""
    return f"#{int(rank)}"


def positive_money_float(value: Any) -> Any:
    """Return non-negative money as a float for share calculations."""
    return max(money_to_float(value), 0.0)


def format_money_text(value: Any) -> Any:
    """Format money text."""
    return format_money_display(value)


def format_signed_money_text(value: Any) -> Any:
    """Format signed money text."""
    value = rounded_money_float(value)
    prefix = "+" if value > 0 else "-" if value < 0 else ""
    return f"{prefix}{format_money_text(abs(value))}"


def build_category_context(categories: Any) -> Any:
    """Build category context."""
    return ", ".join(categories) if categories else gettext("All categories")


def build_tag_context(tags: Any) -> Any:
    """Build tag context."""
    return ", ".join(tags) if tags else gettext("All tags")


def build_account_context(account_name: Any = "") -> Any:
    """Build account context."""
    return str(account_name or "").strip() or gettext("All accounts")


def build_period_filter_context(option_label: Any, categories: Any, tags: Any = None, account_name: Any = "") -> Any:
    """Build period filter context."""
    return gettext(
        "{period} - Account: {account} - Categories: {categories} - Tags: {tags}",
        period=gettext(option_label),
        account=build_account_context(account_name),
        categories=build_category_context(categories),
        tags=build_tag_context(tags or []),
    )


def build_year_filter_context(
    years: Any,
    baseline_year: Any,
    categories: Any,
    tags: Any = None,
    account_name: Any = "",
) -> Any:
    """Build year filter context."""
    baseline_label = str(baseline_year) if baseline_year else gettext("previous year")
    return gettext(
        "Years: {years} - Baseline: {baseline} - Account: {account} - Categories: {categories} - Tags: {tags}",
        years=", ".join(str(year) for year in years),
        baseline=baseline_label,
        account=build_account_context(account_name),
        categories=build_category_context(categories),
        tags=build_tag_context(tags or []),
    )
