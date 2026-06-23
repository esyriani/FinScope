"""Service-level context tests for dashboard pages."""

from tests.support.context_services import (
    seed_dashboard_review_queue_data,
    seed_dashboard_spending_only,
    seed_dashboard_unknown_only,
    seed_reimbursable_dashboard_data,
    seed_reporting_data,
)
from tests.support.database import insert_account, insert_merchant, insert_transaction, set_owner_setting
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
    assert [(option["value"], option["active"]) for option in context["classification_scope_options"]] == [
        ("categorized", True),
        ("all", False),
    ]
    assert context["total_spending"] == 290.00
    assert context["total_income"] == 1000.00
    assert context["net_cashflow"] == 710.00
    assert context["transaction_count"] == 5
    assert context["uncategorized_count"] == 1
    assert context["data_quality"]["transaction_count"] == 5
    assert context["data_quality"]["quality_score"] == 80
    assert context["data_quality"]["unknown_count"] == 1
    assert context["data_quality"]["needs_review_count"] == 1
    assert context["data_quality"]["unknown_needs_review_count"] == 1
    assert context["data_quality"]["unknown_review_sentence"] == "1 unknown transaction needs review."
    assert context["data_quality"]["unknown_spending_total"] == 30.00
    assert context["data_quality"]["untagged_spending_total"] == 30.00
    assert [row["label"] for row in context["data_quality"]["source_rows"]] == [
        "Rule",
        "Manual",
        "Similarity",
        "AI",
    ]
    assert context["data_quality"]["review_label"] == "Review 1 transaction needing review"
    assert context["data_quality"]["level"] == "warning"
    assert "quick_view=categorized" not in context["data_quality"]["categorized_url"]
    assert "dashboard_insights" not in context
    assert context["dashboard_report_links"]["overview"].startswith("/reports?period=custom&quick_view=categorized")
    assert "date_from=2026-01-01" in context["dashboard_report_links"]["overview"]
    assert "date_to=2026-02-28" in context["dashboard_report_links"]["overview"]
    assert context["dashboard_report_links"]["income"].startswith(
        "/reports/income?period=custom&quick_view=categorized"
    )
    assert "date_from=2026-01-01" in context["dashboard_report_links"]["income"]
    assert "date_to=2026-02-28" in context["dashboard_report_links"]["income"]
    assert "measure=income" in context["dashboard_report_links"]["income"]
    assert context["dashboard_chart_data"]["netMonthLabels"] == ["2026-01", "2026-02"]
    assert [row["label"] for row in context["top_driver_previews"]["categories"]] == ["Food", "Utilities"]
    assert "UNKNOWN" not in {row["label"] for row in context["top_driver_previews"]["categories"]}


def test_dashboard_context_limits_top_driver_previews_from_settings(app, core_conn):
    """Verify dashboard top-driver previews use the runtime setting."""
    set_owner_setting(core_conn, "dashboard_top_driver_limit", "2")
    merchant_ids = {
        "Alpha Market": insert_merchant(core_conn, "Alpha Market"),
        "Beta Bills": insert_merchant(core_conn, "Beta Bills"),
        "Gamma Travel": insert_merchant(core_conn, "Gamma Travel"),
    }
    for index, (tx_date, merchant_name, amount, category) in enumerate(
        [
            ("2026-04-02", "Alpha Market", 10.00, "Food"),
            ("2026-04-03", "Beta Bills", 20.00, "Utilities"),
            ("2026-04-04", "Gamma Travel", 30.00, "Travel"),
            ("2026-05-02", "Alpha Market", 100.00, "Food"),
            ("2026-05-03", "Beta Bills", 90.00, "Utilities"),
            ("2026-05-04", "Gamma Travel", 80.00, "Travel"),
        ]
    ):
        insert_transaction(
            core_conn,
            merchant_name,
            amount,
            category,
            tx_date=tx_date,
            merchant_id=merchant_ids[merchant_name],
            fingerprint=f"dashboard-driver-limit-{index}",
            category_source="rule",
            needs_review=0,
        )

    with app.test_request_context("/dashboard"):
        context = build_dashboard_context(
            MultiDict(
                [
                    ("period", "custom"),
                    ("date_from", "2026-05-01"),
                    ("date_to", "2026-05-31"),
                ]
            )
        )

    assert [row["label"] for row in context["top_driver_previews"]["categories"]] == ["Food", "Utilities"]
    assert [row["label"] for row in context["top_driver_previews"]["merchants"]] == ["Alpha Market", "Beta Bills"]
    assert len(context["top_driver_previews"]["changes"]) == 2


