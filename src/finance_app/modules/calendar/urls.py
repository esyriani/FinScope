"""URL builders for the calendar feature."""

from collections.abc import Mapping
from datetime import date
from urllib.parse import urlencode

from flask import url_for

from finance_app.core.periods import PERIOD_CUSTOM
from finance_app.modules.transactions.constants import IGNORED_FILTER_ACTIVE


def calendar_url(month: date, params: Mapping[str, object]) -> str:
    """Build a calendar URL with blank query values removed."""
    cleaned: dict[str, object] = {"month": month.isoformat()[:7]}
    for key, value in params.items():
        if isinstance(value, (list, tuple)):
            values = [item for item in value if item]
            if values:
                cleaned[key] = values
        elif value:
            cleaned[key] = value
    return f"{url_for('calendar_page.calendar_view')}?{urlencode(cleaned, doseq=True)}"


def transactions_url(date_from: str, date_to: str, account_id: int | None = None) -> str:
    """Build a transactions URL for an inclusive date range."""
    params: dict[str, object] = {
        "period": PERIOD_CUSTOM,
        "date_from": date_from,
        "date_to": date_to,
        "ignored": IGNORED_FILTER_ACTIVE,
    }
    if account_id:
        params["account_id"] = account_id
    query = urlencode(params)
    return f"{url_for('transactions.transactions')}?{query}"
