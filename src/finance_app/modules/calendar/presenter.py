"""View-model builders for the calendar feature."""

from calendar import Calendar
from collections.abc import Iterable, Mapping
from datetime import date
from typing import Any

from .constants import HEATMAP_OPTIONS
from .urls import transactions_url


def build_calendar_days(
    month_start: date,
    transactions: Iterable[Mapping[str, Any]],
    account_id: int | None = None,
    merchant_search: str = "",
) -> list[dict[str, Any]]:
    """Build calendar days."""
    transaction_by_date: dict[str, list[Mapping[str, Any]]] = {}
    for transaction in transactions:
        transaction_by_date.setdefault(transaction["date"], []).append(transaction)

    days = []
    for day in Calendar(firstweekday=0).itermonthdates(month_start.year, month_start.month):
        day_key = day.isoformat()
        day_transactions = transaction_by_date.get(day_key, [])
        spending = sum(item["amount"] for item in day_transactions if item["type"] == "spending")
        income = sum(item["amount"] for item in day_transactions if item["type"] == "income")
        days.append(
            {
                "date": day_key,
                "day_number": day.day,
                "in_month": day.month == month_start.month,
                "is_today": day_key == date.today().isoformat(),
                "spending": round(spending, 2),
                "income": round(income, 2),
                "net": round(income - spending, 2),
                "transactions": day_transactions,
                "url": transactions_url(day_key, day_key, account_id=account_id, merchant_search=merchant_search),
            }
        )

    return days


def apply_heatmap(days: list[dict[str, Any]], metric: str) -> None:
    """Apply heatmap."""
    max_values = {
        option: max(
            (abs(heatmap_value(day, option)) if option == "net" else heatmap_value(day, option) for day in days),
            default=0,
        )
        for option in HEATMAP_OPTIONS
    }

    values = []
    for day in days:
        value = heatmap_value(day, metric)
        day["heatmap_value"] = value
        values.append(abs(value) if metric == "net" else value)
        day["heatmap"] = {}
        for option in HEATMAP_OPTIONS:
            option_value = heatmap_value(day, option)
            option_basis = abs(option_value) if option == "net" else option_value
            max_value = max_values[option]
            intensity = (option_basis / max_value) if max_value else 0
            day["heatmap"][option] = {
                "value": option_value,
                "alpha": round(min(0.42, 0.08 + (intensity * 0.34)), 3) if intensity else 0,
                "class": heatmap_class(option, option_value),
            }

    max_value = max(values, default=0)
    for day in days:
        intensity_basis = abs(day["heatmap_value"]) if metric == "net" else day["heatmap_value"]
        intensity = (intensity_basis / max_value) if max_value else 0
        day["heatmap_alpha"] = round(min(0.42, 0.08 + (intensity * 0.34)), 3) if intensity else 0
        day["heatmap_class"] = heatmap_class(metric, day["heatmap_value"])


def heatmap_class(metric: str, value: float) -> str:
    """Return class."""
    if metric == "income":
        return "calendar-heat-income"
    if metric == "net":
        return "calendar-heat-income" if value >= 0 else "calendar-heat-spending"
    return "calendar-heat-spending"


def heatmap_value(day: Mapping[str, Any], metric: str) -> float:
    """Return value."""
    if metric == "income":
        return day["income"]
    if metric == "net":
        return day["net"]
    return day["spending"]


def build_calendar_day_json(days: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Build calendar day json."""
    return {
        day["date"]: {
            "date": day["date"],
            "dayNumber": day["day_number"],
            "spending": day["spending"],
            "income": day["income"],
            "net": day["net"],
            "url": day["url"],
            "transactions": [
                {
                    "description": item["description"],
                    "amount": item["amount"],
                    "type": item["type"],
                    "category": item["category"],
                    "accountName": item["account_name"],
                    "url": item["url"],
                }
                for item in day["transactions"]
            ],
        }
        for day in days
    }
