"""Service-level context tests for dashboard pages."""

from tests.support.context_services import (
    quick_view_count,
    seed_dashboard_review_queue_data,
    seed_dashboard_spending_only,
    seed_dashboard_unknown_only,
    seed_reimbursable_dashboard_data,
    seed_reporting_data,
)
from tests.support.database import insert_account, insert_merchant, insert_transaction
from werkzeug.datastructures import MultiDict

from finance_app.modules.dashboard.service import build_dashboard_context


def test_dashboard_context_totals_filters_custom_dates_and_sorting(app, core_conn):
    """Verify dashboard context totals, filters, custom ranges, and sorting."""
    seed_reporting_data(core_conn)
    args = MultiDict(
        [
            ("period", "custom"),
            ("date_from", "2026-01-01"),
            ("date_to", "2026-02-28"),
        ]
    )

    with app.test_request_context("/dashboard"):
        context = build_dashboard_context(args)

    assert context["selected_period"] == "custom"
    assert context["period_label"] == "01-Jan-2026 to 28-Feb-2026"
    assert context["quick_view"] == "categorized"
    assert [(option["value"], option["active"]) for option in context["quick_view_options"]] == [
        ("categorized", True),
        ("needs_review", False),
        ("unknown", False),
        ("all", False),
    ]
    assert context["total_spending"] == 260.00
    assert context["total_income"] == 1000.00
    assert context["net_cashflow"] == 740.00
    assert context["transaction_count"] == 4
    assert context["uncategorized_count"] == 0
    assert context["data_quality"]["transaction_count"] == 5
    assert context["data_quality"]["quality_score"] == 80
    assert context["data_quality"]["review_label"] == "Review 1 transaction needing review"
    assert context["data_quality"]["level"] == "warning"
    assert "quick_view=categorized" not in context["data_quality"]["categorized_url"]
    insights = context["dashboard_insights"]
    assert insights["average_transaction_amount"] == 315.00
    assert insights["untagged_spending_count"] == 0
    assert insights["untagged_spending_rate"] == 0.0
    assert insights["verified_count"] == 0
    assert insights["verified_rate"] == 0.0
    assert insights["top_source"]["label"] == "Rule"
    assert insights["top_source"]["count"] == 3
    assert insights["top_source"]["rate"] == 75.0
    assert context["dashboard_report_links"]["overview"].startswith(
        "/reports?period=custom&date_from=2026-01-01&date_to=2026-02-28"
    )
    assert context["dashboard_report_links"]["income"].startswith(
        "/reports/income?period=custom&date_from=2026-01-01&date_to=2026-02-28"
    )
    assert "measure=income" in context["dashboard_report_links"]["income"]


def test_dashboard_context_quick_views_and_dimension_filters(app, core_conn):
    """Verify dashboard quick views and always-visible dimension filters."""
    seed_reporting_data(core_conn)

    with app.test_request_context("/dashboard"):
        unknown_context = build_dashboard_context(
            MultiDict(
                [
                    ("period", "custom"),
                    ("date_from", "2026-01-01"),
                    ("date_to", "2026-02-28"),
                    ("quick_view", "unknown"),
                ]
            )
        )
        food_context = build_dashboard_context(
            MultiDict(
                [
                    ("period", "custom"),
                    ("date_from", "2026-01-01"),
                    ("date_to", "2026-02-28"),
                    ("categories", "Food"),
                    ("filter_mode", "include"),
                ]
            )
        )
        tax_context = build_dashboard_context(
            MultiDict(
                [
                    ("period", "custom"),
                    ("date_from", "2026-01-01"),
                    ("date_to", "2026-02-28"),
                    ("tags", "Tax"),
                ]
            )
        )
        metro_context = build_dashboard_context(
            MultiDict(
                [
                    ("period", "custom"),
                    ("date_from", "2026-01-01"),
                    ("date_to", "2026-02-28"),
                    ("merchant_query", "  metro   grocery "),
                ]
            )
        )

    assert unknown_context["quick_view"] == "unknown"
    assert unknown_context["total_spending"] == 30.00
    assert unknown_context["transaction_count"] == 1
    assert unknown_context["uncategorized_count"] == 1
    assert quick_view_count(unknown_context, "unknown") == 1

    assert food_context["quick_view"] == "categorized"
    assert food_context["selected_categories"] == ["Food"]
    assert food_context["total_spending"] == 140.00
    assert food_context["transaction_count"] == 2
    assert food_context["dashboard_report_links"]["taxonomy"].startswith(
        "/reports/taxonomy?period=custom&date_from=2026-01-01&date_to=2026-02-28"
    )

    assert tax_context["quick_view"] == "categorized"
    assert tax_context["selected_tags"] == ["Tax"]
    assert tax_context["total_spending"] == 100.00
    assert tax_context["transaction_count"] == 1
    assert tax_context["dashboard_report_links"]["taxonomy"].startswith(
        "/reports/taxonomy?period=custom&date_from=2026-01-01&date_to=2026-02-28"
    )

    assert metro_context["merchant_search"] == "metro grocery"
    assert metro_context["total_spending"] == 100.00
    assert metro_context["transaction_count"] == 1
    assert "search=metro+grocery" in metro_context["dashboard_links"]["transactions"]
    assert "merchant_query=metro+grocery" in metro_context["dashboard_report_links"]["merchants"]


