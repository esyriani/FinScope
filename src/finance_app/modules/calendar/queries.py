"""SQLAlchemy Core query helpers for the calendar feature."""

from collections.abc import Iterable, Sequence
from datetime import date
from typing import Any

from sqlalchemy import func, select

from finance_app.core.constants import NON_REPORTABLE_TRANSACTION_KINDS
from finance_app.core.periods import shift_months
from finance_app.database.tables import (
    accounts as accounts_table,
)
from finance_app.database.tables import (
    merchants as merchants_table,
)
from finance_app.database.tables import (
    transactions as transactions_table,
)
from finance_app.modules.categories.tag_filters import transaction_tag_condition
from finance_app.modules.recurring.settings import RECURRENCE_DETECTION_DEFAULTS, RecurrenceDetectionSettings
from finance_app.modules.settings.runtime import get_float_setting, get_int_setting


def get_recurrence_detection_settings(conn: Any) -> RecurrenceDetectionSettings:
    """Return recurrence detection settings."""
    defaults = RECURRENCE_DETECTION_DEFAULTS
    return RecurrenceDetectionSettings(
        minimum_occurrences=get_int_setting(
            conn,
            "recurrence_minimum_occurrences",
            defaults.minimum_occurrences,
        ),
        date_tolerance_days=get_int_setting(
            conn,
            "recurrence_date_tolerance_days",
            defaults.date_tolerance_days,
        ),
        amount_tolerance_absolute=get_float_setting(
            conn,
            "recurrence_amount_tolerance_absolute",
            defaults.amount_tolerance_absolute,
            minimum=0,
        ),
        amount_tolerance_percent=get_float_setting(
            conn,
            "recurrence_amount_tolerance_percent",
            defaults.amount_tolerance_percent,
            minimum=0,
            maximum=1,
        ),
        missed_cycles_before_inactive=get_int_setting(
            conn,
            "recurrence_missed_cycles_before_inactive",
            defaults.missed_cycles_before_inactive,
        ),
    )


def build_category_filter(
    selected_categories: Sequence[str],
    selected_tags: Sequence[str],
    unknown_category: str,
) -> tuple[Any, ...]:
    """Build Core category and tag filters for calendar transaction queries."""
    conditions: list[Any] = []
    if selected_categories:
        conditions.append(func.coalesce(transactions_table.c.category, unknown_category).in_(selected_categories))

    tag_condition = transaction_tag_condition(selected_tags)
    if tag_condition is not None:
        conditions.append(tag_condition)

    return tuple(conditions)


def non_transfer_clause() -> Any:
    """Return the calendar Core filter for reportable transaction kinds."""
    return transactions_table.c.transaction_kind.not_in(NON_REPORTABLE_TRANSACTION_KINDS)


def transaction_row_select(unknown_category: str) -> Any:
    """Return the shared calendar transaction projection."""
    return select(
        transactions_table.c.tx_date,
        transactions_table.c.description,
        transactions_table.c.merchant_id,
        merchants_table.c.merchant_key.label("merchant_name"),
        merchants_table.c.merchant_key.label("merchant_key"),
        transactions_table.c.amount,
        transactions_table.c.transaction_kind,
        func.coalesce(transactions_table.c.category, unknown_category).label("category"),
        func.coalesce(accounts_table.c.name, "Personal").label("account_name"),
    ).select_from(
        transactions_table.outerjoin(
            accounts_table,
            accounts_table.c.id == transactions_table.c.account_id,
        ).outerjoin(
            merchants_table,
            merchants_table.c.id == transactions_table.c.merchant_id,
        )
    )


def fetch_month_transactions(
    conn: Any,
    month_start: date,
    month_end: date,
    unknown_category: str,
    category_filter: Iterable[Any],
) -> Any:
    """Fetch month transactions."""
    return (
        conn.execute(
            transaction_row_select(unknown_category)
            .where(
                transactions_table.c.ignored == 0,
                non_transfer_clause(),
                transactions_table.c.tx_date >= month_start,
                transactions_table.c.tx_date <= month_end,
                *category_filter,
            )
            .order_by(transactions_table.c.tx_date, transactions_table.c.amount.desc())
        )
        .mappings()
        .fetchall()
    )


def fetch_recurring_source_rows(
    conn: Any,
    month_start: date,
    unknown_category: str,
    category_filter: Iterable[Any],
) -> Any:
    """Fetch historical rows used to infer recurring activity."""
    recurring_start = shift_months(month_start, -18)
    return (
        conn.execute(
            transaction_row_select(unknown_category)
            .where(
                transactions_table.c.ignored == 0,
                non_transfer_clause(),
                transactions_table.c.tx_date >= recurring_start,
                transactions_table.c.tx_date < month_start,
                *category_filter,
            )
            .order_by(transactions_table.c.tx_date)
        )
        .mappings()
        .fetchall()
    )
