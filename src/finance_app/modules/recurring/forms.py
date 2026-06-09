"""Form parsing and validation helpers for the recurring feature."""

from collections.abc import Mapping
from datetime import date
from typing import Any

from finance_app.modules.recurring.patterns import recurring_pattern_key


def recurring_pattern_payload(payload: Mapping[str, Any]) -> dict[str, object]:
    """Build pattern payload."""
    merchant = str(payload.get("merchant") or "").strip()
    tx_type = str(payload.get("type") or "").strip()
    merchant_id = parse_optional_positive_int(payload.get("merchantId"))
    match_type = parse_match_type(payload.get("matchType"), merchant_id)
    if not merchant or tx_type not in {"spending", "income"}:
        raise ValueError("Recurring pattern payload is incomplete.")
    merchant_id = merchant_id if match_type == "merchant" else None
    merchant_key = f"merchant:{merchant_id}" if merchant_id else merchant
    return {
        "pattern_key": recurring_pattern_key(merchant_key, tx_type),
        "merchant_id": merchant_id,
        "merchant": merchant,
        "match_type": match_type,
        "type": tx_type,
    }


def parse_expected_day(value: object) -> int | None:
    """Parse expected day."""
    text = str(value or "").strip()
    if not text:
        return None
    if "-" in text:
        try:
            return date.fromisoformat(text).day
        except ValueError:
            return None
    try:
        day = int(text)
    except ValueError:
        return None
    return day if 1 <= day <= 31 else None


def parse_optional_positive_int(value: object) -> int | None:
    """Parse an optional positive integer."""
    if value in (None, ""):
        return None
    try:
        parsed = int(str(value))
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def parse_match_type(value: object, merchant_id: int | None = None) -> str:
    """Return the recurring pattern match mode for a payload."""
    text = str(value or "").strip().lower()
    if text in {"merchant", "keyword"}:
        return text
    return "merchant" if merchant_id else "keyword"
