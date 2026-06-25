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
    assert_no_element,
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
        "Categories and tags",
        "Reportable cash flow",
        "Quick view",
        "Monthly statement",
        "Category breakdown",
        "Tag breakdown",
        "Account breakdown",
        "Merchant breakdown",
        "Categorized",
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
    assert_has_element(
        response,
        "input",
        attrs={"id": "reports-quick-view-categorized", "value": "categorized", "checked": True},
    )
    assert "data-sortable-table" in body
    assert "data-paginated-table" in body
    assert "data-table-search" in body
    assert 'data-row-drilldown="dblclick"' in body
    assert 'data-row-href="/transactions' not in body
    assert 'data-row-href="/reports/categories/' in body
    assert 'data-row-href="/reports/tags/' in body
    assert 'data-row-href="/reports/merchants' in body
    assert 'href="/comparison?comparison_view=period&amp;analysis_mode=spending"' in body
    assert_not_visible_text(response, "UNKNOWN")
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

    response = client.get("/reports/export.xlsx?period=custom&date_from=2026-01-01&date_to=2026-02-28&quick_view=all")

    assert response.status_code == 200
    assert response.mimetype == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    assert "reports-overview-" in response.headers["Content-Disposition"]
    with zipfile.ZipFile(io.BytesIO(response.data)) as archive:
        assert "xl/workbook.xml" in archive.namelist()
        worksheet = archive.read("xl/worksheets/sheet1.xml").decode("utf-8")
    assert "Food" in worksheet
    assert "UNKNOWN" in worksheet


def test_reports_taxonomy_route_renders_index_targets(client, core_conn):
    """Verify taxonomy report index renders the explorer and direct target actions."""
    seed_reporting_data(core_conn)

    response = client.get("/reports/taxonomy?period=custom&date_from=2026-01-01&date_to=2026-01-31")
    body = response_html(response)

    assert response.status_code == 200
    assert_visible_text(
        response,
        "Open a category or tag report...",
        "Pinned reports",
        "Report explorer",
        "All",
        "Categories",
        "Tags",
        "Analytics categories",
        "Has income",
        "Has spending",
        "Food",
        "Tax",
    )
    assert "data-taxonomy-open-control" in body
    assert "data-taxonomy-open-menu" in body
    assert 'role="combobox"' in body
    assert 'data-taxonomy-filter="analytics-categories"' in body
    assert 'data-taxonomy-filter="has-spending"' in body
    assert "data-taxonomy-sort" not in body
    assert "Sort by" not in body
    assert "data-sortable-table" in body
    assert "data-no-export" in body
    assert 'data-sort-column="0"' in body
    assert 'data-sort-column="5"' in body
    assert 'data-row-href="/reports/categories/' in body
    assert 'data-row-href="/reports/tags/' in body
    assert 'class="fw-semibold reports-taxonomy-name-link"' in body
    assert 'href="/reports/categories/' in body
    assert 'href="/reports/tags/' in body
    assert "<th>Type</th>" not in body
    explorer_table = body.split('id="reports-taxonomy-explorer-table"', 1)[1].split("</table>", 1)[0]
    assert "Built-in" not in explorer_table
    assert "reports-tag-swatch" not in explorer_table
    assert "reports-taxonomy-type-badge-category" in explorer_table
    assert "reports-taxonomy-type-badge-tag" in explorer_table
    assert_not_visible_text(response, "Edit category", "Approve selected", "Recategorize selected")

    state_response = client.get(
        "/reports/taxonomy?period=custom&date_from=2026-01-01&date_to=2026-01-31"
        "&taxonomy_filter=tags&taxonomy_search=Tax"
    )
    state_body = response_html(state_response)
    assert_has_element(
        state_response,
        "input",
        attrs={"data-taxonomy-explorer-search": True, "value": "Tax"},
    )
    assert_has_element(
        state_response,
        "button",
        attrs={"data-taxonomy-filter": "tags", "aria-pressed": "true"},
        text="Tags",
    )
    assert "taxonomy_filter=tags" in state_body
    assert "taxonomy_search=Tax" in state_body


