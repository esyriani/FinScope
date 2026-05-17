"""Application orchestration for the recurring feature."""

from calendar import Calendar
from datetime import date
from urllib.parse import urlencode

from flask import url_for

from finance_app.core.i18n import format_month_year, gettext, weekday_abbreviation_labels
from finance_app.modules.calendar.service import (
    build_recurring_activity_context,
    build_recurring_activity_json,
    clean_categories,
    clean_tags,
    default_month,
    parse_month,
    recurring_amount_change_cashflow_impact,
    shift_month,
)


RECURRING_VIEWS = {"list", "calendar"}
STATUS_OPTIONS = [
    {"value": "occurred", "label": "Occurred"},
    {"value": "amount_changed", "label": "Amount changed"},
    {"value": "likely_occurred", "label": "Likely occurred"},
    {"value": "expected", "label": "Expected"},
    {"value": "overdue", "label": "Overdue"},
    {"value": "possibly_inactive", "label": "Possibly inactive"},
]
CONFIDENCE_OPTIONS = ["High", "Medium", "Low"]


def build_recurring_page_context(args):
    """Build recurring page context."""
    selected_month = parse_month(args.get("month")) or default_month()
    selected_categories = clean_categories(args.getlist("categories"))
    selected_tags = clean_tags(args.getlist("tags"))
    selected_recurring_view = parse_recurring_view(args.get("view"))
    selected_statuses = clean_statuses(args.getlist("statuses"))
    selected_confidence = parse_confidence(args.get("confidence"))
    recurring_context = build_recurring_activity_context(
        selected_month,
        selected_categories,
        selected_tags,
    )
    recurring_items = filter_recurring_items(
        recurring_context["recurring_items"],
        selected_statuses,
        selected_confidence,
    )

    return {
        "selected_month": recurring_context["month_start"].isoformat()[:7],
        "month_label": format_month_year(recurring_context["month_start"]),
        "selected_categories": selected_categories,
        "category_options": recurring_context["category_options"],
        "selected_tags": selected_tags,
        "tag_options": recurring_context["tag_options"],
        "selected_recurring_view": selected_recurring_view,
        "selected_statuses": selected_statuses,
        "selected_confidence": selected_confidence,
        "status_options": STATUS_OPTIONS,
        "confidence_options": CONFIDENCE_OPTIONS,
        "previous_month_url": recurring_filter_url(
            shift_month(recurring_context["month_start"], -1),
            selected_categories,
            selected_tags,
            selected_recurring_view,
            selected_statuses,
            selected_confidence,
        ),
        "next_month_url": recurring_filter_url(
            shift_month(recurring_context["month_start"], 1),
            selected_categories,
            selected_tags,
            selected_recurring_view,
            selected_statuses,
            selected_confidence,
        ),
        "current_month_url": recurring_filter_url(
            default_month(),
            selected_categories,
            selected_tags,
            selected_recurring_view,
            selected_statuses,
            selected_confidence,
        ),
        "list_view_url": recurring_view_url(
            recurring_context["month_start"],
            selected_categories,
            selected_tags,
            "list",
            selected_statuses,
            selected_confidence,
        ),
        "calendar_view_url": recurring_view_url(
            recurring_context["month_start"],
            selected_categories,
            selected_tags,
            "calendar",
            selected_statuses,
            selected_confidence,
        ),
        "clear_filters_url": recurring_clear_url(selected_recurring_view),
        "recurring_summary": build_recurring_summary(recurring_items),
        "recurring_items": recurring_items,
        "recurring_activity_json": build_recurring_activity_json(recurring_items),
        "recurring_calendar_days": build_recurring_calendar_days(
            recurring_context["month_start"],
            recurring_items,
        ),
        "recurring_calendar_legend": build_recurring_calendar_legend(recurring_items),
        "weekday_labels": weekday_abbreviation_labels(),
    }


def parse_recurring_view(value):
    """Parse recurring view."""
    value = str(value or "").strip().lower()
    return value if value in RECURRING_VIEWS else "list"


def clean_statuses(values):
    """Clean statuses."""
    valid_statuses = {option["value"] for option in STATUS_OPTIONS}
    return [
        status
        for status in (str(value or "").strip() for value in values)
        if status in valid_statuses
    ]


def parse_confidence(value):
    """Parse confidence."""
    value = str(value or "").strip()
    return value if value in CONFIDENCE_OPTIONS else ""


def filter_recurring_items(recurring_items, selected_statuses, selected_confidence):
    """Filter recurring items."""
    filtered = recurring_items
    if selected_statuses:
        selected_status_set = set(selected_statuses)
        filtered = [item for item in filtered if item["status"] in selected_status_set]
    if selected_confidence:
        filtered = [item for item in filtered if item["confidence"] == selected_confidence]
    return filtered