def test_dashboard_context_filters_by_exact_and_partial_merchant(app, core_conn):
    """Verify merchant filters support durable selections and typed text."""
    metro_id = insert_merchant(core_conn, "METRO GROCERY")
    pharmacy_id = insert_merchant(core_conn, "METRO PHARMACY")
    insert_transaction(
        core_conn,
        "Card purchase 1234",
        80.00,
        "Food",
        merchant_id=metro_id,
        tx_date="2026-03-05",
        fingerprint="dashboard-merchant-exact-metro",
        category_source="rule",
        needs_review=0,
    )
    insert_transaction(
        core_conn,
        "Metro Grocery receipt",
        20.00,
        "Food",
        tx_date="2026-03-06",
        fingerprint="dashboard-merchant-partial-description",
        category_source="rule",
        needs_review=0,
    )
    insert_transaction(
        core_conn,
        "UDEM - PAIE payroll",
        15.00,
        "Food",
        tx_date="2026-03-06",
        fingerprint="dashboard-merchant-spaced-description",
        category_source="rule",
        needs_review=0,
    )
    insert_transaction(
        core_conn,
        "Card purchase 5678",
        30.00,
        "Food",
        merchant_id=pharmacy_id,
        tx_date="2026-03-07",
        fingerprint="dashboard-merchant-other-linked",
        category_source="rule",
        needs_review=0,
    )

    with app.test_request_context("/dashboard"):
        exact_context = build_dashboard_context(
            MultiDict(
                [
                    ("period", "custom"),
                    ("date_from", "2026-03-01"),
                    ("date_to", "2026-03-31"),
                    ("merchant_id", str(metro_id)),
                    ("merchant_query", "METRO GROCERY"),
                ]
            )
        )
        partial_context = build_dashboard_context(
            MultiDict(
                [
                    ("period", "custom"),
                    ("date_from", "2026-03-01"),
                    ("date_to", "2026-03-31"),
                    ("merchant_query", "metro grocery"),
                ]
            )
        )
        spaced_context = build_dashboard_context(
            MultiDict(
                [
                    ("period", "custom"),
                    ("date_from", "2026-03-01"),
                    ("date_to", "2026-03-31"),
                    ("merchant_query", "UDEM PAIE"),
                ]
            )
        )

    assert exact_context["selected_merchant_id"] == metro_id
    assert exact_context["selected_merchant_label"] == "METRO GROCERY"
    assert exact_context["merchant_query"] == "METRO GROCERY"
    assert exact_context["total_spending"] == 80.00
    assert exact_context["transaction_count"] == 1
    assert f"merchant_id={metro_id}" in exact_context["dashboard_report_links"]["merchants"]
    assert "merchant_query=METRO+GROCERY" in exact_context["dashboard_report_links"]["merchants"]

    assert partial_context["selected_merchant_id"] is None
    assert partial_context["selected_merchant_label"] == "metro grocery"
    assert partial_context["merchant_query"] == "metro grocery"
    assert partial_context["total_spending"] == 100.00
    assert partial_context["transaction_count"] == 2
    assert "merchant_query=metro+grocery" in partial_context["dashboard_report_links"]["merchants"]

    assert spaced_context["selected_merchant_id"] is None
    assert spaced_context["selected_merchant_label"] == "UDEM PAIE"
    assert spaced_context["merchant_query"] == "UDEM PAIE"
    assert spaced_context["total_spending"] == 15.00
    assert spaced_context["transaction_count"] == 1
    assert "merchant_query=UDEM+PAIE" in spaced_context["dashboard_report_links"]["merchants"]


def test_dashboard_context_filters_reporting_by_account(app, core_conn):
    """Verify dashboard totals and drill-down URLs can be scoped to one account."""
    card_id = insert_account(core_conn, "Rewards Visa", account_type="credit_card")
    checking_id = insert_account(core_conn, "Daily Checking")
    insert_transaction(
        core_conn,
        "Metro Grocery",
        80.00,
        "Food",
        account_id=card_id,
        tx_date="2026-01-05",
        fingerprint="dashboard-account-card-food",
        category_source="rule",
        needs_review=0,
    )
    insert_transaction(
        core_conn,
        "Payroll",
        -1000.00,
        "Income",
        account_id=card_id,
        tx_date="2026-01-06",
        transaction_kind="income",
        fingerprint="dashboard-account-card-income",
        category_source="rule",
        needs_review=0,
    )
    insert_transaction(
        core_conn,
        "Checking Store",
        400.00,
        "Food",
        account_id=checking_id,
        tx_date="2026-01-05",
        fingerprint="dashboard-account-checking-food",
        category_source="rule",
        needs_review=0,
    )
    args = MultiDict(
        [
            ("period", "custom"),
            ("date_from", "2026-01-01"),
            ("date_to", "2026-01-31"),
            ("account_id", str(card_id)),
        ]
    )

    with app.test_request_context("/dashboard"):
        context = build_dashboard_context(args)

    assert context["selected_account_id"] == card_id
    assert {account["name"] for account in context["account_options"]} == {"Daily Checking", "Rewards Visa"}
    assert context["total_spending"] == 80.00
    assert context["total_income"] == 1000.00
    assert context["transaction_count"] == 2
    assert f"account_id={card_id}" in context["dashboard_links"]["transactions"]
    assert f"account_id={card_id}" in context["dashboard_report_links"]["accounts"]
    assert f"account_id={card_id}" in context["dashboard_report_links"]["income"]


