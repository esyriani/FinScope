"""Tests for calendar recurring-transaction inference."""

from datetime import date
from decimal import Decimal

from finance_app.modules.calendar.recurrence import (
    classify_recurring_match,
    infer_recurring_items,
    missed_recurring_cycles,
    recurrence_amount_tolerance,
    recurring_amount_change_details,
    recurring_confidence_label,
    recurring_frequency_label,
)
from finance_app.modules.recurring.settings import RecurrenceDetectionSettings


def settings():
    """Return compact deterministic recurrence settings for tests."""
    return RecurrenceDetectionSettings(
        minimum_occurrences=3,
        date_tolerance_days=3,
        amount_tolerance_absolute=5,
        amount_tolerance_percent=0.10,
        missed_cycles_before_inactive=2,
    )


def candidate(tx_date, amount):
    """Build a current-month recurrence candidate."""
    return {
        "date": tx_date,
        "amount": amount,
    }


def test_classify_recurring_match_statuses_by_date_amount_and_missing_cycles():
    """Verify recurrence status priority across match and missing scenarios."""
    recurrence_settings = settings()
    expected_date = date(2026, 5, 15)

    occurred = classify_recurring_match(
        [candidate("2026-05-16", 101.0)],
        expected_date,
        100.0,
        date(2026, 5, 20),
        recurrence_settings,
    )
    amount_changed = classify_recurring_match(
        [candidate("2026-05-15", 130.0)],
        expected_date,
        100.0,
        date(2026, 5, 20),
        recurrence_settings,
    )
    likely = classify_recurring_match(
        [candidate("2026-05-09", 100.0)],
        expected_date,
        100.0,
        date(2026, 5, 20),
        recurrence_settings,
    )
    far_same_merchant = classify_recurring_match(
        [candidate("2026-05-05", 100.0)],
        expected_date,
        100.0,
        date(2026, 5, 20),
        recurrence_settings,
    )
    expected = classify_recurring_match(
        [],
        expected_date,
        100.0,
        date(2026, 5, 16),
        recurrence_settings,
        last_seen=date(2026, 4, 15),
        frequency="Monthly-like",
    )
    overdue = classify_recurring_match(
        [],
        expected_date,
        100.0,
        date(2026, 5, 20),
        recurrence_settings,
        last_seen=date(2026, 4, 15),
        frequency="Monthly-like",
    )
    inactive = classify_recurring_match(
        [],
        expected_date,
        100.0,
        date(2026, 5, 20),
        recurrence_settings,
        last_seen=date(2026, 3, 15),
        frequency="Monthly-like",
    )

    assert occurred["status"] == "occurred"
    assert occurred["date_difference_days"] == 1
    assert occurred["amount_difference"] == 1.0
    assert amount_changed["status"] == "amount_changed"
    assert amount_changed["amount_difference"] == 30.0
    assert likely["status"] == "likely_occurred"
    assert likely["date_difference_days"] == -6
    assert likely["likely_date_tolerance_days"] == 6
    assert far_same_merchant["status"] == "overdue"
    assert far_same_merchant["matched_date"] is None
    assert expected["status"] == "expected"
    assert overdue["status"] == "overdue"
    assert inactive["status"] == "possibly_inactive"
    assert inactive["missed_cycles"] == 2


def test_amount_tolerance_and_amount_change_details():
    """Verify amount tolerances and human-readable amount-change metadata."""
    recurrence_settings = settings()
    amount_changed_match = classify_recurring_match(
        [candidate("2026-05-15", 130.0)],
        date(2026, 5, 15),
        100.0,
        date(2026, 5, 20),
        recurrence_settings,
    )

    assert recurrence_amount_tolerance(Decimal("20.00"), recurrence_settings) == Decimal("5")
    assert recurrence_amount_tolerance(Decimal("100.00"), recurrence_settings) == Decimal("10")
    assert isinstance(recurrence_amount_tolerance(Decimal("100.00"), recurrence_settings), Decimal)
    assert recurring_amount_change_details(100.0, amount_changed_match) == {
        "typical_amount": 100.0,
        "actual_amount": 130.0,
        "difference": 30.0,
        "percent": 30.0,
    }
    assert recurring_amount_change_details(100.0, {"status": "occurred"}) is None


def test_missed_recurring_cycles_for_known_frequencies():
    """Verify missed cycle calculations for monthly, weekly, quarterly, and annual patterns."""
    expected_date = date(2026, 5, 15)

    assert missed_recurring_cycles(date(2026, 3, 15), expected_date, "Monthly-like") == 2
    assert missed_recurring_cycles(date(2026, 5, 1), expected_date, "Weekly") == 2
    assert missed_recurring_cycles(date(2026, 2, 1), expected_date, "Quarterly") == 1
    assert missed_recurring_cycles(date(2024, 5, 15), expected_date, "Annual") == 2
    assert missed_recurring_cycles(date(2026, 5, 16), expected_date, "Monthly-like") == 0


