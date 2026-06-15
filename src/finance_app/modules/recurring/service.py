"""Application orchestration for the recurring feature."""

from calendar import Calendar
from collections.abc import Iterable, Mapping
from datetime import date, datetime
from typing import Any
from urllib.parse import urlencode

from flask import url_for

from finance_app.core.i18n import format_month_year, gettext, weekday_abbreviation_labels
from finance_app.core.money import format_money_display
from finance_app.modules.accounts.filters import parse_account_id
from finance_app.modules.calendar.presenter import recurring_amount_change_cashflow_impact
from finance_app.modules.calendar.service import (
    build_recurring_activity_context,
    build_recurring_activity_json,
    clean_categories,
    clean_tags,
    default_month,
    parse_month,
    shift_month,
)
from finance_app.modules.merchants.filters import parse_merchant_id, parse_merchant_query

RECURRING_VIEWS = {"list", "calendar"}
RECURRING_CALENDAR_VISIBLE_COUNT = 3
RECURRING_CALENDAR_MERCHANT_LABEL_LENGTH = 23
RECURRING_STATUS_PRIORITY: dict[str, int] = {
    "overdue": 0,
    "amount_changed": 1,
    "expected": 2,
    "likely_occurred": 3,
    "matched": 3,
    "occurred": 4,
    "possibly_inactive": 5,
}
STATUS_OPTIONS: list[dict[str, str]] = [
    {"value": "occurred", "label": "Occurred"},
    {"value": "amount_changed", "label": "Amount changed"},
    {"value": "likely_occurred", "label": "Likely occurred"},
    {"value": "expected", "label": "Expected"},
    {"value": "overdue", "label": "Overdue"},
    {"value": "possibly_inactive", "label": "Possibly inactive"},
]
CONFIDENCE_OPTIONS: list[str] = ["High", "Medium", "Low"]
ALL_CONFIDENCE_FILTER_VALUE = "all"
DEFAULT_CONFIDENCE_FILTER = "High"


