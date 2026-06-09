"""Helpers for recurring."""

from collections.abc import Mapping
from typing import Any

from sqlalchemy import func, insert, select, update

from finance_app.core.constants import (
    RECURRING_USER_STATUS_DETECTED,
    RECURRING_USER_STATUSES,
)
from finance_app.core.money import rounded_money_float
from finance_app.database.tables import recurring_patterns as recurring_patterns_table
from finance_app.modules.merchants.repository import (
    find_merchant_by_id,
    get_or_create_merchant_for_name,
    merchant_identity_key,
)

VALID_RECURRING_USER_STATUSES = set(RECURRING_USER_STATUSES)
VALID_RECURRING_FREQUENCIES = {
    "Weekly",
    "Biweekly",
    "Monthly-like",
    "Quarterly",
    "Annual",
    "Irregular recurring",
}


def recurring_pattern_select() -> Any:
    """Return the shared recurring pattern column projection."""
    return select(
        recurring_patterns_table.c.pattern_key,
        recurring_patterns_table.c.merchant_id,
        recurring_patterns_table.c.merchant,
        recurring_patterns_table.c.type,
        recurring_patterns_table.c.user_status,
        recurring_patterns_table.c.frequency,
        recurring_patterns_table.c.expected_day,
        recurring_patterns_table.c.typical_amount,
        recurring_patterns_table.c.date_tolerance_days,
        recurring_patterns_table.c.amount_tolerance,
        recurring_patterns_table.c.active,
        recurring_patterns_table.c.created_at,
        recurring_patterns_table.c.updated_at,
    )


def recurring_pattern_key(merchant: object, tx_type: object) -> str:
    """Build pattern key."""
    return f"{str(merchant or '').strip()}::{str(tx_type or '').strip()}"


def get_recurring_pattern_metadata(conn: Any) -> dict[str, dict[str, Any]]:
    """Return recurring pattern metadata."""
    rows = conn.execute(recurring_pattern_select()).mappings().fetchall()
    metadata: dict[str, dict[str, Any]] = {}
    for row in rows:
        pattern = recurring_pattern_from_row(row)
        metadata[row["pattern_key"]] = pattern
        if row["merchant_id"]:
            metadata[recurring_pattern_key(merchant_identity_key(row["merchant_id"]), row["type"])] = pattern
    return metadata


def get_recurring_pattern(conn: Any, pattern_key: object) -> dict[str, Any] | None:
    """Return recurring pattern."""
    row = (
        conn.execute(recurring_pattern_select().where(recurring_patterns_table.c.pattern_key == pattern_key))
        .mappings()
        .fetchone()
    )
    if not row:
        return None
    return recurring_pattern_from_row(row)


def get_recurring_pattern_by_merchant_type(conn: Any, merchant_id: object, tx_type: object) -> dict[str, Any] | None:
    """Return recurring pattern by durable merchant and cash-flow type."""
    row = (
        conn.execute(
            recurring_pattern_select().where(
                recurring_patterns_table.c.merchant_id == merchant_id,
                recurring_patterns_table.c.type == tx_type,
            )
        )
        .mappings()
        .fetchone()
    )
    if not row:
        return None
    return recurring_pattern_from_row(row)


def recurring_pattern_from_row(row: Mapping[str, Any]) -> dict[str, Any]:
    """Return a recurring pattern mapping with presentation-compatible amounts."""
    pattern = dict(row)
    pattern["match_type"] = "merchant" if row["merchant_id"] else "keyword"
    if pattern["typical_amount"] is not None:
        pattern["typical_amount"] = rounded_money_float(pattern["typical_amount"])
    if pattern["amount_tolerance"] is not None:
        pattern["amount_tolerance"] = rounded_money_float(pattern["amount_tolerance"])
    return pattern


