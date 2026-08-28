"""Card formatting helpers for comparison insight presenters."""

from typing import Any

from finance_app.core.i18n import gettext
from finance_app.core.money import format_money_display, money_to_float, rounded_money_float
from finance_app.modules.comparison.constants import UNKNOWN_WARNING_THRESHOLD


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
    noun = gettext(row.get("noun") or "spending")
    if row["state"] == "new":
        value = gettext("{name}: new {noun} this period", name=name, noun=noun)
    elif row["state"] == "dropped":
        value = gettext("{name}: no {noun} this period", name=name, noun=noun)
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
    if row.get("tone"):
        return row["tone"]
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