def build_recurring_page_context(args: Any) -> dict[str, Any]:
    """Build recurring page context."""
    selected_month = parse_month(args.get("month")) or default_month()
    selected_categories = clean_categories(args.getlist("categories"))
    selected_tags = clean_tags(args.getlist("tags"))
    selected_recurring_view = parse_recurring_view(args.get("view"))
    selected_statuses = clean_statuses(args.getlist("statuses"))
    selected_confidence = parse_confidence(args.get("confidence"))
    selected_account_id = parse_account_id(args.get("account_id"))
    selected_merchant_id = parse_merchant_id(args.get("merchant_id"))
    merchant_query = parse_merchant_query(args.get("merchant_query"))
    confidence_filter_applied = selected_confidence != DEFAULT_CONFIDENCE_FILTER
    has_account_filter = selected_account_id is not None
    has_merchant_filter = bool(selected_merchant_id or merchant_query)
    has_applied_filters = bool(
        selected_categories
        or selected_tags
        or selected_statuses
        or confidence_filter_applied
        or has_account_filter
        or has_merchant_filter
    )
    recurring_context = build_recurring_activity_context(
        selected_month,
        selected_categories,
        selected_tags,
        selected_account_id,
        selected_merchant_id,
        merchant_query,
    )
    filtered_items = filter_recurring_items(
        recurring_context["recurring_items"],
        selected_statuses,
        selected_confidence,
    )
    recurring_items = decorate_recurring_items_for_table(
        filtered_items,
        recurring_context["month_start"],
        recurring_context["month_end"],
    )

    return {
        "selected_month": recurring_context["month_start"].isoformat()[:7],
        "month_label": format_month_year(recurring_context["month_start"]),
        "selected_categories": selected_categories,
        "category_options": recurring_context["category_options"],
        "selected_tags": selected_tags,
        "tag_options": recurring_context["tag_options"],
        "selected_account_id": selected_account_id,
        "account_options": recurring_context["account_options"],
        "selected_merchant_id": selected_merchant_id,
        "merchant_query": merchant_query,
        "selected_merchant_label": recurring_context["selected_merchant_label"],
        "merchant_suggestion_limit": recurring_context["merchant_suggestion_limit"],
        "selected_recurring_view": selected_recurring_view,
        "selected_statuses": selected_statuses,
        "selected_confidence": selected_confidence,
        "selected_confidence_filter_value": confidence_filter_value(selected_confidence),
        "status_options": STATUS_OPTIONS,
        "confidence_options": CONFIDENCE_OPTIONS,
        "previous_month_url": recurring_filter_url(
            shift_month(recurring_context["month_start"], -1),
            selected_categories,
            selected_tags,
            selected_recurring_view,
            selected_statuses,
            selected_confidence,
            selected_account_id,
            selected_merchant_id,
            merchant_query,
        ),
        "next_month_url": recurring_filter_url(
            shift_month(recurring_context["month_start"], 1),
            selected_categories,
            selected_tags,
            selected_recurring_view,
            selected_statuses,
            selected_confidence,
            selected_account_id,
            selected_merchant_id,
            merchant_query,
        ),
        "current_month_url": recurring_filter_url(
            default_month(),
            selected_categories,
            selected_tags,
            selected_recurring_view,
            selected_statuses,
            selected_confidence,
            selected_account_id,
            selected_merchant_id,
            merchant_query,
        ),
        "list_view_url": recurring_view_url(
            recurring_context["month_start"],
            selected_categories,
            selected_tags,
            "list",
            selected_statuses,
            selected_confidence,
            selected_account_id,
            selected_merchant_id,
            merchant_query,
        ),
        "calendar_view_url": recurring_view_url(
            recurring_context["month_start"],
            selected_categories,
            selected_tags,
            "calendar",
            selected_statuses,
            selected_confidence,
            selected_account_id,
            selected_merchant_id,
            merchant_query,
        ),
        "recurring_status_filter_links": build_recurring_status_filter_links(
            recurring_context["month_start"],
            selected_categories,
            selected_tags,
            selected_recurring_view,
            selected_statuses,
            selected_confidence,
            selected_account_id,
            selected_merchant_id,
            merchant_query,
        ),
        "clear_filters_url": recurring_clear_url(
            selected_recurring_view,
            recurring_context["month_start"],
        ),
        "recurring_summary": build_recurring_summary(recurring_items),
        "recurring_items": recurring_items,
        "all_recurring_ids": [item["id"] for item in recurring_items],
        "table_page_size": recurring_context["table_page_size"],
        "recurring_empty_state_message": recurring_empty_state_message(
            has_applied_filters,
            has_account_filter=has_account_filter,
            has_merchant_filter=has_merchant_filter,
        ),
        "recurring_activity_json": build_recurring_activity_json(recurring_items),
        "recurring_calendar_days": build_recurring_calendar_days(
            recurring_context["month_start"],
            recurring_items,
        ),
        "recurring_calendar_legend": build_recurring_calendar_legend(recurring_items),
        "weekday_labels": weekday_abbreviation_labels(),
    }


def parse_recurring_view(value: object) -> str:
    """Parse recurring view."""
    value = str(value or "").strip().lower()
    return value if value in RECURRING_VIEWS else "list"


def clean_statuses(values: Iterable[object]) -> list[str]:
    """Clean statuses."""
    valid_statuses = {option["value"] for option in STATUS_OPTIONS}
    return [status for status in (str(value or "").strip() for value in values) if status in valid_statuses]


def parse_confidence(value: object) -> str:
    """Parse confidence."""
    if value is None:
        return DEFAULT_CONFIDENCE_FILTER

    value = str(value or "").strip()
    if not value or value.lower() == ALL_CONFIDENCE_FILTER_VALUE:
        return ""
    return value if value in CONFIDENCE_OPTIONS else DEFAULT_CONFIDENCE_FILTER


def confidence_filter_value(selected_confidence: str) -> str:
    """Return the query/form value for the selected confidence filter."""
    return selected_confidence or ALL_CONFIDENCE_FILTER_VALUE


def recurring_empty_state_message(
    has_applied_filters: bool,
    *,
    has_account_filter: bool = False,
    has_merchant_filter: bool = False,
) -> str:
    """Return the recurring empty-state message for the active filter context."""
    if has_account_filter and has_merchant_filter:
        return gettext("No recurring activity matches this account and merchant.")
    if has_account_filter:
        return gettext("No recurring activity matches this account.")
    if has_merchant_filter:
        return gettext("No recurring activity matches this merchant.")
    if has_applied_filters:
        return gettext("No recurring activity matches the current filters.")
    return gettext("No recurring activity detected for this month.")