def test_reports_account_and_merchant_routes_render_entity_indexes(client, core_conn, data_factory):
    """Verify account and merchant report indexes render target links."""
    seed = seed_entity_report_data(data_factory, core_conn)

    accounts_response = client.get("/reports/accounts?period=custom&date_from=2026-01-01&date_to=2026-01-31")
    merchants_response = client.get("/reports/merchants?period=custom&date_from=2026-01-01&date_to=2026-01-31")
    accounts_body = response_html(accounts_response)
    merchants_body = response_html(merchants_response)

    assert accounts_response.status_code == 200
    assert merchants_response.status_code == 200
    assert_visible_text(
        accounts_response,
        "Open an account report...",
        "Report explorer",
        "Account",
        "Personal Checking",
        "Travel Card",
        "Checking account",
        "Credit card",
        "Has spending",
        "Scope",
        "Category",
        "Tags",
    )
    assert_visible_text(
        merchants_response,
        "Open a merchant report...",
        "Report explorer",
        "Merchant",
        seed["metro_merchant_name"],
        "Has income",
        "Has spending",
        "Scope",
        "Category",
        "Tags",
    )
    assert_no_element(accounts_response, "select", attrs={"id": "reports-account"})
    assert_has_element(accounts_response, "input", attrs={"id": "reports-merchant-search"})
    assert_has_element(accounts_response, "input", attrs={"name": "categories", "value": "Food"})
    assert_has_element(accounts_response, "input", attrs={"name": "tags", "value": "Tax"})
    assert_has_element(merchants_response, "select", attrs={"id": "reports-account"})
    assert_no_element(merchants_response, "input", attrs={"id": "reports-merchant-search"})
    assert_has_element(merchants_response, "input", attrs={"name": "categories", "value": "Food"})
    assert_has_element(merchants_response, "input", attrs={"name": "tags", "value": "Tax"})
    assert f'data-row-href="/reports/accounts/{seed["checking_id"]}' in accounts_body
    assert f'data-row-href="/reports/merchants/{seed["metro_merchant_id"]}' in merchants_body
    assert "data-report-open-control" in accounts_body
    assert "data-report-open-menu" in accounts_body
    assert "data-report-explorer" in accounts_body
    assert 'data-report-filter="checking"' in accounts_body
    assert (
        f'<a class="fw-semibold reports-taxonomy-name-link" href="/reports/accounts/{seed["checking_id"]}'
        in accounts_body
    )
    assert (
        f'<a class="fw-semibold reports-taxonomy-name-link" href="/reports/merchants/{seed["metro_merchant_id"]}'
        in merchants_body
    )
    assert_has_element(
        accounts_response,
        "a",
        attrs={
            "href": f"/reports/accounts/{seed['checking_id']}?period=custom&date_from=2026-01-01&date_to=2026-01-31"
        },
        text="Personal Checking",
    )
    assert_has_element(
        merchants_response,
        "a",
        attrs={
            "href": f"/reports/merchants/{seed['metro_merchant_id']}?period=custom&date_from=2026-01-01&date_to=2026-01-31"
        },
        text=seed["metro_merchant_name"],
    )
    assert_not_visible_text(accounts_response, "Edit category", "Approve selected", "Recategorize selected")
    assert_not_visible_text(merchants_response, "Edit category", "Approve selected", "Recategorize selected")


