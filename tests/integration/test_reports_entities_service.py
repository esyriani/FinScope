"""Service-level tests for Reports account and merchant contexts."""

from urllib.parse import quote_plus

from tests.support.context_services import seed_entity_report_data
from werkzeug.datastructures import MultiDict

from finance_app.modules.reports.definitions import REPORT_ACCOUNTS, REPORT_MERCHANTS
from finance_app.modules.reports.service import (
    build_reports_account_detail_context,
    build_reports_context,
    build_reports_merchant_detail_context,
)


def entity_args(*extra):
    """Return a deterministic custom Reports period with optional filters."""
    return MultiDict(
        [
            ("period", "custom"),
            ("date_from", "2026-01-01"),
            ("date_to", "2026-01-31"),
            *extra,
        ]
    )


def rows_by_label(rows):
    """Return report rows keyed by label."""
    return {row["label"]: row for row in rows}


def test_account_index_lists_account_report_targets(app, core_conn, data_factory):
    """Verify account report targets use linked account aggregates."""
    seed = seed_entity_report_data(data_factory, core_conn)

    with app.test_request_context("/reports/accounts"):
        context = build_reports_context(REPORT_ACCOUNTS, entity_args())

    account_rows = rows_by_label(context["entity_rows"])
    assert context["active_report_section"].key == REPORT_ACCOUNTS
    assert context["entity_index_title"] == "Account reports"
    assert context["total_spending"] == 340.00
    assert context["total_income"] == 1000.00
    assert account_rows["Personal Checking"]["spending"] == 140.00
    assert account_rows["Personal Checking"]["income"] == 1000.00
    assert account_rows["Personal Checking"]["type_label"] == "Checking account"
    assert account_rows["Personal Checking"]["url"].startswith(f"/reports/accounts/{seed['checking_id']}")
    assert account_rows["Travel Card"]["spending"] == 200.00
    assert account_rows["Travel Card"]["type_label"] == "Credit card"


def test_account_detail_scopes_to_target_account(app, core_conn, data_factory):
    """Verify account detail reports target the path account over stale account filters."""
    seed = seed_entity_report_data(data_factory, core_conn)

    with app.test_request_context(f"/reports/accounts/{seed['checking_id']}"):
        context = build_reports_account_detail_context(
            seed["checking_id"],
            entity_args(("account_id", str(seed["card_id"]))),
        )

    category_rows = rows_by_label(context["category_rows"])
    tag_rows = rows_by_label(context["tag_rows"])
    merchant_rows = rows_by_label(context["merchant_rows"])
    evidence_descriptions = {row["description"] for row in context["entity_evidence_rows"]}
    assert context["entity_target"].name == "Personal Checking"
    assert context["selected_account_id"] == seed["checking_id"]
    assert context["total_spending"] == 140.00
    assert context["total_income"] == 1000.00
    assert context["transaction_count"] == 3
    assert category_rows["Food"]["spending"] == 140.00
    assert category_rows["Income"]["income"] == 1000.00
    assert category_rows["Food"]["url"].startswith("/reports/categories/")
    assert tag_rows["Tax"]["spending"] == 100.00
    assert tag_rows["Tax"]["url"].startswith("/reports/tags/")
    assert tag_rows["Shared"]["spending"] == 40.00
    assert merchant_rows[seed["metro_merchant_name"]]["spending"] == 100.00
    assert merchant_rows[seed["metro_merchant_name"]]["url"].startswith(
        f"/reports/merchants/{seed['metro_merchant_id']}"
    )
    assert merchant_rows[seed["cafe_merchant_name"]]["spending"] == 40.00
    assert evidence_descriptions == {"Metro Grocery", "Cafe Bistro", "Payroll"}
    assert f"account_id={seed['checking_id']}" in context["transaction_url"]
    assert f"account_id={seed['checking_id']}" in context["comparison_url"]
    assert f"account_id={seed['card_id']}" not in context["comparison_url"]
    assert f"account_id={seed['card_id']}" not in context["reports_export_csv_url"]


def test_merchant_index_lists_merchant_report_targets(app, core_conn, data_factory):
    """Verify merchant report targets use durable merchant aggregates."""
    seed = seed_entity_report_data(data_factory, core_conn)

    with app.test_request_context("/reports/merchants"):
        context = build_reports_context(REPORT_MERCHANTS, entity_args())

    merchant_rows = rows_by_label(context["entity_rows"])
    assert context["active_report_section"].key == REPORT_MERCHANTS
    assert context["entity_index_title"] == "Merchant reports"
    assert merchant_rows[seed["metro_merchant_name"]]["spending"] == 100.00
    assert merchant_rows[seed["metro_merchant_name"]]["type_label"] == "Merchant"
    assert merchant_rows[seed["metro_merchant_name"]]["url"].startswith(
        f"/reports/merchants/{seed['metro_merchant_id']}"
    )
    assert merchant_rows[seed["hotel_merchant_name"]]["spending"] == 200.00
    assert merchant_rows[seed["payroll_merchant_name"]]["income"] == 1000.00


def test_merchant_detail_scopes_to_target_merchant(app, core_conn, data_factory):
    """Verify merchant detail reports target the path merchant over stale merchant filters."""
    seed = seed_entity_report_data(data_factory, core_conn)

    with app.test_request_context(f"/reports/merchants/{seed['metro_merchant_id']}"):
        context = build_reports_merchant_detail_context(
            seed["metro_merchant_id"],
            entity_args(
                ("merchant_id", str(seed["cafe_merchant_id"])),
                ("merchant_query", seed["cafe_merchant_name"]),
            ),
        )

    account_rows = rows_by_label(context["account_rows"])
    category_rows = rows_by_label(context["category_rows"])
    tag_rows = rows_by_label(context["tag_rows"])
    evidence_descriptions = {row["description"] for row in context["entity_evidence_rows"]}
    assert context["entity_target"].name == seed["metro_merchant_name"]
    assert context["selected_merchant_id"] == seed["metro_merchant_id"]
    assert context["selected_merchant_label"] == seed["metro_merchant_name"]
    assert context["total_spending"] == 100.00
    assert context["total_income"] == 0.00
    assert context["transaction_count"] == 1
    assert account_rows["Personal Checking"]["spending"] == 100.00
    assert account_rows["Personal Checking"]["url"].startswith(f"/reports/accounts/{seed['checking_id']}")
    assert category_rows["Food"]["spending"] == 100.00
    assert tag_rows["Tax"]["spending"] == 100.00
    assert evidence_descriptions == {"Metro Grocery"}
    assert f"merchant_key={quote_plus(seed['metro_merchant_name'])}" in context["transaction_url"]
    assert f"merchant_id={seed['metro_merchant_id']}" in context["comparison_url"]
    assert f"merchant_id={seed['cafe_merchant_id']}" not in context["comparison_url"]
    assert f"merchant_id={seed['cafe_merchant_id']}" not in context["reports_export_csv_url"]
    assert "merchant_query=" not in context["reports_export_csv_url"]
