"""Tests for recurring pattern routes and persistence helpers."""

import pytest

from finance_app.core.csrf import CSRF_HEADER_NAME, CSRF_SESSION_KEY
from finance_app.modules.recurring.forms import parse_expected_day, recurring_pattern_payload
from finance_app.modules.recurring.patterns import (
    get_recurring_pattern,
    get_recurring_pattern_by_merchant_type,
    get_recurring_pattern_metadata,
    normalize_active,
    normalize_frequency,
    normalize_optional_float,
    normalize_optional_int,
    normalize_user_status,
    recurring_pattern_key,
    upsert_recurring_pattern,
)
from finance_app.modules.merchants.repository import get_or_create_merchant_for_name


def set_csrf_token(client, token="test-csrf-token"):
    """Store a CSRF token in the test client's session."""
    with client.session_transaction() as session:
        session[CSRF_SESSION_KEY] = token
    return token


def recurring_json(client, path, payload):
    """POST a JSON recurring-pattern payload with CSRF protection."""
    token = set_csrf_token(client)
    return client.post(
        path,
        json=payload,
        headers={CSRF_HEADER_NAME: token},
    )


def valid_payload(**overrides):
    """Build a valid recurring route payload."""
    payload = {
        "patternKey": "NETFLIX::spending",
        "merchant": "NETFLIX",
        "type": "spending",
    }
    payload.update(overrides)
    return payload


def test_recurring_confirm_ignore_and_edit_routes_persist_metadata(client, db_conn):
    """Verify recurring pattern mutation routes persist user metadata."""
    merchant_id = get_or_create_merchant_for_name(db_conn, "NETFLIX")["id"]
    db_conn.commit()
    payload = valid_payload(merchantId=merchant_id, matchType="merchant")

    confirm = recurring_json(client, "/recurring/patterns/confirm", payload)
    confirmed = get_recurring_pattern_by_merchant_type(db_conn, merchant_id, "spending")

    ignore = recurring_json(client, "/recurring/patterns/ignore", payload)
    ignored = get_recurring_pattern_by_merchant_type(db_conn, merchant_id, "spending")

    edit = recurring_json(
        client,
        "/recurring/patterns/edit",
        valid_payload(
            merchantId=merchant_id,
            matchType="merchant",
            frequency="Monthly-like",
            expectedDate="2026-05-14",
            typicalAmount="18.99",
            dateToleranceDays="3",
            amountTolerance="2.50",
            active="0",
        ),
    )
    edited = get_recurring_pattern_by_merchant_type(db_conn, merchant_id, "spending")

    assert confirm.status_code == 200
    assert confirm.get_json() == {"ok": True, "userStatus": "confirmed", "active": 1}
    assert confirmed["user_status"] == "confirmed"
    assert confirmed["merchant_id"] == merchant_id
    assert confirmed["match_type"] == "merchant"
    assert confirmed["active"] == 1

    assert ignore.status_code == 200
    assert ignore.get_json() == {"ok": True, "userStatus": "ignored", "active": 0}
    assert ignored["user_status"] == "ignored"
    assert ignored["active"] == 0

    assert edit.status_code == 200
    assert edit.get_json() == {"ok": True, "userStatus": "edited", "active": 0}
    assert edited["user_status"] == "edited"
    assert edited["frequency"] == "Monthly-like"
    assert edited["expected_day"] == 14
    assert edited["typical_amount"] == 18.99
    assert edited["date_tolerance_days"] == 3
    assert edited["amount_tolerance"] == 2.50
    assert edited["active"] == 0