def test_dashboard_context_quick_views_and_dimension_filters(app, core_conn):
    """Verify dashboard quick views and always-visible dimension filters."""
    seed_reporting_data(core_conn)

    with app.test_request_context("/dashboard"):
        old_unknown_context = build_dashboard_context(
            MultiDict(
                [
                    ("period", "custom"),
                    ("date_from", "2026-01-01"),
                    ("date_to", "2026-02-28"),
                    ("quick_view", "unknown"),
                ]
            )
        )
        categorized_context = build_dashboard_context(
            MultiDict(
                [
                    ("period", "custom"),
                    ("date_from", "2026-01-01"),
                    ("date_to", "2026-02-28"),
                    ("quick_view", "categorized"),
                ]
            )
        )
        ignored_category_context = build_dashboard_context(
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

    assert old_unknown_context["quick_view"] == "categorized"
    assert old_unknown_context["total_spending"] == 290.00
    assert old_unknown_context["transaction_count"] == 5
    assert old_unknown_context["uncategorized_count"] == 1
    assert old_unknown_context["data_quality"]["unknown_count"] == 1

    assert categorized_context["quick_view"] == "categorized"
    assert categorized_context["total_spending"] == 290.00
    assert categorized_context["transaction_count"] == 5
    assert categorized_context["uncategorized_count"] == 1
    assert "quick_view=categorized" not in categorized_context["dashboard_links"]["transactions"]

    assert "selected_categories" not in ignored_category_context
    assert ignored_category_context["quick_view"] == "categorized"
    assert ignored_category_context["total_spending"] == 290.00
    assert ignored_category_context["transaction_count"] == 5
    assert ignored_category_context["dashboard_report_links"]["taxonomy"].startswith(
        "/reports/taxonomy?period=custom&quick_view=categorized"
    )
    assert "date_from=2026-01-01" in ignored_category_context["dashboard_report_links"]["taxonomy"]
    assert "date_to=2026-02-28" in ignored_category_context["dashboard_report_links"]["taxonomy"]

    assert "selected_tags" not in tax_context
    assert tax_context["quick_view"] == "categorized"
    assert tax_context["total_spending"] == 290.00
    assert tax_context["transaction_count"] == 5
    assert tax_context["dashboard_report_links"]["taxonomy"].startswith(
        "/reports/taxonomy?period=custom&quick_view=categorized"
    )
    assert "date_from=2026-01-01" in tax_context["dashboard_report_links"]["taxonomy"]
    assert "date_to=2026-02-28" in tax_context["dashboard_report_links"]["taxonomy"]

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


def test_dashboard_ignores_tag_filters_and_keeps_reportable_cashflow_scope(app, core_conn):
    """Verify Dashboard tag query params do not reintroduce tag analysis filters."""
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
    assert "selected_tags" not in reimbursable_context
    assert reimbursable_context["total_spending"] == 300.00
    assert reimbursable_context["total_income"] == 0
    assert reimbursable_context["net_cashflow"] == -300.00
    assert reimbursable_context["transaction_count"] == 1
    assert "amount_type=credit" not in reimbursable_context["dashboard_links"]["income"]
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
    assert context["dashboard_report_links"]["overview"] == "/reports?period=ytd&quick_view=categorized"
    assert context["dashboard_report_links"]["income"] == (
        "/reports/income?period=ytd&quick_view=categorized&measure=income"
    )


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


def test_dashboard_context_handles_all_unknown_scope(app, core_conn):
    """Verify all-UNKNOWN views expose quality risk without analysis filters."""
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

    assert context["quick_view"] == "categorized"
    assert context["total_spending"] == 60.00
    assert context["transaction_count"] == 2
    assert context["uncategorized_count"] == 2
    assert context["data_quality"]["level"] == "danger"
    assert context["data_quality"]["unknown_count"] == 2
    assert context["data_quality"]["unknown_review_sentence"] == "2 unknown transactions need review."
    assert context["data_quality"]["review_label"] == "Review 2 transactions needing review"


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

    assert context["data_quality"]["unknown_count"] == 1
    assert context["data_quality"]["needs_review_count"] == 2
    assert [(metric["label"], metric["value"]) for metric in context["data_quality"]["readiness_metrics"]] == [
        ("Categorized", "67%"),
        ("Unknown needing review", 1),
        ("Untagged", 3),
    ]
    assert context["data_quality"]["review_label"] == "Review 2 transactions needing review"
    assert context["data_quality"]["review_url"] == "/review"
