"""Route tests for Reports pages and exports."""

import csv
import io
import zipfile
from decimal import Decimal
from urllib.parse import quote_plus

from sqlalchemy import select, text
from tests.support.context_services import seed_entity_report_data, seed_reporting_data
from tests.support.html import (
    assert_asset_reference,
    assert_has_element,
    assert_not_visible_text,
    assert_visible_text,
    response_html,
)

from finance_app.core.constants import REIMBURSEMENT_CATEGORY, TRANSACTION_KIND_EXPENSE, TRANSACTION_KIND_INCOME
from finance_app.database.tables import categories as categories_table
from finance_app.database.tables import tags as tags_table
from finance_app.modules.reimbursements.service import create_reimbursement_allocation


def category_id(conn, name):
    """Return a persisted category id by name."""
    return conn.execute(select(categories_table.c.id).where(categories_table.c.name == name)).scalar_one()


def tag_id(conn, name):
    """Return a persisted tag id by name."""
    return conn.execute(select(tags_table.c.id).where(tags_table.c.name == name)).scalar_one()


def test_reports_overview_route_renders_read_only_analysis(client, core_conn):
    """Verify Reports overview renders shared filters, charts, tables, and actions."""
    seed_reporting_data(core_conn)

    response = client.get("/reports?period=custom&date_from=2026-01-01&date_to=2026-02-28")
    body = response_html(response)

    assert response.status_code == 200
    assert_visible_text(
        response,
        "Reports",
        "Detailed money analysis.",
        "Reportable cash flow",
        "Quick view",
        "Monthly statement",
        "Category breakdown",
        "Tag breakdown",
        "Account breakdown",
        "Merchant breakdown",
        "UNKNOWN",
    )
    assert_not_visible_text(
        response,
        "Export CSV",
        "Export Excel",
        "Edit category",
        "Approve selected",
        "Recategorize selected",
    )
    assert 'class="reports-section-tabs page-tabs nav nav-tabs"' in body
    assert "reports-chart-card reports-chart-card-wide" in body
    assert "data-chart-export-scope" in body
    assert "data-table-export-scope" in body
    assert "data-table-export-toolbar" in body
    assert "data-collapse-panel-header-toggle" in body
    assert 'id="reports-monthly-table-panel"' in body
    assert "reports-table-collapse" in body
    assert "reports-table-toggle" not in body
    assert "data-sortable-table" in body
    assert "data-paginated-table" in body
    assert "data-table-search" in body
    assert 'data-row-drilldown="dblclick"' in body
    assert 'data-row-href="/transactions' not in body
    assert 'data-row-href="/reports/categories/' in body
    assert 'data-row-href="/reports/tags/' in body
    assert 'data-row-href="/reports/merchants' in body
    assert 'href="/comparison?comparison_view=period&amp;analysis_mode=spending"' in body
    assert_asset_reference(response, r"/static/js/reports-charts\.js\?v=[0-9a-f]{12}")
    assert_asset_reference(response, r"/static/js/exports\.js\?v=[0-9a-f]{12}")


def test_reports_overview_csv_export_uses_active_filters_and_sanitizes_formulas(client, core_conn):
    """Verify Reports CSV exports filtered overview rows and neutralize spreadsheet formulas."""
    core_conn.execute(text("""
        INSERT INTO transactions (tx_date, description, amount, category, category_source, fingerprint)
        VALUES ('2026-04-02', 'Formula Store', 12.34, '=Injected', 'manual', 'reports-export-formula')
        """))
    core_conn.commit()

    response = client.get("/reports/export.csv?period=custom&date_from=2026-04-01&date_to=2026-04-30")
    rows = list(csv.DictReader(io.StringIO(response.get_data(as_text=True))))

    assert response.status_code == 200
    assert response.mimetype == "text/csv"
    assert "reports-overview-" in response.headers["Content-Disposition"]
    assert rows[0]["Section"] == "Summary"
    assert any(row["Label"] == "'=Injected" and row["Spending"] == "12.34" for row in rows)


def test_reports_overview_xlsx_export_returns_workbook(client, core_conn):
    """Verify Reports Excel exports a real workbook package."""
    seed_reporting_data(core_conn)

    response = client.get("/reports/export.xlsx?period=custom&date_from=2026-01-01&date_to=2026-02-28")

    assert response.status_code == 200
    assert response.mimetype == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    assert "reports-overview-" in response.headers["Content-Disposition"]
    with zipfile.ZipFile(io.BytesIO(response.data)) as archive:
        assert "xl/workbook.xml" in archive.namelist()
        worksheet = archive.read("xl/worksheets/sheet1.xml").decode("utf-8")
    assert "Food" in worksheet
    assert "UNKNOWN" in worksheet


