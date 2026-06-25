"""Service-level tests for Reports overview context."""

from sqlalchemy import text
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


def test_reports_overview_uses_categorized_reportable_cash_flow_by_default(app, core_conn):
    """Verify Reports overview defaults to categorized reportable cash-flow rows."""
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
    assert context["quick_view"] == "categorized"
    assert context["total_spending"] == 260.00
    assert context["total_income"] == 1000.00
    assert context["net_cashflow"] == 740.00
    assert context["transaction_count"] == 4
    assert "UNKNOWN" not in rows_by_label(context["category_rows"])
    assert rows_by_label(context["category_rows"])["Food"]["spending"] == 140.00
    assert rows_by_label(context["category_rows"])["Income"]["income"] == 1000.00
    assert rows_by_label(context["category_rows"])["Food"]["url"].startswith("/reports/categories/")
    assert rows_by_label(context["tag_rows"])["Tax"]["url"].startswith("/reports/tags/")
    assert rows_by_label(context["merchant_rows"])["METRO GROCERY"]["url"].startswith("/reports/merchants")
    assert not rows_by_label(context["merchant_rows"])["METRO GROCERY"]["url"].startswith("/transactions")
    assert rows_by_label(context["monthly_rows"])["2026-01"]["spending"] == 140.00
    assert rows_by_label(context["monthly_rows"])["2026-02"]["spending"] == 120.00
    assert "Card payment" not in {row["label"] for row in context["merchant_rows"]}
    assert "comparison_view=period" in context["comparison_url"]
    assert "analysis_mode=spending" in context["comparison_url"]


def test_reports_overview_all_quick_view_includes_unknown_rows(app, core_conn):
    """Verify the All quick view includes unknown reportable rows when selected."""
    seed_reporting_data(core_conn)

    context = reports_context(
        app,
        [
            ("period", "custom"),
            ("date_from", "2026-01-01"),
            ("date_to", "2026-02-28"),
            ("quick_view", "all"),
        ],
    )

    assert context["quick_view"] == "all"
    assert context["total_spending"] == 290.00
    assert context["net_cashflow"] == 710.00
    assert context["transaction_count"] == 5
    assert rows_by_label(context["category_rows"])["UNKNOWN"]["spending"] == 30.00
    assert rows_by_label(context["monthly_rows"])["2026-02"]["spending"] == 150.00


def test_reports_overview_implicit_scope_falls_back_when_only_unknown_rows_exist(app, core_conn):
    """Verify a fresh all-unknown dataset does not render a blank default report."""
    core_conn.execute(text("""
        INSERT INTO transactions (
            tx_date,
            description,
            amount,
            category,
            needs_review,
            category_source,
            transaction_kind,
            fingerprint
        )
        VALUES ('2026-02-01', 'Unknown store', 42.00, 'UNKNOWN', 1, 'unknown', 'expense', 'reports-unknown-only')
        """))
    core_conn.commit()

    default_context = reports_context(
        app,
        [
            ("period", "custom"),
            ("date_from", "2026-02-01"),
            ("date_to", "2026-02-28"),
        ],
    )
    explicit_context = reports_context(
        app,
        [
            ("period", "custom"),
            ("date_from", "2026-02-01"),
            ("date_to", "2026-02-28"),
            ("quick_view", "categorized"),
        ],
    )

    assert default_context["quick_view"] == "all"
    assert default_context["transaction_count"] == 1
    assert default_context["total_spending"] == 42.00
    assert "quick_view=all" in default_context["reports_export_csv_url"]
    assert explicit_context["quick_view"] == "categorized"
    assert explicit_context["transaction_count"] == 0


def test_reports_overview_quick_view_filters_unknown_rows(app, core_conn):
    """Verify Reports quick view filters all aggregates and exposes scoped counts."""
    seed_reporting_data(core_conn)

    context = reports_context(
        app,
        [
            ("period", "custom"),
            ("date_from", "2026-01-01"),
            ("date_to", "2026-02-28"),
            ("quick_view", "unknown"),
        ],
    )

    quick_view_options = {option["value"]: option for option in context["quick_view_options"]}
    assert context["quick_view"] == "unknown"
    assert quick_view_options["unknown"]["active"] is True
    assert quick_view_options["all"]["count"] == 5
    assert quick_view_options["categorized"]["count"] == 4
    assert context["transaction_count"] == 1
    assert context["total_spending"] == 30.00
    assert set(rows_by_label(context["category_rows"])) == {"UNKNOWN"}


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
    assert "analysis_mode=income" in context["comparison_url"]
