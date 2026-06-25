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
    explorer_rows = rows_by_label(context["entity_explorer_rows"])
    assert context["active_report_section"].key == REPORT_ACCOUNTS
    assert context["entity_index_title"] == "Account reports"
    assert context["entity_open_label"] == "Open an account report..."
    assert context["entity_search_placeholder"] == "Search accounts"
    assert context["total_spending"] == 340.00
    assert context["total_income"] == 1000.00
    assert account_rows["Personal Checking"]["spending"] == 140.00
    assert account_rows["Personal Checking"]["income"] == 1000.00
    assert account_rows["Personal Checking"]["type_label"] == "Checking account"
    assert account_rows["Personal Checking"]["url"].startswith(f"/reports/accounts/{seed['checking_id']}")
    assert account_rows["Personal Checking"]["transactions_url"].startswith("/transactions")
    assert account_rows["Personal Checking"]["comparison_url"].startswith("/comparison")
    assert "checking" in account_rows["Personal Checking"]["filter_tokens"]
    assert explorer_rows["Personal Checking"] == account_rows["Personal Checking"]
    assert account_rows["Travel Card"]["spending"] == 200.00
    assert account_rows["Travel Card"]["type_label"] == "Credit card"
    assert any(option["label"] == "Personal Checking" for option in context["entity_target_options"])
    assert [chip["value"] for chip in context["entity_filter_chips"]] == [
        "all",
        "checking",
        "credit-card",
        "has-income",
        "has-spending",
    ]


def test_account_index_ignores_account_filter_and_applies_categorized_taxonomy_filters(app, core_conn, data_factory):
    """Verify Accounts ignores account filters but honors categorized category/tag refiners."""
    seed = seed_entity_report_data(data_factory, core_conn)

    with app.test_request_context("/reports/accounts"):
        context = build_reports_context(
            REPORT_ACCOUNTS,
            entity_args(
                ("account_id", str(seed["card_id"])),
                ("quick_view", "categorized"),
                ("categories", "Food"),
                ("tags", "Tax"),
            ),
        )

    account_rows = rows_by_label(context["entity_rows"])
    assert context["reports_show_account_filter"] is False
    assert context["reports_taxonomy_filter_controls_available"] is True
    assert context["reports_taxonomy_filter_controls_visible"] is True
    assert context["selected_categories"] == ["Food"]
    assert context["selected_tags"] == ["Tax"]
    assert context["total_spending"] == 100.00
    assert account_rows["Personal Checking"]["spending"] == 100.00
    assert "Travel Card" not in account_rows
    assert "categories=Food" in context["transaction_url"]
    assert "tags=Tax" in context["transaction_url"]
    assert f"account_id={seed['card_id']}" not in context["transaction_url"]


def test_account_index_ignores_taxonomy_refiners_outside_categorized_scope(app, core_conn, data_factory):
    """Verify hidden category and tag refiners do not constrain non-categorized scopes."""
    seed_entity_report_data(data_factory, core_conn)

    with app.test_request_context("/reports/accounts"):
        context = build_reports_context(
            REPORT_ACCOUNTS,
            entity_args(
                ("quick_view", "all"),
                ("categories", "Food"),
                ("tags", "Tax"),
            ),
        )

    account_rows = rows_by_label(context["entity_rows"])
    assert context["reports_taxonomy_filter_controls_visible"] is False
    assert context["total_spending"] == 340.00
    assert account_rows["Personal Checking"]["spending"] == 140.00
    assert account_rows["Travel Card"]["spending"] == 200.00
    assert "categories=Food" not in context["transaction_url"]
    assert "tags=Tax" not in context["transaction_url"]


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
    assert context["entity_breadcrumbs"][1]["label"] == "Accounts"
    assert context["entity_back_url"].startswith("/reports/accounts")
    assert [item["target"] for item in context["entity_detail_subnav"]] == [
        "entity-summary",
        "entity-monthly",
        "entity-composition",
        "entity-merchants",
        "entity-transactions",
    ]
    assert any(option["label"] == "Travel Card" for option in context["entity_target_options"])
    assert any(link["label"] == "Open related merchant report" for link in context["entity_related_links"])
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
    assert context["entity_open_label"] == "Open a merchant report..."
    assert context["entity_search_placeholder"] == "Search merchants"
    assert merchant_rows[seed["metro_merchant_name"]]["spending"] == 100.00
    assert merchant_rows[seed["metro_merchant_name"]]["type_label"] == "Merchant"
    assert merchant_rows[seed["metro_merchant_name"]]["url"].startswith(
        f"/reports/merchants/{seed['metro_merchant_id']}"
    )
    assert merchant_rows[seed["metro_merchant_name"]]["transactions_url"].startswith("/transactions")
    assert merchant_rows[seed["metro_merchant_name"]]["comparison_url"].startswith("/comparison")
    assert merchant_rows[seed["hotel_merchant_name"]]["spending"] == 200.00
    assert merchant_rows[seed["payroll_merchant_name"]]["income"] == 1000.00
    assert any(option["label"] == seed["metro_merchant_name"] for option in context["entity_target_options"])
    assert [chip["value"] for chip in context["entity_filter_chips"]] == ["all", "has-income", "has-spending"]


def test_merchant_index_ignores_merchant_filter(app, core_conn, data_factory):
    """Verify Merchants ignores stale merchant filters that are not shown on the page."""
    seed = seed_entity_report_data(data_factory, core_conn)

    with app.test_request_context("/reports/merchants"):
        context = build_reports_context(
            REPORT_MERCHANTS,
            entity_args(
                ("merchant_id", str(seed["cafe_merchant_id"])),
                ("merchant_query", seed["cafe_merchant_name"]),
            ),
        )

    merchant_rows = rows_by_label(context["entity_rows"])
    assert context["reports_show_merchant_filter"] is False
    assert merchant_rows[seed["metro_merchant_name"]]["spending"] == 100.00
    assert merchant_rows[seed["cafe_merchant_name"]]["spending"] == 40.00
    assert merchant_rows[seed["hotel_merchant_name"]]["spending"] == 200.00


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
    assert context["entity_breadcrumbs"][1]["label"] == "Merchants"
    assert context["entity_back_url"].startswith("/reports/merchants")
    assert [item["target"] for item in context["entity_detail_subnav"]] == [
        "entity-summary",
        "entity-monthly",
        "entity-composition",
        "entity-accounts",
        "entity-transactions",
    ]
    assert any(option["label"] == seed["cafe_merchant_name"] for option in context["entity_target_options"])
    assert any(link["label"] == "Open related account report" for link in context["entity_related_links"])
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
