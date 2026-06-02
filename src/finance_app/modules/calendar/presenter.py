"""View-model builders for the calendar feature."""

from calendar import Calendar
from datetime import date

from finance_app.core.money import money_to_float, rounded_money_float
from finance_app.modules.merchants.repository import merchant_identity_from_row
from .urls import transactions_url
from .constants import HEATMAP_OPTIONS, UNMATCHED_RECURRING_STATUSES


def build_calendar_transactions(rows, conn=None):
    """Build calendar transactions."""
    transactions = []
    for row in rows:
        amount = money_to_float(row["amount"])
        transaction_kind = row["transaction_kind"] or ""
        if transaction_kind == "expense" and amount > 0:
            tx_type = "spending"
        elif transaction_kind == "income" and amount < 0:
            tx_type = "income"
        else:
            tx_type = "neutral"
        merchant = merchant_identity_from_row(row, conn=conn)
        transactions.append(
            {
                "date": row["tx_date"],
                "description": row["description"],
                "merchant_id": merchant["id"],
                "merchant_key": merchant["key"],
                "merchant_name": merchant["name"],
                "amount": rounded_money_float(abs(amount)),
                "signed_amount": rounded_money_float(amount),
                "type": tx_type,
                "category": row["category"],
                "account_name": row["account_name"],
                "url": transactions_url(row["tx_date"], row["tx_date"]),
            }
        )
    return transactions


def build_calendar_days(month_start, transactions):
    """Build calendar days."""
    transaction_by_date = {}
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
                "url": transactions_url(day_key, day_key),
            }
        )

    return days


def apply_heatmap(days, metric):
    """Apply heatmap."""
    max_values = {
        option: max(
            (
                abs(heatmap_value(day, option)) if option == "net" else heatmap_value(day, option)
                for day in days
            ),
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


def heatmap_class(metric, value):
    """Return class."""
    if metric == "income":
        return "calendar-heat-income"
    if metric == "net":
        return "calendar-heat-income" if value >= 0 else "calendar-heat-spending"
    return "calendar-heat-spending"


def heatmap_value(day, metric):
    """Return value."""
    if metric == "income":
        return day["income"]
    if metric == "net":
        return day["net"]
    return day["spending"]


def build_calendar_summary(transactions, recurring_items):
    """Build calendar summary."""
    spending = sum(item["amount"] for item in transactions if item["type"] == "spending")
    income = sum(item["amount"] for item in transactions if item["type"] == "income")
    recurring_spending = sum(item["amount"] for item in recurring_items if item["type"] == "spending")
    recurring_income = sum(item["amount"] for item in recurring_items if item["type"] == "income")
    recurring_occurred_count = len([
        item
        for item in recurring_items
        if item["status"] in {"occurred", "amount_changed", "likely_occurred", "matched"}
    ])
    recurring_expected_count = len([item for item in recurring_items if item["status"] == "expected"])
    recurring_overdue_count = len([item for item in recurring_items if item["status"] == "overdue"])
    recurring_possibly_inactive_count = len([item for item in recurring_items if item["status"] == "possibly_inactive"])
    amount_changed_items = [item for item in recurring_items if item.get("amount_change")]
    amount_change_total_impact = sum(recurring_amount_change_cashflow_impact(item) for item in amount_changed_items)
    expected_spending = sum(
        item["amount"]
        for item in recurring_items
        if item["type"] == "spending" and item["status"] in UNMATCHED_RECURRING_STATUSES
    )
    expected_income = sum(
        item["amount"]
        for item in recurring_items
        if item["type"] == "income" and item["status"] in UNMATCHED_RECURRING_STATUSES
    )
    return {
        "spending": round(spending, 2),
        "income": round(income, 2),
        "net": round(income - spending, 2),
        "transaction_count": len(transactions),
        "expected_spending": round(expected_spending, 2),
        "expected_income": round(expected_income, 2),
        "expected_count": recurring_expected_count + recurring_overdue_count,
        "recurring_count": len(recurring_items),
        "recurring_occurred_count": recurring_occurred_count,
        "recurring_expected_count": recurring_expected_count,
        "recurring_overdue_count": recurring_overdue_count,
        "recurring_possibly_inactive_count": recurring_possibly_inactive_count,
        "recurring_spending": round(recurring_spending, 2),
        "recurring_income": round(recurring_income, 2),
        "recurring_amount_change_count": len(amount_changed_items),
        "recurring_amount_change_total_impact": round(amount_change_total_impact, 2),
    }


def recurring_amount_change_cashflow_impact(item):
    """Build amount change cashflow impact."""
    difference = item["amount_change"]["difference"]
    return difference if item["type"] == "income" else -difference


def build_calendar_day_json(days):
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


def build_recurring_activity_json(recurring_items):
    """Build recurring activity json."""
    return {
        item["id"]: {
            "id": item["id"],
            "patternKey": item["pattern_key"],
            "merchantId": item["merchant_id"],
            "matchType": item["match_type"],
            "merchant": item["merchant"],
            "category": item["category"],
            "type": item["type"],
            "frequency": item["frequency"],
            "amount": item["amount"],
            "date": item["date"],
            "lastSeen": item["last_seen"],
            "observedMonths": item["observed_months"],
            "status": item["status"],
            "statusLabel": item.get("status_label", ""),
            "statusDetail": item.get("status_detail", ""),
            "confidence": item["confidence"],
            "userStatus": item["user_status"],
            "active": item["active"],
            "amountChange": item["amount_change"],
            "matchDetails": item["match_details"],
            "occurrences": item["occurrences"],
        }
        for item in recurring_items
    }