def filter_recurring_items(
    recurring_items: Iterable[Mapping[str, Any]],
    selected_statuses: Iterable[str],
    selected_confidence: str,
) -> list[Mapping[str, Any]]:
    """Filter recurring items."""
    filtered = list(recurring_items)
    if selected_statuses:
        selected_status_set = set(selected_statuses)
        filtered = [item for item in filtered if item["status"] in selected_status_set]
    if selected_confidence:
        filtered = [item for item in filtered if item["confidence"] == selected_confidence]
    return filtered


def decorate_recurring_items_for_table(
    recurring_items: Iterable[Mapping[str, Any]],
    month_start: date,
    month_end: date,
) -> list[dict[str, Any]]:
    """Return recurring items with compact table display metadata attached."""
    evaluation_date = recurring_evaluation_date(month_start, month_end)
    return [
        {
            **item,
            "status_label": recurring_status_label(item["status"]),
            "status_detail": recurring_status_detail(item, evaluation_date),
        }
        for item in recurring_items
    ]


def recurring_evaluation_date(month_start: date, month_end: date) -> date:
    """Return the date used to explain expected or overdue recurring rows."""
    today = date.today()
    if today < month_start:
        return month_start
    if today > month_end:
        return month_end
    return today


def recurring_status_detail(item: Mapping[str, Any], evaluation_date: date) -> str:
    """Return a short user-facing explanation for a recurring row status."""
    status = item["status"]
    details = item.get("match_details") or {}
    if status == "occurred":
        return gettext("Date and amount matched.")
    if status == "amount_changed":
        return gettext("Date matched; amount changed.")
    if status in {"likely_occurred", "matched"}:
        return gettext("Merchant appeared outside date tolerance.")
    if status == "expected":
        return gettext("No matching transaction yet.")
    if status == "overdue":
        expected_date = parse_recurring_date(item.get("date"))
        days = max(0, (evaluation_date - expected_date).days) if expected_date else 0
        return gettext("1 day overdue") if days == 1 else gettext("{count} days overdue", count=days)
    if status == "possibly_inactive":
        missed_cycles = details.get("missed_cycles")
        if missed_cycles == 1:
            return gettext("Missed 1 expected cycle.")
        if missed_cycles:
            return gettext("Missed {count} expected cycles.", count=missed_cycles)
        return gettext("Missed multiple expected cycles.")
    return ""


def parse_recurring_date(value: object) -> date | None:
    """Return a parsed ISO date for recurring display helpers."""
    try:
        return datetime.strptime(str(value or ""), "%Y-%m-%d").date()
    except ValueError:
        return None