def test_reports_entity_scope_refiners_are_hidden_outside_categorized_scope(client, core_conn, data_factory):
    """Verify report category and tag refiners are disabled when Scope is not categorized."""
    seed_entity_report_data(data_factory, core_conn)

    response = client.get(
        "/reports/accounts?period=custom&date_from=2026-01-01&date_to=2026-01-31"
        "&quick_view=all&categories=Food&tags=Tax"
    )

    assert response.status_code == 200
    assert_has_element(response, "div", attrs={"data-reports-scope-refiner": True, "class": "d-none"})
    assert_has_element(response, "input", attrs={"name": "categories", "value": "Food", "disabled": True})
    assert_has_element(response, "input", attrs={"name": "tags", "value": "Tax", "disabled": True})


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
        "Merchants",
        "Evidence preview",
        "Payroll",
        "Scope",
        "Category",
        "Tags",
    )
    assert_has_element(response, "select", attrs={"id": "reports-account"})
    assert_has_element(response, "input", attrs={"id": "reports-merchant-search"})
    assert_has_element(response, "input", attrs={"name": "categories", "value": "Food"})
    assert_has_element(response, "input", attrs={"name": "tags", "value": "Tax"})
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
        "Reports",
        "Categories and tags",
        "Category report",
        "Food",
        "Back to categories and tags",
        "Summary",
        "Monthly",
        "Composition",
        "Merchants",
        "Evidence preview",
        "Open all transactions",
        "Related reports",
        "Metro Grocery",
        "Cafe Bistro",
    )
    assert_visible_text(
        tag_response,
        "Reports",
        "Categories and tags",
        "Tag report",
        "Tax",
        "Summary",
        "Monthly",
        "Composition",
        "Merchants",
        "Tag reports are non-exclusive, so one transaction can appear in more than one tag report.",
        "Tax-tag exports emphasize the filtered evidence rows for year-end review.",
    )
    assert_not_visible_text(category_response, "Edit category", "Approve selected", "Recategorize selected")
    assert_not_visible_text(tag_response, "Edit category", "Approve selected", "Recategorize selected")
    assert_not_visible_text(category_response, "Export report")
    assert_not_visible_text(tag_response, "Export report")
    assert_has_element(
        category_response,
        "a",
        attrs={"href": "/comparison?comparison_view=period&analysis_mode=spending&period_categories=Food"},
        text="Compare",
    )
    category_body = response_html(category_response)
    assert 'href="/reports/taxonomy?period=custom&amp;date_from=2026-01-01&amp;date_to=2026-01-31"' in category_body
    assert 'href="/reports/categories/' in category_body
    assert "Rental" in category_body
    assert "Reimbursable" in category_body
    assert "Reimbursement" in category_body
    assert "data-chart-export" not in category_body
    assert "data-chart-export-scope" not in category_body
    assert "data-table-export-toolbar" not in category_body
    assert "data-table-search" not in category_body
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
        "Reports",
        "Accounts",
        "Account report",
        "Personal Checking",
        "Checking account",
        "Summary",
        "Monthly",
        "Composition",
        "Merchants",
        "Evidence preview",
        "Open all transactions",
        "Related reports",
        seed["metro_merchant_name"],
        seed["cafe_merchant_name"],
        "Payroll",
    )
    assert_visible_text(
        merchant_response,
        "Reports",
        "Merchants",
        "Merchant report",
        seed["metro_merchant_name"],
        "Summary",
        "Monthly",
        "Composition",
        "Accounts",
        "Evidence preview",
        "Open all transactions",
        "Related reports",
        "Personal Checking",
        "Metro Grocery",
    )
    assert_not_visible_text(
        account_response, "Hotel Stay", "Edit category", "Approve selected", "Recategorize selected"
    )
    assert_not_visible_text(
        merchant_response, "Cafe Bistro", "Edit category", "Approve selected", "Recategorize selected"
    )
    assert_not_visible_text(account_response, "Export report")
    assert_not_visible_text(merchant_response, "Export report")
    account_body = response_html(account_response)
    merchant_body = response_html(merchant_response)
    assert 'href="/reports/accounts?period=custom&amp;date_from=2026-01-01&amp;date_to=2026-01-31"' in account_body
    assert 'href="/reports/merchants?period=custom&amp;date_from=2026-01-01&amp;date_to=2026-01-31"' in merchant_body
    assert "data-report-target-switcher" in account_body
    assert "data-chart-export" not in account_body
    assert "data-chart-export" not in merchant_body
    assert "data-table-export-toolbar" not in account_body
    assert "data-table-export-toolbar" not in merchant_body
    assert "data-table-search" not in account_body
    assert "data-table-search" not in merchant_body


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