def upsert_recurring_pattern(
    conn: Any,
    pattern_key: object,
    merchant: object,
    tx_type: object,
    merchant_id: object | None = None,
    **values: Any,
) -> None:
    """Insert or update recurring pattern."""
    identity = recurring_pattern_identity(conn, pattern_key, merchant, tx_type, merchant_id)
    current: dict[str, Any] = (
        (
            get_recurring_pattern_by_merchant_type(conn, identity["merchant_id"], tx_type)
            if identity["merchant_id"]
            else None
        )
        or get_recurring_pattern(conn, identity["pattern_key"])
        or get_recurring_pattern(conn, pattern_key)
        or {}
    )
    stored_pattern_key = current.get("pattern_key") or identity["pattern_key"]
    user_status = normalize_user_status(
        values.get("user_status", current.get("user_status", RECURRING_USER_STATUS_DETECTED))
    )
    active = normalize_active(values.get("active", current.get("active", 1)))
    frequency = normalize_frequency(values.get("frequency", current.get("frequency")))
    expected_day = normalize_optional_int(
        values.get("expected_day", current.get("expected_day")),
        minimum=1,
        maximum=31,
    )
    typical_amount = normalize_optional_float(
        values.get("typical_amount", current.get("typical_amount")),
        minimum=0,
    )
    date_tolerance_days = normalize_optional_int(
        values.get("date_tolerance_days", current.get("date_tolerance_days")),
        minimum=0,
    )
    amount_tolerance = normalize_optional_float(
        values.get("amount_tolerance", current.get("amount_tolerance")),
        minimum=0,
    )

    if current:
        conn.execute(
            update(recurring_patterns_table)
            .where(recurring_patterns_table.c.pattern_key == stored_pattern_key)
            .values(
                pattern_key=identity["pattern_key"],
                merchant_id=identity["merchant_id"],
                merchant=identity["merchant"],
                type=str(tx_type or "").strip(),
                user_status=user_status,
                frequency=frequency,
                expected_day=expected_day,
                typical_amount=typical_amount,
                date_tolerance_days=date_tolerance_days,
                amount_tolerance=amount_tolerance,
                active=active,
                updated_at=func.current_timestamp(),
            )
        )
        return

    row_values = {
        "pattern_key": identity["pattern_key"],
        "merchant_id": identity["merchant_id"],
        "merchant": identity["merchant"],
        "type": str(tx_type or "").strip(),
        "user_status": user_status,
        "frequency": frequency,
        "expected_day": expected_day,
        "typical_amount": typical_amount,
        "date_tolerance_days": date_tolerance_days,
        "amount_tolerance": amount_tolerance,
        "active": active,
    }
    conn.execute(
        insert(recurring_patterns_table).values(
            **row_values,
            created_at=func.current_timestamp(),
            updated_at=func.current_timestamp(),
        )
    )


def recurring_pattern_identity(
    conn: Any,
    pattern_key: object,
    merchant: object,
    tx_type: object,
    merchant_id: object | None = None,
) -> dict[str, Any]:
    """Resolve a recurring pattern identity to a durable merchant when possible."""
    merchant_row = find_merchant_by_id(conn, merchant_id)
    if merchant_id and merchant_row is None:
        merchant_row = get_or_create_merchant_for_name(conn, merchant)

    if merchant_row:
        return {
            "merchant_id": merchant_row["id"],
            "merchant": merchant_row["merchant_key"],
            "pattern_key": recurring_pattern_key(merchant_identity_key(merchant_row["id"]), tx_type),
        }

    merchant_name = str(merchant or "").strip()
    return {
        "merchant_id": None,
        "merchant": merchant_name,
        "pattern_key": recurring_pattern_key(merchant_name or pattern_key, tx_type),
    }


def normalize_user_status(value: object) -> str:
    """Normalize user status."""
    text = str(value or RECURRING_USER_STATUS_DETECTED).strip().lower()
    return text if text in VALID_RECURRING_USER_STATUSES else RECURRING_USER_STATUS_DETECTED


def normalize_frequency(value: object) -> str | None:
    """Normalize frequency."""
    text = str(value or "").strip()
    return text if text in VALID_RECURRING_FREQUENCIES else None


def normalize_active(value: object) -> int:
    """Normalize active."""
    if isinstance(value, str):
        return 0 if value.strip().lower() in {"0", "false", "inactive", "no"} else 1
    return 1 if value else 0


def normalize_optional_int(value: object, minimum: int | None = None, maximum: int | None = None) -> int | None:
    """Normalize optional int."""
    if value in (None, ""):
        return None
    try:
        parsed = value if isinstance(value, int) else int(str(value))
    except (TypeError, ValueError):
        return None
    if minimum is not None and parsed < minimum:
        return None
    if maximum is not None and parsed > maximum:
        return None
    return parsed


def normalize_optional_float(value: object, minimum: float | None = None) -> float | None:
    """Normalize optional float."""
    if value in (None, ""):
        return None
    try:
        parsed = float(str(value))
    except (TypeError, ValueError):
        return None
    if minimum is not None and parsed < minimum:
        return None
    return round(parsed, 2)