def test_reports_taxonomy_route_renders_index_targets(client, core_conn):
    """Verify taxonomy report index renders double-click category and tag targets."""
    seed_reporting_data(core_conn)

    response = client.get("/reports/taxonomy?period=custom&date_from=2026-01-01&date_to=2026-01-31")
    body = response_html(response)

    assert response.status_code == 200
    assert_visible_text(response, "Category reports", "Tag reports", "Food", "Tax")
    assert 'data-row-href="/reports/categories/' in body
    assert 'data-row-href="/reports/tags/' in body
    assert '<a href="/reports/categories/' not in body
    assert '<a href="/reports/tags/' not in body
    assert_not_visible_text(response, "Edit category", "Approve selected", "Recategorize selected")


def test_reports_account_and_merchant_routes_render_entity_indexes(client, core_conn, data_factory):
    """Verify account and merchant report indexes render target links."""
    seed = seed_entity_report_data(data_factory, core_conn)

    accounts_response = client.get("/reports/accounts?period=custom&date_from=2026-01-01&date_to=2026-01-31")
    merchants_response = client.get("/reports/merchants?period=custom&date_from=2026-01-01&date_to=2026-01-31")
    accounts_body = response_html(accounts_response)
    merchants_body = response_html(merchants_response)

    assert accounts_response.status_code == 200
    assert merchants_response.status_code == 200
    assert_visible_text(accounts_response, "Account reports", "Personal Checking", "Travel Card", "Checking account")
    assert_visible_text(merchants_response, "Merchant reports", seed["metro_merchant_name"], "Merchant")
    assert f'data-row-href="/reports/accounts/{seed["checking_id"]}' in accounts_body
    assert f'data-row-href="/reports/merchants/{seed["metro_merchant_id"]}' in merchants_body
    assert f'<a href="/reports/accounts/{seed["checking_id"]}' not in accounts_body
    assert f'<a href="/reports/merchants/{seed["metro_merchant_id"]}' not in merchants_body
    assert_not_visible_text(accounts_response, "Edit category", "Approve selected", "Recategorize selected")
    assert_not_visible_text(merchants_response, "Edit category", "Approve selected", "Recategorize selected")


def test_reports_income_route_renders_income_analysis(client, core_conn):
    """Verify income and credits report renders scoped income rows."""
    seed_reporting_data(core_conn)

    response = client.get("/reports/income?period=custom&date_from=2026-01-01&date_to=2026-02-28")
    body = response_html(response)

    assert response.status_code == 200
    assert_visible_text(
        response,
        "Income and credits",
        "Average credit",
        "Income and credit sources",
        "Monthly statement",
        "Account breakdown",
        "Counterparties",
        "Evidence preview",
        "Payroll",
    )
    assert 'href="/comparison?comparison_view=period&amp;analysis_mode=income"' in body
    assert 'href="/reports/income/export.csv?period=custom' not in body
    assert 'href="/reports/income/export.xlsx?period=custom' not in body
    assert_not_visible_text(response, "Metro Grocery", "Cafe Bistro", "Edit category", "Approve selected")


def test_reports_category_and_tag_detail_routes_render_read_only_reports(client, core_conn):
    """Verify category and tag detail pages render scoped read-only reports."""
    seed_reporting_data(core_conn)
    food_id = category_id(core_conn, "Food")
    tax_id = tag_id(core_conn, "Tax")

    category_response = client.get(
        f"/reports/categories/{food_id}?period=custom&date_from=2026-01-01&date_to=2026-01-31"
    )
    tag_response = client.get(f"/reports/tags/{tax_id}?period=custom&date_from=2026-01-01&date_to=2026-01-31")

    assert category_response.status_code == 200
    assert tag_response.status_code == 200
    assert_visible_text(
        category_response,
        "Category report",
        "Food",
        "Tag composition",
        "Counterparties",
        "Evidence preview",
        "Metro Grocery",
        "Cafe Bistro",
    )
    assert_visible_text(
        tag_response,
        "Tag report",
        "Tax",
        "Category composition",
        "Tag reports are non-exclusive, so one transaction can appear in more than one tag report.",
        "Tax-tag exports emphasize the filtered evidence rows for year-end review.",
    )
    assert_not_visible_text(category_response, "Edit category", "Approve selected", "Recategorize selected")
    assert_not_visible_text(tag_response, "Edit category", "Approve selected", "Recategorize selected")
    assert_has_element(
        category_response,
        "a",
        attrs={"href": "/comparison?comparison_view=period&analysis_mode=spending&period_categories=Food"},
        text="Compare",
    )
    assert_has_element(
        tag_response,
        "a",
        attrs={"href": "/comparison?comparison_view=period&analysis_mode=spending&period_tags=Tax"},
        text="Compare",
    )