def build_recurring_summary(recurring_items: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Build recurring summary."""
    recurring_items = list(recurring_items)
    amount_changed_items = [item for item in recurring_items if item.get("amount_change")]
    low_confidence_detected_items = [
        item
        for item in recurring_items
        if (
            item["confidence"] == "Low"
            and item.get("user_status") == "detected"
            and item["status"] != "possibly_inactive"
        )
    ]
    active_statuses = {"occurred", "amount_changed", "likely_occurred", "expected", "overdue", "matched"}
    expected_statuses = {"expected"}
    occurred_statuses = {"occurred", "amount_changed", "likely_occurred", "matched"}
    needs_attention_items = {item["id"] for item in recurring_items if item["status"] in {"overdue", "amount_changed"}}
    needs_attention_items.update(item["id"] for item in low_confidence_detected_items)
    return {
        "needs_attention_count": len(needs_attention_items),
        "active_count": len(
            [item for item in recurring_items if item.get("active", 1) and item["status"] in active_statuses]
        ),
        "expected_count": len([item for item in recurring_items if item["status"] in expected_statuses]),
        "occurred_count": len([item for item in recurring_items if item["status"] in occurred_statuses]),
        "overdue_count": len([item for item in recurring_items if item["status"] == "overdue"]),
        "possibly_inactive_count": len([item for item in recurring_items if item["status"] == "possibly_inactive"]),
        "low_confidence_detected_count": len(low_confidence_detected_items),
        "recurring_spending": round(
            sum(
                item["amount"]
                for item in recurring_items
                if item["type"] == "spending" and item["status"] != "possibly_inactive"
            ),
            2,
        ),
        "recurring_income": round(
            sum(
                item["amount"]
                for item in recurring_items
                if item["type"] == "income" and item["status"] != "possibly_inactive"
            ),
            2,
        ),
        "amount_change_count": len(amount_changed_items),
        "amount_change_total_impact": round(
            sum(recurring_amount_change_cashflow_impact(item) for item in amount_changed_items),
            2,
        ),
    }


def recurring_view_url(
    month_start: date,
    selected_categories: Iterable[str],
    selected_tags: Iterable[str],
    view: str,
    selected_statuses: Iterable[str] | None = None,
    selected_confidence: str = "",
    selected_account_id: int | None = None,
    selected_merchant_id: int | None = None,
    merchant_query: str = "",
) -> str:
    """Build view URL."""
    return recurring_filter_url(
        month_start,
        selected_categories,
        selected_tags,
        view,
        selected_statuses,
        selected_confidence,
        selected_account_id,
        selected_merchant_id,
        merchant_query,
    )


def recurring_filter_url(
    month_start: date,
    selected_categories: Iterable[str],
    selected_tags: Iterable[str],
    view: str,
    selected_statuses: Iterable[str] | None = None,
    selected_confidence: str = "",
    selected_account_id: int | None = None,
    selected_merchant_id: int | None = None,
    merchant_query: str = "",
) -> str:
    """Build a recurring URL while preserving filter state."""
    params: dict[str, object] = {
        "month": month_start.isoformat()[:7],
        "view": view,
    }
    if selected_categories:
        params["categories"] = selected_categories
    if selected_tags:
        params["tags"] = selected_tags
    if selected_statuses:
        params["statuses"] = selected_statuses
    if selected_confidence == "":
        params["confidence"] = ALL_CONFIDENCE_FILTER_VALUE
    elif selected_confidence != DEFAULT_CONFIDENCE_FILTER:
        params["confidence"] = selected_confidence
    if selected_account_id:
        params["account_id"] = selected_account_id
    if selected_merchant_id:
        params["merchant_id"] = selected_merchant_id
    if merchant_query:
        params["merchant_query"] = merchant_query

    return f"{url_for('recurring.recurring')}?{urlencode(params, doseq=True)}"


def recurring_clear_url(view: str, month_start: date | None = None) -> str:
    """Build a recurring filter-clear URL while preserving the viewed period."""
    params: dict[str, object] = {"view": view}
    if month_start:
        params["month"] = month_start.isoformat()[:7]
    return f"{url_for('recurring.recurring')}?{urlencode(params)}"


def build_recurring_status_filter_links(
    month_start: date,
    selected_categories: Iterable[str],
    selected_tags: Iterable[str],
    view: str,
    selected_statuses: list[str],
    selected_confidence: str,
    selected_account_id: int | None = None,
    selected_merchant_id: int | None = None,
    merchant_query: str = "",
) -> list[dict[str, Any]]:
    """Build status filter links that keep recurring summaries and views aligned."""
    return [
        {
            "value": "",
            "label": "All",
            "url": recurring_filter_url(
                month_start,
                selected_categories,
                selected_tags,
                view,
                selected_statuses=[],
                selected_confidence=selected_confidence,
                selected_account_id=selected_account_id,
                selected_merchant_id=selected_merchant_id,
                merchant_query=merchant_query,
            ),
            "selected": not selected_statuses,
        },
        *[
            {
                "value": option["value"],
                "label": option["label"],
                "url": recurring_filter_url(
                    month_start,
                    selected_categories,
                    selected_tags,
                    view,
                    selected_statuses=[option["value"]],
                    selected_confidence=selected_confidence,
                    selected_account_id=selected_account_id,
                    selected_merchant_id=selected_merchant_id,
                    merchant_query=merchant_query,
                ),
                "selected": selected_statuses == [option["value"]],
            }
            for option in STATUS_OPTIONS
        ],
    ]


def build_recurring_calendar_days(
    month_start: date,
    recurring_items: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Build recurring calendar days."""
    items_by_date: dict[str, list[dict[str, Any]]] = {}
    for item in recurring_items:
        placement_date = recurring_calendar_item_date(item)
        items_by_date.setdefault(placement_date, []).append(recurring_calendar_chip(item))

    days = []
    for day in Calendar(firstweekday=0).itermonthdates(month_start.year, month_start.month):
        day_key = day.isoformat()
        day_items = sorted(
            items_by_date.get(day_key, []),
            key=recurring_calendar_sort_key,
        )
        visible_items = day_items[:RECURRING_CALENDAR_VISIBLE_COUNT]
        days.append(
            {
                "date": day_key,
                "day_number": day.day,
                "in_month": day.month == month_start.month,
                "is_today": day_key == date.today().isoformat(),
                "item_count": len(day_items),
                "attention_count": sum(1 for chip in day_items if chip["needs_attention"]),
                "recurring_items": visible_items,
                "all_recurring_items": day_items,
                "more_count": max(0, len(day_items) - len(visible_items)),
            }
        )

    return days


def recurring_calendar_item_date(item: Mapping[str, Any]) -> str:
    """Build calendar item date."""
    match_details = item.get("match_details") or {}
    matched_date = match_details.get("matched_date")
    if item["status"] in {"occurred", "likely_occurred", "amount_changed", "matched"} and matched_date:
        return matched_date
    return item["date"]


def recurring_calendar_chip(item: Mapping[str, Any]) -> dict[str, Any]:
    """Build calendar chip."""
    merchant = item["merchant"]
    return {
        "id": item["id"],
        "pattern_key": item["pattern_key"],
        "status": item["status"],
        "status_label": item.get("status_label") or recurring_status_label(item["status"]),
        "status_detail": item.get("status_detail", ""),
        "merchant": merchant,
        "merchant_label": recurring_calendar_merchant_label(merchant),
        "category": item["category"],
        "frequency": item["frequency"],
        "user_status": item["user_status"],
        "active": item["active"],
        "amount_label": recurring_signed_amount_label(item),
        "amount_class": "text-success" if item["type"] == "income" else "text-danger",
        "needs_attention": recurring_calendar_needs_attention(item),
        "aria_label": recurring_calendar_chip_label(item),
    }


def recurring_calendar_merchant_label(merchant: object) -> str:
    """Return a compact merchant label for constrained calendar chips."""
    merchant_text = str(merchant or "")
    label_limit = RECURRING_CALENDAR_MERCHANT_LABEL_LENGTH
    if len(merchant_text) <= label_limit:
        return merchant_text

    return f"{merchant_text[: label_limit - 3]}..."


def recurring_calendar_sort_key(chip: Mapping[str, Any]) -> tuple[int, str]:
    """Return a priority key for recurring calendar chips."""
    return (
        RECURRING_STATUS_PRIORITY.get(chip["status"], 99),
        str(chip["merchant"]).lower(),
    )


def recurring_calendar_needs_attention(item: Mapping[str, Any]) -> bool:
    """Return whether a recurring item should be called out in calendar summaries."""
    if item["status"] in {"overdue", "amount_changed"}:
        return True
    return (
        item.get("confidence") == "Low"
        and item.get("user_status") == "detected"
        and item["status"] != "possibly_inactive"
    )


def recurring_signed_amount_label(item: Mapping[str, Any]) -> str:
    """Build signed amount label."""
    sign = "+" if item["type"] == "income" else "-"
    return f"{sign}{format_money_display(item['amount'])}"


def recurring_calendar_chip_label(item: Mapping[str, Any]) -> str:
    """Build calendar chip label."""
    direction = gettext("income") if item["type"] == "income" else gettext("payment")
    return gettext(
        "{status} recurring {direction} {merchant}, typical amount {amount}.",
        status=gettext(recurring_status_label(item["status"])),
        direction=direction,
        merchant=item["merchant"],
        amount=format_money_display(item["amount"]),
    )


def build_recurring_calendar_legend(recurring_items: Iterable[Mapping[str, Any]]) -> list[dict[str, str]]:
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


def recurring_status_label(status: object) -> str:
    """Build status label."""
    status_text = str(status or "")
    labels = {
        "occurred": "Occurred",
        "amount_changed": "Amount changed",
        "likely_occurred": "Likely occurred",
        "matched": "Likely occurred",
        "expected": "Expected",
        "overdue": "Overdue",
        "possibly_inactive": "Possibly inactive",
    }
    return labels.get(status_text, status_text.replace("_", " ").title())
