"""Financial correctness tests for analytics edge cases.

Validates dashboard, Reports, and comparison totals for refunds, account
payments, and date boundaries using the SQLAlchemy Core data layer.
"""

import pytest
from sqlalchemy import text
from werkzeug.datastructures import MultiDict

from finance_app.core.constants import (
    REIMBURSEMENT_CATEGORY,
    TRANSACTION_KIND_EXPENSE,
    TRANSACTION_KIND_INCOME,
    TRANSACTION_KIND_PAYMENT,
    TRANSACTION_KIND_REFUND,
)
from finance_app.modules.categories.repository import create_category, resolve_category_id
from finance_app.modules.comparison import service as comparison_service
from finance_app.modules.dashboard.service import build_dashboard_context
from finance_app.modules.reimbursements.service import create_reimbursement_allocation
from finance_app.modules.reports.definitions import REPORT_OVERVIEW
from finance_app.modules.reports.service import build_reports_context


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
    ignored=0,
):
    """Insert one report transaction for analytics assertions."""
    create_category(conn, category)
    result = conn.execute(
        text("""
        INSERT INTO transactions (
            tx_date,
            description,
            amount,
            category,
            category_id,
            needs_review,
            category_source,
            ignored,
            transaction_kind,
            fingerprint
        )
        VALUES (:p0, :p1, :p2, :p3, :category_id, 0, 'manual', :p4, :p5, :p6)
        """),
        {
            "p0": tx_date,
            "p1": description,
            "p2": amount,
            "p3": category,
            "category_id": resolve_category_id(conn, category),
            "p4": ignored,
            "p5": transaction_kind,
            "p6": fingerprint,
        },
    )
    conn.commit()
    return result.lastrowid


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


def reports_for_range(app, date_from, date_to):
    """Build a Reports overview context for an inclusive custom range."""
    with app.test_request_context("/reports"):
        return build_reports_context(
            REPORT_OVERVIEW,
            MultiDict(
                [
                    ("period", "custom"),
                    ("date_from", date_from),
                    ("date_to", date_to),
                ]
            ),
        )


def rows_by_label(rows):
    """Return report rows keyed by their display label."""
    return {row["label"]: row for row in rows}


@pytest.mark.parametrize(
    ("rows", "expected"),
    [
        (
            [
                ("2026-01-02", "Grocery run", 125.55, "Food", TRANSACTION_KIND_EXPENSE, 0),
                ("2026-01-03", "Transit pass", 10.10, "Transport", TRANSACTION_KIND_EXPENSE, 0),
                ("2026-01-04", "Grocery refund", -25.10, "Food", TRANSACTION_KIND_REFUND, 0),
                ("2026-01-05", "Payroll", -2000.00, "Income", TRANSACTION_KIND_INCOME, 0),
                ("2026-01-06", "Card payment", 500.00, "Transfers", TRANSACTION_KIND_PAYMENT, 0),
                ("2026-01-07", "Ignored expense", 999.99, "Food", TRANSACTION_KIND_EXPENSE, 1),
            ],
            {
                "total_spending": 110.55,
                "total_income": 2000.00,
                "net_cashflow": 1889.45,
                "transaction_count": 4,
            },
        ),
        (
            [
                ("2026-01-02", "Payroll", -1500.00, "Income", TRANSACTION_KIND_INCOME, 0),
                ("2026-01-03", "Card payment", 750.00, "Transfers", TRANSACTION_KIND_PAYMENT, 0),
                ("2026-01-04", "Ignored grocery", 45.00, "Food", TRANSACTION_KIND_EXPENSE, 1),
            ],
            {
                "total_spending": 0.00,
                "total_income": 1500.00,
                "net_cashflow": 1500.00,
                "transaction_count": 1,
            },
        ),
    ],
)
def test_dashboard_financial_calculations_table_driven(app, core_conn, rows, expected):
    """Verify dashboard totals classify transaction kinds and ignored rows."""
    for index, (tx_date, description, amount, category, kind, ignored) in enumerate(rows):
        insert_financial_transaction(
            core_conn,
            tx_date,
            description,
            amount,
            category,
            kind,
            f"financial-table-{index}-{description}",
            ignored=ignored,
        )

    context = dashboard_for_range(app, "2026-01-01", "2026-01-31")

    for key, expected_value in expected.items():
        assert context[key] == expected_value
    assert round(context["total_income"] - context["total_spending"], 2) == context["net_cashflow"]

    reports_context = reports_for_range(app, "2026-01-01", "2026-01-31")

    assert round(sum(row["spending"] for row in reports_context["category_rows"]), 2) == context["total_spending"]


