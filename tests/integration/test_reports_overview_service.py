"""Service-level tests for Reports overview context."""

from tests.support.context_services import seed_reporting_data
from werkzeug.datastructures import MultiDict

from finance_app.modules.reports.definitions import REPORT_INCOME, REPORT_OVERVIEW
from finance_app.modules.reports.service import build_reports_context


def reports_context(app, args):
    """Build a Reports overview context in a request context."""
    with app.test_request_context("/reports"):
        return build_reports_context(REPORT_OVERVIEW, MultiDict(args))


def income_context(app, args):
    """Build a Reports income context in a request context."""
    with app.test_request_context("/reports/income"):
        return build_reports_context(REPORT_INCOME, MultiDict(args))


def rows_by_label(rows):
    """Return report rows keyed by label."""
    return {row["label"]: row for row in rows}


def test_reports_overview_uses_reportable_cash_flow_by_default(app, core_conn):
    """Verify Reports overview includes unknown reportable rows and excludes transfers by default."""
    seed_reporting_data(core_conn)

    context = reports_context(
        app,
        [
            ("period", "custom"),
            ("date_from", "2026-01-01"),
            ("date_to", "2026-02-28"),
        ],
    )

    assert context["selected_basis"] == "cash_flow"
    assert context["selected_measure"] == "spending"
    assert context["total_spending"] == 290.00
    assert context["total_income"] == 1000.00
    assert context["net_cashflow"] == 710.00
    assert context["transaction_count"] == 5
    assert rows_by_label(context["category_rows"])["UNKNOWN"]["spending"] == 30.00
    assert rows_by_label(context["category_rows"])["Food"]["spending"] == 140.00
    assert rows_by_label(context["category_rows"])["Income"]["income"] == 1000.00
    assert rows_by_label(context["monthly_rows"])["2026-01"]["spending"] == 140.00
    assert rows_by_label(context["monthly_rows"])["2026-02"]["spending"] == 150.00
    assert "Card payment" not in {row["label"] for row in context["merchant_rows"]}


def test_reports_overview_ledger_basis_includes_payments_and_transfers(app, core_conn):
    """Verify ledger basis includes active payment and transfer rows."""
    seed_reporting_data(core_conn)

    context = reports_context(
        app,
        [
            ("period", "custom"),
            ("date_from", "2026-01-01"),
            ("date_to", "2026-01-31"),
            ("basis", "ledger"),
        ],
    )

    assert context["selected_basis"] == "ledger"
    assert context["total_spending"] == 640.00
    assert context["total_income"] == 1000.00
    assert context["net_cashflow"] == 360.00
    assert context["transaction_count"] == 4
    assert rows_by_label(context["category_rows"])["Transfers"]["spending"] == 500.00


def test_reports_income_section_scopes_to_income_and_credit_rows(app, core_conn):
    """Verify the income report focuses on rows contributing income and credits."""
    seed_reporting_data(core_conn)

    context = income_context(
        app,
        [
            ("period", "custom"),
            ("date_from", "2026-01-01"),
            ("date_to", "2026-02-28"),
        ],
    )

    assert context["active_report_section"].key == REPORT_INCOME
    assert context["selected_measure"] == "income"
    assert context["total_spending"] == 0.00
    assert context["total_income"] == 1000.00
    assert context["net_cashflow"] == 1000.00
    assert context["average_income_credit"] == 1000.00
    assert context["transaction_count"] == 1
    assert rows_by_label(context["monthly_rows"])["2026-01"]["income"] == 1000.00
    assert rows_by_label(context["category_rows"])["Income"]["income"] == 1000.00
    assert rows_by_label(context["merchant_rows"])["PAYROLL"]["income"] == 1000.00
    assert {row["description"] for row in context["income_evidence_rows"]} == {"Payroll"}
    assert "amount_type=credit" in context["transaction_url"]