def test_dashboard_tag_cashflow_includes_tagged_transfer_credits(app, core_conn):
    """Verify tagged dashboard cash flow nets reimbursed transfer credits."""
    seed_reimbursable_dashboard_data(core_conn)
    date_args = [
        ("period", "custom"),
        ("date_from", "2026-03-01"),
        ("date_to", "2026-03-31"),
    ]

    with app.test_request_context("/dashboard"):
        untagged_context = build_dashboard_context(MultiDict(date_args))
        reimbursable_context = build_dashboard_context(MultiDict([*date_args, ("tags", "Reimbursable")]))

    assert untagged_context["total_spending"] == 300.00
    assert untagged_context["total_income"] == 0
    assert untagged_context["net_cashflow"] == -300.00
    assert untagged_context["transaction_count"] == 1

    assert reimbursable_context["quick_view"] == "categorized"
    assert reimbursable_context["selected_tags"] == ["Reimbursable"]
    assert reimbursable_context["total_spending"] == 300.00
    assert reimbursable_context["total_income"] == 250.00
    assert reimbursable_context["net_cashflow"] == -50.00
    assert reimbursable_context["transaction_count"] == 2
    assert "amount_type=credit" in reimbursable_context["dashboard_links"]["income"]
    assert "measure=income" in reimbursable_context["dashboard_report_links"]["income"]


def test_dashboard_context_handles_empty_database(app):
    """Verify dashboard context is coherent when no transactions exist."""
    with app.test_request_context("/dashboard"):
        context = build_dashboard_context(MultiDict())

    assert context["total_spending"] == 0
    assert context["total_income"] == 0
    assert context["net_cashflow"] == 0
    assert context["transaction_count"] == 0
    assert context["uncategorized_count"] == 0
    assert context["cash_flow_summary"]["savings_rate"] is None
    assert context["cash_flow_summary"]["savings_rate_label"] == "n/a"
    assert context["data_quality"]["level"] == "empty"
    assert context["data_quality"]["message"] == "No transactions in this view."
    assert context["dashboard_report_links"]["overview"] == "/reports?period=ytd"
    assert context["dashboard_report_links"]["income"] == "/reports/income?period=ytd&measure=income"


def test_dashboard_context_handles_zero_income_savings_rate(app, core_conn):
    """Verify spending-only views do not divide by zero for savings rate."""
    seed_dashboard_spending_only(core_conn)
    args = MultiDict(
        [
            ("period", "custom"),
            ("date_from", "2026-06-01"),
            ("date_to", "2026-06-30"),
        ]
    )

    with app.test_request_context("/dashboard"):
        context = build_dashboard_context(args)

    assert context["total_spending"] == 25.00
    assert context["total_income"] == 0
    assert context["net_cashflow"] == -25.00
    assert context["cash_flow_summary"]["status"] == "deficit"
    assert context["cash_flow_summary"]["savings_rate"] is None
    assert context["cash_flow_summary"]["savings_detail"] == "No income in this view."


def test_dashboard_context_handles_all_unknown_quick_view(app, core_conn):
    """Verify all-UNKNOWN views expose quality risk without category rows."""
    seed_dashboard_unknown_only(core_conn)
    args = MultiDict(
        [
            ("period", "custom"),
            ("date_from", "2026-07-01"),
            ("date_to", "2026-07-31"),
            ("quick_view", "unknown"),
        ]
    )

    with app.test_request_context("/dashboard"):
        context = build_dashboard_context(args)

    assert context["quick_view"] == "unknown"
    assert context["total_spending"] == 60.00
    assert context["transaction_count"] == 2
    assert context["uncategorized_count"] == 2
    assert context["data_quality"]["level"] == "danger"
    assert context["data_quality"]["review_label"] == "Review 2 transactions needing review"
    assert quick_view_count(context, "unknown") == 2


def test_dashboard_review_cta_uses_full_review_queue_count(app, core_conn):
    """Verify dashboard review CTA counts all rows that the Review page will show."""
    seed_dashboard_review_queue_data(core_conn)
    args = MultiDict(
        [
            ("period", "custom"),
            ("date_from", "2026-08-01"),
            ("date_to", "2026-08-31"),
        ]
    )

    with app.test_request_context("/dashboard"):
        context = build_dashboard_context(args)

    assert quick_view_count(context, "unknown") == 1
    assert quick_view_count(context, "needs_review") == 2
    assert context["data_quality"]["review_label"] == "Review 2 transactions needing review"
    assert context["data_quality"]["review_url"] == "/review"