def test_dashboard_refunds_reduce_expense_totals_without_counting_as_income(app, core_conn):
    """Verify refund rows reduce spending totals and stay out of income totals."""
    insert_financial_transaction(
        core_conn,
        "2026-02-01",
        "Book Store Purchase",
        100.00,
        "Personal",
        TRANSACTION_KIND_EXPENSE,
        "financial-refund-purchase",
    )
    insert_financial_transaction(
        core_conn,
        "2026-02-02",
        "Book Store Refund",
        -25.00,
        "Personal",
        TRANSACTION_KIND_REFUND,
        "financial-refund-credit",
    )
    insert_financial_transaction(
        core_conn,
        "2026-02-03",
        "Payroll",
        -1000.00,
        "Income",
        TRANSACTION_KIND_INCOME,
        "financial-refund-income",
    )
    insert_financial_transaction(
        core_conn,
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

    reports_context = reports_for_range(app, "2026-02-01", "2026-02-28")
    category_rows = rows_by_label(reports_context["category_rows"])
    monthly_rows = rows_by_label(reports_context["monthly_rows"])

    assert category_rows["Personal"]["spending"] == 75.00
    assert monthly_rows["2026-02"]["spending"] == 75.00
    assert monthly_rows["2026-02"]["income"] == 1000.00


def test_dashboard_reimbursements_reduce_original_expense_category(app, core_conn):
    """Verify allocated reimbursements reduce spending without becoming income."""
    expense_id = insert_financial_transaction(
        core_conn,
        "2026-02-01",
        "Conference hotel",
        1000.00,
        "Travel",
        TRANSACTION_KIND_EXPENSE,
        "financial-reimbursement-expense",
    )
    reimbursement_id = insert_financial_transaction(
        core_conn,
        "2026-02-15",
        "Conference reimbursement",
        -900.00,
        REIMBURSEMENT_CATEGORY,
        TRANSACTION_KIND_INCOME,
        "financial-reimbursement-credit",
    )
    create_reimbursement_allocation(reimbursement_id, expense_id, 900.00, conn=core_conn)

    context = dashboard_for_range(app, "2026-02-01", "2026-02-28")

    assert context["total_spending"] == 100.00
    assert context["total_income"] == 0.00
    assert context["net_cashflow"] == -100.00
    assert context["transaction_count"] == 1

    reports_context = reports_for_range(app, "2026-02-01", "2026-02-28")
    category_rows = rows_by_label(reports_context["category_rows"])
    monthly_rows = rows_by_label(reports_context["monthly_rows"])

    assert category_rows["Travel"]["spending"] == 100.00
    assert monthly_rows["2026-02"]["spending"] == 100.00
    assert monthly_rows["2026-02"]["income"] == 0.00


def test_dashboard_custom_date_boundaries_are_inclusive_for_leap_months(app, core_conn):
    """Verify custom dashboard ranges include exact start/end dates only."""
    rows = [
        ("2024-01-31", "Before range", 10.00, "financial-before-range"),
        ("2024-02-01", "Start boundary", 20.00, "financial-start-boundary"),
        ("2024-02-29", "Leap day boundary", 40.00, "financial-leap-boundary"),
        ("2024-03-01", "After range", 80.00, "financial-after-range"),
    ]
    for tx_date, description, amount, fingerprint in rows:
        insert_financial_transaction(
            core_conn,
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

    reports_context = reports_for_range(app, "2024-02-01", "2024-02-29")
    category_rows = rows_by_label(reports_context["category_rows"])

    assert category_rows["Food"]["spending"] == 60.00


def test_comparison_refunds_reduce_current_and_previous_period_spending(app, core_conn, monkeypatch):
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
            core_conn,
            tx_date,
            description,
            amount,
            "Food" if kind != TRANSACTION_KIND_INCOME else "Income",
            kind,
            fingerprint,
        )

    with app.test_request_context("/comparison"):
        context = comparison_service.build_comparison_context(MultiDict([("period_comparison", "month_previous")]))

    totals = {metric["label"]: metric for metric in context["period_comparison"]["totals"]}
    category_rows = {row["category"]: row for row in context["period_comparison"]["category_rows"]}

    assert totals["Spending"]["current"] == 150.00
    assert totals["Spending"]["previous"] == 100.00
    assert totals["Income and Credits"]["current"] == 1000.00
    assert totals["Transactions"]["current"] == 3
    assert totals["Transactions"]["previous"] == 2
    assert category_rows["Food"]["current"] == 150.00
    assert category_rows["Food"]["previous"] == 100.00


def test_comparison_reimbursements_reduce_original_expense_category(app, core_conn, monkeypatch):
    """Verify comparison views use allocated reimbursements as expense offsets."""
    monkeypatch.setattr(comparison_service, "date", FixedComparisonDate)
    expense_id = insert_financial_transaction(
        core_conn,
        "2026-03-01",
        "Conference hotel",
        1000.00,
        "Travel",
        TRANSACTION_KIND_EXPENSE,
        "financial-comparison-reimbursement-expense",
    )
    reimbursement_id = insert_financial_transaction(
        core_conn,
        "2026-03-02",
        "Conference reimbursement",
        -900.00,
        REIMBURSEMENT_CATEGORY,
        TRANSACTION_KIND_INCOME,
        "financial-comparison-reimbursement-credit",
    )
    create_reimbursement_allocation(reimbursement_id, expense_id, 900.00, conn=core_conn)

    with app.test_request_context("/comparison"):
        context = comparison_service.build_comparison_context(MultiDict([("period_comparison", "month_previous")]))

    totals = {metric["label"]: metric for metric in context["period_comparison"]["totals"]}
    category_rows = {row["category"]: row for row in context["period_comparison"]["category_rows"]}

    assert totals["Spending"]["current"] == 100.00
    assert totals["Income and Credits"]["current"] == 0.00
    assert totals["Transactions"]["current"] == 1
    assert category_rows["Travel"]["current"] == 100.00