def test_reports_account_and_merchant_detail_routes_render_read_only_reports(client, core_conn, data_factory):
    """Verify account and merchant detail pages render scoped read-only reports."""
    seed = seed_entity_report_data(data_factory, core_conn)

    account_response = client.get(
        f"/reports/accounts/{seed['checking_id']}"
        f"?period=custom&date_from=2026-01-01&date_to=2026-01-31&account_id={seed['card_id']}"
    )
    merchant_response = client.get(
        f"/reports/merchants/{seed['metro_merchant_id']}"
        "?period=custom&date_from=2026-01-01&date_to=2026-01-31"
        f"&merchant_id={seed['cafe_merchant_id']}&merchant_query={quote_plus(seed['cafe_merchant_name'])}"
    )

    assert account_response.status_code == 200
    assert merchant_response.status_code == 200
    assert_visible_text(
        account_response,
        "Account report",
        "Personal Checking",
        "Checking account",
        "Counterparties",
        "Evidence preview",
        seed["metro_merchant_name"],
        seed["cafe_merchant_name"],
        "Payroll",
    )
    assert_visible_text(
        merchant_response,
        "Merchant report",
        seed["metro_merchant_name"],
        "Account breakdown",
        "Evidence preview",
        "Personal Checking",
        "Metro Grocery",
    )
    assert_not_visible_text(
        account_response, "Hotel Stay", "Edit category", "Approve selected", "Recategorize selected"
    )
    assert_not_visible_text(
        merchant_response, "Cafe Bistro", "Edit category", "Approve selected", "Recategorize selected"
    )


def test_reports_reimbursable_tag_route_renders_tracking_panel(client, core_conn, data_factory):
    """Verify Reimbursable tag details include reimbursement tracking without edit controls."""
    expense_id = data_factory.transactions.create(
        description="Conference hotel",
        amount=Decimal("1000.00"),
        tx_date="2026-01-09",
        category="Travel",
        transaction_kind=TRANSACTION_KIND_EXPENSE,
        needs_review=0,
        tags=["Reimbursable"],
    )
    reimbursement_id = data_factory.transactions.create(
        description="Employer reimbursement",
        amount=Decimal("-400.00"),
        tx_date="2026-01-20",
        category=REIMBURSEMENT_CATEGORY,
        transaction_kind=TRANSACTION_KIND_INCOME,
        needs_review=0,
    )
    create_reimbursement_allocation(reimbursement_id, expense_id, Decimal("400.00"), conn=core_conn)
    reimbursable_id = tag_id(core_conn, "Reimbursable")

    response = client.get(f"/reports/tags/{reimbursable_id}?period=custom&date_from=2026-01-01&date_to=2026-01-31")

    assert response.status_code == 200
    assert_visible_text(
        response,
        "Reimbursable expense tracking",
        "Gross reimbursable spending",
        "Matched reimbursements",
        "Pending reimbursement",
        "400.00",
        "600.00",
    )
    assert_not_visible_text(response, "Match selected", "Save match", "Complete expense")


def test_reports_taxonomy_csv_export_uses_target_filename_and_rows(client, core_conn):
    """Verify taxonomy detail CSV exports use the active target and filters."""
    seed_reporting_data(core_conn)
    tax_id = tag_id(core_conn, "Tax")

    response = client.get(f"/reports/tags/{tax_id}/export.csv?period=custom&date_from=2026-01-01&date_to=2026-01-31")
    rows = list(csv.DictReader(io.StringIO(response.get_data(as_text=True))))

    assert response.status_code == 200
    assert response.mimetype == "text/csv"
    assert "reports-tag-tax-" in response.headers["Content-Disposition"]
    assert any(row["Section"] == "Evidence" and row["Label"] == "Metro Grocery" for row in rows)


def test_reports_income_csv_export_uses_income_filename_and_rows(client, core_conn):
    """Verify income CSV exports use the income target and filtered evidence rows."""
    seed_reporting_data(core_conn)

    response = client.get("/reports/income/export.csv?period=custom&date_from=2026-01-01&date_to=2026-02-28")
    rows = list(csv.DictReader(io.StringIO(response.get_data(as_text=True))))

    assert response.status_code == 200
    assert response.mimetype == "text/csv"
    assert "reports-income-and-credits-" in response.headers["Content-Disposition"]
    assert any(row["Section"] == "Evidence" and row["Label"] == "Payroll" for row in rows)
    assert not any(row["Label"] == "Metro Grocery" for row in rows)


def test_reports_merchant_csv_export_uses_target_filename_and_rows(client, core_conn, data_factory):
    """Verify merchant detail CSV exports use the path target and active filters."""
    seed = seed_entity_report_data(data_factory, core_conn)

    response = client.get(
        f"/reports/merchants/{seed['metro_merchant_id']}/export.csv"
        "?period=custom&date_from=2026-01-01&date_to=2026-01-31"
        f"&merchant_id={seed['cafe_merchant_id']}&merchant_query={quote_plus(seed['cafe_merchant_name'])}"
    )
    rows = list(csv.DictReader(io.StringIO(response.get_data(as_text=True))))

    assert response.status_code == 200
    assert response.mimetype == "text/csv"
    assert "reports-merchant-metro-grocery-" in response.headers["Content-Disposition"]
    assert any(row["Section"] == "Evidence" and row["Label"] == "Metro Grocery" for row in rows)
    assert any(row["Section"] == "Account" and row["Label"] == "Personal Checking" for row in rows)
    assert not any(row["Label"] == "Cafe Bistro" for row in rows)
