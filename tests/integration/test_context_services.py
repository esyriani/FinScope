"""Service-level context tests for dashboard pages."""

from sqlalchemy import text
from werkzeug.datastructures import MultiDict

from finance_app.modules.categories.taxonomy import set_transaction_tags
from finance_app.modules.dashboard.service import build_dashboard_context
from tests.support.context_services import (
    category_totals,
    merchant_totals,
    quick_view_count,
    seed_dashboard_period_delta_data,
    seed_dashboard_review_queue_data,
    seed_dashboard_spending_only,
    seed_dashboard_unknown_only,
    seed_reimbursable_dashboard_data,
    seed_reporting_data,
)


def test_dashboard_context_totals_filters_custom_dates_and_sorting(app, core_conn):
    """Verify dashboard context totals, filters, custom ranges, and sorting."""
    seed_reporting_data(core_conn)
    args = MultiDict(
        [
            ("period", "custom"),
            ("date_from", "2026-01-01"),
            ("date_to", "2026-02-28"),
            ("category_sort", "category"),
            ("category_direction", "asc"),
            ("merchant_sort", "spending"),
            ("merchant_direction", "desc"),
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
    assert category_totals(context) == {
        "Food": 140.00,
        "Utilities": 120.00,
    }
    assert list(merchant_totals(context).items()) == [
        ("HYDRO QUEBEC", 120.00),
        ("METRO GROCERY", 100.00),
    ]


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
                    ("merchant_search", "  metro   grocery "),
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
    assert category_totals(food_context) == {"Food": 140.00}
    assert list(merchant_totals(food_context).items()) == [
        ("METRO GROCERY", 100.00),
        ("CAFE BISTRO", 40.00),
    ]

    assert tax_context["quick_view"] == "categorized"
    assert tax_context["selected_tags"] == ["Tax"]
    assert tax_context["total_spending"] == 100.00
    assert tax_context["transaction_count"] == 1
    assert category_totals(tax_context) == {"Food": 100.00}
    assert list(merchant_totals(tax_context).items()) == [("METRO GROCERY", 100.00)]

    assert metro_context["merchant_search"] == "metro grocery"
    assert metro_context["total_spending"] == 100.00
    assert metro_context["transaction_count"] == 1
    assert category_totals(metro_context) == {"Food": 100.00}
    assert list(merchant_totals(metro_context).items()) == [("METRO GROCERY", 100.00)]
    assert "search=metro+grocery" in metro_context["dashboard_links"]["transactions"]


def test_dashboard_context_tag_breakdown_counts_each_matching_tag(app, core_conn):
    """Verify tag breakdown uses tag-associated spending, including overlaps."""
    seed_reporting_data(core_conn)
    cafe_id = core_conn.execute(text("""
        SELECT id
        FROM transactions
        WHERE fingerprint = 'seed-2026-food-cafe'
        """)).fetchone()._mapping["id"]
    set_transaction_tags(core_conn, cafe_id, ["Shared", "Tax"], source="manual")
    core_conn.commit()
    args = MultiDict(
        [
            ("period", "custom"),
            ("date_from", "2026-01-01"),
            ("date_to", "2026-02-28"),
            ("quick_view", "all"),
            ("breakdown", "tag"),
        ]
    )

    with app.test_request_context("/dashboard"):
        context = build_dashboard_context(args)
        untagged_context = build_dashboard_context(
            MultiDict(
                [
                    ("period", "custom"),
                    ("date_from", "2026-01-01"),
                    ("date_to", "2026-02-28"),
                    ("quick_view", "all"),
                    ("breakdown", "tag"),
                    ("show_untagged", "1"),
                ]
            )
        )

    assert context["breakdown_mode"] == "tag"
    assert context["quick_view"] == "all"
    assert context["breakdown_is_tag"] is True
    assert context["show_untagged"] is False
    assert "show_untagged=1" in context["show_untagged_url"]
    assert context["breakdown_chart_title"] == "Spending by tag"
    assert context["breakdown_table_title"] == "Tag detail"
    assert context["breakdown_label"] == "Tag"
    assert context["total_spending"] == 290.00
    assert category_totals(context) == {
        "Government": 120.00,
        "Shared": 40.00,
        "Tax": 140.00,
    }
    assert sum(category_totals(context).values()) > context["total_spending"]
    assert context["category_labels"] == ["Tax", "Government", "Shared"]
    tax_row = next(row for row in context["category_rows"] if row["category"] == "Tax")
    assert "tags=Tax" in tax_row["url"]
    assert "amount_type=spending" in tax_row["url"]
    assert all(row["category"] != "Untagged" for row in context["category_rows"])

    assert untagged_context["show_untagged"] is True
    assert "show_untagged" not in untagged_context["show_untagged_url"]
    assert category_totals(untagged_context) == {
        "Government": 120.00,
        "Shared": 40.00,
        "Tax": 140.00,
        "Untagged": 30.00,
    }
    assert untagged_context["category_labels"] == [
        "Tax",
        "Government",
        "Shared",
        "Untagged",
    ]
    untagged_row = next(row for row in untagged_context["category_rows"] if row["category"] == "Untagged")
    assert untagged_row["url"] == ""


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
    assert reimbursable_context["income_month_totals"] == [250.00]
    assert reimbursable_context["net_month_totals"] == [-50.00]
    assert category_totals(reimbursable_context) == {"Travel": 300.00}
    assert "amount_type=credit" in reimbursable_context["dashboard_links"]["income"]


def test_dashboard_breakdown_hides_income_category_by_default(app, core_conn):
    """Verify dashboard category and tag breakdowns only show income when requested."""
    seed_reporting_data(core_conn)
    income_tx_id = core_conn.execute(text("""
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
        VALUES (
            '2026-01-09',
            'Misclassified income category expense',
            25.00,
            'Income',
            0,
            'manual',
            0,
            'expense',
            'dashboard-income-category-expense'
        )
        """)).lastrowid
    set_transaction_tags(core_conn, income_tx_id, ["Shared"], source="manual")
    core_conn.commit()
    date_args = [
        ("period", "custom"),
        ("date_from", "2026-01-01"),
        ("date_to", "2026-02-28"),
        ("quick_view", "all"),
    ]

    with app.test_request_context("/dashboard"):
        category_context = build_dashboard_context(MultiDict(date_args))
        income_context = build_dashboard_context(MultiDict([*date_args, ("show_income", "1")]))
        tag_context = build_dashboard_context(MultiDict([*date_args, ("breakdown", "tag")]))
        income_tag_context = build_dashboard_context(
            MultiDict([*date_args, ("breakdown", "tag"), ("show_income", "1")])
        )

    assert category_context["show_income"] is False
    assert "show_income=1" in category_context["show_income_url"]
    assert "Income" not in category_totals(category_context)
    assert category_totals(income_context)["Income"] == 25.00
    assert "show_income" not in income_context["show_income_url"]
    assert category_totals(tag_context)["Shared"] == 40.00
    assert category_totals(income_tag_context)["Shared"] == 65.00


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
    assert context["category_rows"] == []
    assert context["merchant_rows"] == []


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
    assert context["category_rows"] == []
    assert context["category_labels"] == []
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


def test_dashboard_context_calculates_previous_period_merchant_deltas(app, core_conn):
    """Verify merchant rows include current versus prior rolling-period deltas."""
    seed_dashboard_period_delta_data(core_conn)
    args = MultiDict(
        [
            ("period", "month"),
            ("merchant_sort", "merchant"),
            ("merchant_direction", "asc"),
        ]
    )

    with app.test_request_context("/dashboard"):
        context = build_dashboard_context(args)

    merchants = {row["merchant"]: row for row in context["merchant_rows"]}
    assert merchants["METRO GROCERY"]["total"] == 150.00
    assert merchants["METRO GROCERY"]["period_change"]["label"] == "+50%"
    assert merchants["METRO GROCERY"]["period_change"]["direction"] == "up"
    assert merchants["METRO GROCERY"]["period_change"]["sort_value"] == 50
    assert merchants["NEW BAKERY"]["period_change"]["label"] == "n/a"
    assert merchants["NEW BAKERY"]["period_change"]["detail"] == "No comparison"
