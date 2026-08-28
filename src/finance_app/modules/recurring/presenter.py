"""View-model builders for recurring activity read models.

Builds transaction candidates, summary metrics, and JSON-safe recurring payloads
shared by the recurring page and calendar page.
"""

from collections.abc import Iterable, Mapping
from typing import Any

from finance_app.core.money import money_to_float, rounded_money_float
from finance_app.modules.merchants.repository import merchant_identity_from_row
from finance_app.modules.transactions.urls import transactions_date_range_url

from .constants import UNMATCHED_RECURRING_STATUSES


def build_recurring_transaction_rows(
    rows: Iterable[Mapping[str, Any]],
    conn: Any = None,
    account_id: int | None = None,
    merchant_search: str = "",
) -> list[dict[str, Any]]:
    """Build normalized transaction rows used by recurring activity matching."""
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
                "url": transactions_date_range_url(
                    row["tx_date"],
                    row["tx_date"],
                    account_id=account_id,
                    merchant_search=merchant_search,
                ),
            }
        )
    return transactions


def build_recurring_activity_summary(
    transactions: Iterable[Mapping[str, Any]],
    recurring_items: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """Build posted and expected cash-flow summary metrics for recurring activity."""
    transactions = list(transactions)
    recurring_items = list(recurring_items)
    spending = sum(item["amount"] for item in transactions if item["type"] == "spending")
    income = sum(item["amount"] for item in transactions if item["type"] == "income")
    recurring_spending = sum(item["amount"] for item in recurring_items if item["type"] == "spending")
    recurring_income = sum(item["amount"] for item in recurring_items if item["type"] == "income")
    recurring_occurred_count = len(
        [
            item
            for item in recurring_items
            if item["status"] in {"occurred", "amount_changed", "likely_occurred", "matched"}
        ]
    )
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


def recurring_amount_change_cashflow_impact(item: Mapping[str, Any]) -> float:
    """Return the signed cash-flow impact of a recurring amount change."""
    difference = item["amount_change"]["difference"]
    return difference if item["type"] == "income" else -difference


def build_recurring_activity_json(recurring_items: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Build JSON-safe recurring activity payloads keyed by item id."""
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
