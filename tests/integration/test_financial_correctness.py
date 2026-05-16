"""Financial correctness tests for analytics edge cases.

Validates dashboard and comparison totals for refunds, account payments, and
date boundaries using the SQLAlchemy Core data layer.
"""

from werkzeug.datastructures import MultiDict

from finance_app.core.constants import (
    TRANSACTION_KIND_EXPENSE,
    TRANSACTION_KIND_INCOME,
    TRANSACTION_KIND_PAYMENT,
    TRANSACTION_KIND_REFUND,
)
from finance_app.modules.comparison import service as comparison_service
from finance_app.modules.dashboard.service import build_dashboard_context


class FixedComparisonDate:
    """Fixed replacement for comparison service date calculations."""

    @classmethod
    def today(cls):
        """Return a deterministic date in March 2026."""
        return cls(2026, 3, 15)

    def __new__(cls, year, month, day):
        """Build a real date instance without importing under the patched name."""
        from datetime import date

        return date(year, month, day)


def insert_financial_transaction(
    conn,
    tx_date,
    description,
    amount,
    category,
    transaction_kind,
    fingerprint,
):
    """Insert one report transaction for analytics assertions."""
    conn.execute(
        """
        INSERT INTO transactions (
            tx_date,
            description,
            amount,
            category,
            needs_review,
            category_source,
            ignored,
            transaction_kind,
            fingerprint
        )
        VALUES (?, ?, ?, ?, 0, 'manual', 0, ?, ?)
        """,
        (tx_date, description, amount, category, transaction_kind, fingerprint),
    )
    conn.commit()


def dashboard_for_range(app, date_from, date_to):
    """Build a dashboard context for an inclusive custom range."""
    with app.test_request_context("/dashboard"):
        return build_dashboard_context(
            MultiDict(
                [
                    ("period", "custom"),
                    ("date_from", date_from),
                    ("date_to", date_to),
                ]
            )
        )


def test_dashboard_refunds_reduce_expense_totals_without_counting_as_income(app, db_conn):
    """Verify refund rows reduce spending totals and stay out of income totals."""
    insert_financial_transaction(
        db_conn,
        "2026-02-01",
        "Book Store Purchase",
        100.00,
        "Personal",
        TRANSACTION_KIND_EXPENSE,
        "financial-refund-purchase",
    )
    insert_financial_transaction(
        db_conn,
        "2026-02-02",
        "Book Store Refund",
        -25.00,
        "Personal",
        TRANSACTION_KIND_REFUND,
        "financial-refund-credit",
    )
    insert_financial_transaction(
        db_conn,
        "2026-02-03",
        "Payroll",
        -1000.00,
        "Income",
        TRANSACTION_KIND_INCOME,
        "financial-refund-income",
    )
    insert_financial_transaction(
        db_conn,
        "2026-02-04",
        "Credit Card Payment",
        500.00,
        "Transfers",
        TRANSACTION_KIND_PAYMENT,
        "financial-refund-payment",
    )

    context = dashboard_for_range(app, "2026-02-01", "2026-02-28")

    assert context["total_spending"] == 75.00
    assert context["total_income"] == 1000.00
    assert context["net_cashflow"] == 925.00
    assert context["transaction_count"] == 3
    assert context["category_totals"] == [75.00]
    assert context["expense_month_totals"] == [75.00]
    assert context["income_month_totals"] == [1000.00]


def test_dashboard_custom_date_boundaries_are_inclusive_for_leap_months(app, db_conn):
    """Verify custom dashboard ranges include exact start/end dates only."""
    rows = [
        ("2024-01-31", "Before range", 10.00, "financial-before-range"),
        ("2024-02-01", "Start boundary", 20.00, "financial-start-boundary"),
        ("2024-02-29", "Leap day boundary", 40.00, "financial-leap-boundary"),
        ("2024-03-01", "After range", 80.00, "financial-after-range"),
    ]
    for tx_date, description, amount, fingerprint in rows:
        insert_financial_transaction(
            db_conn,
            tx_date,
            description,
            amount,
            "Food",
            TRANSACTION_KIND_EXPENSE,
            fingerprint,
        )

    context = dashboard_for_range(app, "2024-02-01", "2024-02-29")

    assert context["total_spending"] == 60.00
    assert context["transaction_count"] == 2
    assert context["first_tx_date"] == "2024-02-01"
    assert context["last_tx_date"] == "2024-02-29"
    assert context["category_totals"] == [60.00]


def test_comparison_refunds_reduce_current_and_previous_period_spending(app, db_conn, monkeypatch):
    """Verify period comparison applies refund offsets in both periods."""
    monkeypatch.setattr(comparison_service, "date", FixedComparisonDate)
    for tx_date, description, amount, kind, fingerprint in [
        ("2026-03-01", "Current purchase", 200.00, TRANSACTION_KIND_EXPENSE, "financial-current-purchase"),
        ("2026-03-02", "Current refund", -50.00, TRANSACTION_KIND_REFUND, "financial-current-refund"),
        ("2026-02-01", "Previous purchase", 120.00, TRANSACTION_KIND_EXPENSE, "financial-previous-purchase"),
        ("2026-02-02", "Previous refund", -20.00, TRANSACTION_KIND_REFUND, "financial-previous-refund"),
        ("2026-03-03", "Current income", -1000.00, TRANSACTION_KIND_INCOME, "financial-current-income"),
    ]:
        insert_financial_transaction(
            db_conn,
            tx_date,
            description,
            amount,
            "Food" if kind != TRANSACTION_KIND_INCOME else "Income",
            kind,
            fingerprint,
        )

    with app.test_request_context("/comparison"):
        context = comparison_service.build_comparison_context(
            MultiDict([("period_comparison", "month_previous")])
        )

    totals = {
        metric["label"]: metric
        for metric in context["period_comparison"]["totals"]
    }
    category_rows = {
        row["category"]: row
        for row in context["period_comparison"]["category_rows"]
    }

    assert totals["Spending"]["current"] == 150.00
    assert totals["Spending"]["previous"] == 100.00
    assert totals["Income and Credits"]["current"] == 1000.00
    assert totals["Transactions"]["current"] == 3
    assert totals["Transactions"]["previous"] == 2
    assert category_rows["Food"]["current"] == 150.00
    assert category_rows["Food"]["previous"] == 100.00
