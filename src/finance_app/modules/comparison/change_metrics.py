"""Shared period-change metrics and labels for comparison presenters."""

from typing import Any

from finance_app.core.i18n import gettext
from finance_app.modules.comparison.insight_cards import format_money_text


def direction_tone(direction: str, positive_tone: str = "danger") -> str:
    """Return a semantic tone for a positive or negative movement."""
    if direction == "up":
        return positive_tone
    if direction == "down":
        return "success" if positive_tone == "danger" else "danger"
    return ""


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