def test_recurring_routes_preserve_keyword_fuzzy_patterns(client, db_conn):
    """Verify recurring routes keep keyword-fuzzy patterns unbound."""
    confirm = recurring_json(
        client,
        "/recurring/patterns/confirm",
        valid_payload(matchType="keyword"),
    )
    pattern = get_recurring_pattern(db_conn, "NETFLIX::spending")

    assert confirm.status_code == 200
    assert pattern["merchant_id"] is None
    assert pattern["match_type"] == "keyword"
    assert db_conn.execute("SELECT COUNT(*) AS count FROM merchants").fetchone()["count"] == 0


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"patternKey": "A::spending", "merchant": "", "type": "spending"},
        {"patternKey": "A::neutral", "merchant": "A", "type": "neutral"},
    ],
)
def test_recurring_routes_reject_incomplete_payloads(client, payload):
    """Verify recurring route payload validation is surfaced as JSON."""
    response = recurring_json(client, "/recurring/patterns/confirm", payload)

    assert response.status_code == 400
    assert response.get_json() == {
        "ok": False,
        "message": "Recurring pattern payload is incomplete.",
    }


def test_recurring_pattern_payload_and_expected_day_normalization():
    """Verify recurring form payload normalization and day parsing."""
    assert recurring_pattern_payload(valid_payload(type="income")) == {
        "pattern_key": "NETFLIX::income",
        "merchant_id": None,
        "merchant": "NETFLIX",
        "match_type": "keyword",
        "type": "income",
    }
    with pytest.raises(ValueError, match="payload is incomplete"):
        recurring_pattern_payload({"patternKey": "bad", "merchant": "NETFLIX", "type": "neutral"})

    assert parse_expected_day("2026-05-31") == 31
    assert parse_expected_day("15") == 15
    assert parse_expected_day("0") is None
    assert parse_expected_day("2026-02-31") is None


def test_recurring_pattern_normalizers():
    """Verify recurring metadata normalizers constrain user-editable values."""
    assert recurring_pattern_key(" Netflix ", " spending ") == "Netflix::spending"
    assert normalize_user_status("CONFIRMED") == "confirmed"
    assert normalize_user_status("stale") == "detected"
    assert normalize_frequency("Weekly") == "Weekly"
    assert normalize_frequency("Every 10 days") is None
    assert normalize_active("inactive") == 0
    assert normalize_active("false") == 0
    assert normalize_active("yes") == 1
    assert normalize_optional_int("12", minimum=1, maximum=31) == 12
    assert normalize_optional_int("32", minimum=1, maximum=31) is None
    assert normalize_optional_float("12.345", minimum=0) == 12.35
    assert normalize_optional_float("-1", minimum=0) is None


def test_upsert_recurring_pattern_preserves_existing_values_when_not_overridden(db_conn):
    """Verify recurring pattern upserts update only explicit metadata values."""
    upsert_recurring_pattern(
        db_conn,
        "GYM::spending",
        "GYM",
        "spending",
        merchant_id=get_or_create_merchant_for_name(db_conn, "GYM")["id"],
        user_status="edited",
        frequency="Monthly-like",
        expected_day=5,
        typical_amount=49.99,
        date_tolerance_days=2,
        amount_tolerance=3.5,
        active=1,
    )
    db_conn.commit()
    merchant_id = db_conn.execute("SELECT id FROM merchants WHERE display_name = 'GYM'").fetchone()["id"]

    upsert_recurring_pattern(
        db_conn,
        f"merchant:{merchant_id}::spending",
        "GYM",
        "spending",
        merchant_id=merchant_id,
        user_status="confirmed",
    )
    db_conn.commit()

    pattern = get_recurring_pattern_by_merchant_type(db_conn, merchant_id, "spending")
    metadata = get_recurring_pattern_metadata(db_conn)
    assert pattern["merchant"] == "GYM"
    assert pattern["user_status"] == "confirmed"
    assert pattern["frequency"] == "Monthly-like"
    assert pattern["expected_day"] == 5
    assert pattern["typical_amount"] == 49.99
    assert pattern["date_tolerance_days"] == 2
    assert pattern["amount_tolerance"] == 3.5
    assert pattern["active"] == 1
    assert metadata[f"merchant:{merchant_id}::spending"]["merchant"] == "GYM"