def test_recurring_frequency_label_weekly_monthly_and_noisy_edges():
    """Verify frequency labels for weekly, monthly, and irregular recurrence evidence."""
    weekly_dates = [
        date(2026, 1, 1),
        date(2026, 1, 8),
        date(2026, 1, 15),
        date(2026, 1, 23),
    ]
    monthly_dates = [
        date(2026, 1, 31),
        date(2026, 2, 28),
        date(2026, 3, 31),
        date(2026, 4, 30),
    ]
    quarterly_dates = [
        date(2026, 1, 31),
        date(2026, 4, 30),
        date(2026, 7, 31),
        date(2026, 10, 31),
    ]
    irregular_dates = [
        date(2026, 1, 1),
        date(2026, 1, 19),
        date(2026, 3, 2),
        date(2026, 7, 12),
    ]

    assert recurring_frequency_label(weekly_dates, {"2026-01"}) == "Weekly"
    assert recurring_frequency_label(monthly_dates, {"2026-01", "2026-02", "2026-03", "2026-04"}) == "Monthly-like"
    assert recurring_frequency_label(quarterly_dates, {"2026-01", "2026-04", "2026-07", "2026-10"}) == "Quarterly"
    assert recurring_frequency_label(irregular_dates, {"2026-01", "2026-03", "2026-07"}) == "Irregular recurring"


def test_infer_recurring_items_uses_current_month_matches_and_metadata_overrides(app):
    """Verify end-to-end recurring inference with current-month matching and metadata."""
    rows = [
        {
            "tx_date": "2026-01-05",
            "description": "NETFLIX",
            "amount": 18.99,
            "category": "Entertainment",
            "account_name": "Visa",
        },
        {
            "tx_date": "2026-02-05",
            "description": "NETFLIX",
            "amount": 18.99,
            "category": "Entertainment",
            "account_name": "Visa",
        },
        {
            "tx_date": "2026-03-05",
            "description": "NETFLIX",
            "amount": 18.99,
            "category": "Entertainment",
            "account_name": "Visa",
        },
    ]
    month_transactions = [
        {
            "merchant_key": "NETFLIX",
            "type": "spending",
            "date": "2026-04-07",
            "amount": 20.00,
        }
    ]

    with app.test_request_context():
        recurring = infer_recurring_items(
            rows,
            date(2026, 4, 1),
            date(2026, 4, 30),
            month_transactions,
            recurrence_settings=settings(),
            recurring_pattern_metadata={
                "NETFLIX::spending": {
                    "user_status": "edited",
                    "active": 1,
                    "expected_day": 6,
                    "typical_amount": 19.50,
                    "frequency": "Monthly-like",
                    "date_tolerance_days": 2,
                    "amount_tolerance": 2.0,
                }
            },
        )

    assert len(recurring) == 1
    item = recurring[0]
    assert item["pattern_key"] == "NETFLIX::spending"
    assert item["date"] == "2026-04-06"
    assert item["amount"] == 19.50
    assert item["frequency"] == "Monthly-like"
    assert item["user_status"] == "edited"
    assert item["status"] == "occurred"
    assert item["match_details"]["date_difference_days"] == 1
    assert item["match_details"]["amount_difference"] == 0.5
    assert item["confidence"] == "Low"


def test_infer_recurring_items_supports_merchant_bound_and_keyword_fuzzy_metadata(app):
    """Verify recurrence metadata can be merchant-bound or keyword-fuzzy."""
    rows = [
        {
            "tx_date": "2026-01-05",
            "description": "NETFLIX",
            "merchant_id": 7,
            "merchant_name": "NETFLIX",
            "merchant_key": "NETFLIX",
            "amount": 18.99,
            "category": "Entertainment",
            "account_name": "Visa",
        },
        {
            "tx_date": "2026-02-05",
            "description": "NETFLIX",
            "merchant_id": 7,
            "merchant_name": "NETFLIX",
            "merchant_key": "NETFLIX",
            "amount": 18.99,
            "category": "Entertainment",
            "account_name": "Visa",
        },
        {
            "tx_date": "2026-03-05",
            "description": "NETFLIX",
            "merchant_id": 7,
            "merchant_name": "NETFLIX",
            "merchant_key": "NETFLIX",
            "amount": 18.99,
            "category": "Entertainment",
            "account_name": "Visa",
        },
    ]
    month_transactions = [
        {
            "merchant_key": "merchant:7",
            "type": "spending",
            "date": "2026-04-07",
            "amount": 18.99,
        }
    ]

    with app.test_request_context():
        recurring = infer_recurring_items(
            rows,
            date(2026, 4, 1),
            date(2026, 4, 30),
            month_transactions,
            recurrence_settings=settings(),
            recurring_pattern_metadata={
                "NETFLIX::spending": {
                    "match_type": "keyword",
                    "user_status": "edited",
                    "active": 1,
                    "expected_day": 7,
                }
            },
        )

    assert len(recurring) == 1
    assert recurring[0]["merchant_id"] == 7
    assert recurring[0]["match_type"] == "keyword"
    assert recurring[0]["pattern_key"] == "NETFLIX::spending"
    assert recurring[0]["status"] == "occurred"


def test_recurring_confidence_label_thresholds():
    """Verify confidence labels track observed-month thresholds."""
    recurrence_settings = settings()

    assert recurring_confidence_label(3, recurrence_settings) == "Low"
    assert recurring_confidence_label(4, recurrence_settings) == "Medium"
    assert recurring_confidence_label(6, recurrence_settings) == "High"