def build_recurring_summary(recurring_items):
    """Build recurring summary."""
    amount_changed_items = [item for item in recurring_items if item.get("amount_change")]
    active_statuses = {"occurred", "amount_changed", "likely_occurred", "expected", "overdue", "matched"}
    expected_statuses = {"expected"}
    occurred_statuses = {"occurred", "amount_changed", "likely_occurred", "matched"}
    return {
        "active_count": len([
            item
            for item in recurring_items
            if item.get("active", 1) and item["status"] in active_statuses
        ]),
        "expected_count": len([item for item in recurring_items if item["status"] in expected_statuses]),
        "occurred_count": len([item for item in recurring_items if item["status"] in occurred_statuses]),
        "overdue_count": len([item for item in recurring_items if item["status"] == "overdue"]),
        "recurring_spending": round(sum(
            item["amount"]
            for item in recurring_items
            if item["type"] == "spending" and item["status"] != "possibly_inactive"
        ), 2),
        "recurring_income": round(sum(
            item["amount"]
            for item in recurring_items
            if item["type"] == "income" and item["status"] != "possibly_inactive"
        ), 2),
        "amount_change_count": len(amount_changed_items),
        "amount_change_total_impact": round(
            sum(recurring_amount_change_cashflow_impact(item) for item in amount_changed_items),
            2,
        ),
    }


def recurring_view_url(
    month_start,
    selected_categories,
    selected_tags,
    view,
    selected_statuses=None,
    selected_confidence="",
):
    """Build view URL."""
    return recurring_filter_url(
        month_start,
        selected_categories,
        selected_tags,
        view,
        selected_statuses,
        selected_confidence,
    )


def recurring_filter_url(
    month_start,
    selected_categories,
    selected_tags,
    view,
    selected_statuses=None,
    selected_confidence="",
):
    """Build a recurring URL while preserving filter state."""
    params = {
        "month": month_start.isoformat()[:7],
        "view": view,
    }
    if selected_categories:
        params["categories"] = selected_categories
    if selected_tags:
        params["tags"] = selected_tags
    if selected_statuses:
        params["statuses"] = selected_statuses
    if selected_confidence:
        params["confidence"] = selected_confidence

    return f"{url_for('recurring.recurring')}?{urlencode(params, doseq=True)}"


def recurring_clear_url(view):
    """Build clear URL."""
    return f"{url_for('recurring.recurring')}?{urlencode({'view': view})}"


def build_recurring_calendar_days(month_start, recurring_items):
    """Build recurring calendar days."""
    items_by_date = {}
    for item in recurring_items:
        placement_date = recurring_calendar_item_date(item)
        items_by_date.setdefault(placement_date, []).append(recurring_calendar_chip(item))

    days = []
    for day in Calendar(firstweekday=0).itermonthdates(month_start.year, month_start.month):
        day_key = day.isoformat()
        day_items = sorted(
            items_by_date.get(day_key, []),
            key=lambda chip: (chip["status_label"], chip["merchant"]),
        )
        visible_items = day_items[:3]
        days.append(
            {
                "date": day_key,
                "day_number": day.day,
                "in_month": day.month == month_start.month,
                "is_today": day_key == date.today().isoformat(),
                "recurring_items": visible_items,
                "all_recurring_items": day_items,
                "more_count": max(0, len(day_items) - len(visible_items)),
            }
        )

    return days


def recurring_calendar_item_date(item):
    """Build calendar item date."""
    match_details = item.get("match_details") or {}
    matched_date = match_details.get("matched_date")
    if item["status"] in {"occurred", "likely_occurred", "amount_changed", "matched"} and matched_date:
        return matched_date
    return item["date"]


def recurring_calendar_chip(item):
    """Build calendar chip."""
    return {
        "id": item["id"],
        "status": item["status"],
        "status_label": recurring_status_label(item["status"]),
        "merchant": item["merchant"],
        "amount_label": recurring_signed_amount_label(item),
        "amount_class": "text-success" if item["type"] == "income" else "text-danger",
        "aria_label": recurring_calendar_chip_label(item),
    }


def recurring_signed_amount_label(item):
    """Build signed amount label."""
    sign = "+" if item["type"] == "income" else "-"
    amount = f"{item['amount']:,.2f}".replace(",", " ")
    return f"{sign}{amount} $"


def recurring_calendar_chip_label(item):
    """Build calendar chip label."""
    direction = gettext("income") if item["type"] == "income" else gettext("payment")
    amount = f"{item['amount']:,.2f}".replace(",", " ")
    return gettext(
        "{status} recurring {direction} {merchant}, typical amount {amount} dollars.",
        status=gettext(recurring_status_label(item["status"])),
        direction=direction,
        merchant=item["merchant"],
        amount=amount,
    )


def build_recurring_calendar_legend(recurring_items):
    """Build recurring calendar legend."""
    seen_statuses = {item["status"] for item in recurring_items}
    ordered_statuses = ["expected", "occurred", "likely_occurred", "amount_changed", "overdue", "possibly_inactive"]
    return [
        {
            "status": status,
            "label": recurring_status_label(status),
        }
        for status in ordered_statuses
        if status in seen_statuses
    ]


def recurring_status_label(status):
    """Build status label."""
    labels = {
        "occurred": "Occurred",
        "amount_changed": "Amount changed",
        "likely_occurred": "Likely occurred",
        "matched": "Likely occurred",
        "expected": "Expected",
        "overdue": "Overdue",
        "possibly_inactive": "Possibly inactive",
    }
    return labels.get(status, str(status or "").replace("_", " ").title())
