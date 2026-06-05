"""Service-level context tests for comparison pages."""

from werkzeug.datastructures import MultiDict

from finance_app.modules.comparison import service as comparison_service
from tests.support.context_services import (
    FixedDate,
    seed_comparison_unknown_warning_data,
    seed_reimbursable_comparison_data,
    seed_reporting_data,
)


def test_comparison_context_year_and_period_metrics(app, core_conn, monkeypatch):
    """Verify comparison context year totals, category filters, and period metrics."""
    seed_reporting_data(core_conn)
    monkeypatch.setattr(comparison_service, "date", FixedDate)
    args = MultiDict(
        [
            ("years", "2025"),
            ("years", "2026"),
            ("baseline_year", "2025"),
            ("period_comparison", "month_last_year"),
            ("period_categories", "Food"),
            ("year_categories", "Food"),
        ]
    )

    with app.test_request_context("/comparison"):
        context = comparison_service.build_comparison_context(args)

    food_comparison = next(
        row for row in context["category_comparison"]
        if row["category"] == "Food"
    )
    period_totals = {
        metric["label"]: metric
        for metric in context["period_comparison"]["totals"]
    }

    assert context["comparison_has_data"] is True
    assert context["available_years"] == [2026, 2025]
    assert context["selected_years"] == [2025, 2026]
    assert context["selected_baseline_year"] == 2025
    assert context["selected_year_categories"] == ["Food"]
    assert context["selected_period_categories"] == ["Food"]
    assert context["monthly_spending"][2025][0] == 80.00
    assert context["monthly_spending"][2026][0] == 140.00
    assert context["monthly_spending_statistics"] == [
        {
            "year": 2025,
            "statistics": {
                "count": 2,
                "total": 140.00,
                "mean": 70.00,
                "median": 70.00,
                "q1": 65.00,
                "q3": 75.00,
                "iqr": 10.00,
                "stdev": 14.14,
                "minimum": 60.00,
                "maximum": 80.00,
                "boxplot": [60.00, 65.00, 70.00, 75.00, 80.00],
            },
            "boxplot": [60.00, 65.00, 70.00, 75.00, 80.00],
        },
        {
            "year": 2026,
            "statistics": {
                "count": 2,
                "total": 340.00,
                "mean": 170.00,
                "median": 170.00,
                "q1": 155.00,
                "q3": 185.00,
                "iqr": 30.00,
                "stdev": 42.43,
                "minimum": 140.00,
                "maximum": 200.00,
                "boxplot": [140.00, 155.00, 170.00, 185.00, 200.00],
            },
            "boxplot": [140.00, 155.00, 170.00, 185.00, 200.00],
        },
    ]
    assert context["monthly_spending_statistics_json"] == context["monthly_spending_statistics"]
    assert food_comparison["totals"] == {2025: 140.00, 2026: 340.00}
    assert food_comparison["changes"][2026]["change"] == 200.00
    assert period_totals["Spending"]["current"] == 200.00
    assert period_totals["Spending"]["previous"] == 60.00
    assert period_totals["Transactions"]["current"] == 1
    assert context["period_comparison"]["category_rows"][0]["category"] == "Food"
    assert context["period_comparison"]["merchant_rows"][0]["merchant"] == "METRO GROCERY"
    insight_groups = context["period_comparison"]["insight_groups"]
    category_insight = context["period_comparison"]["insights"][0]
    activity_insight = next(
        insight for insight in context["period_comparison"]["insights"]
        if insight["label"] == "Transaction activity"
    )
    assert [group["key"] for group in insight_groups] == ["categories", "merchants", "spending"]
    assert insight_groups[0]["insights"][0]["group"] == "categories"
    assert insight_groups[1]["insights"][0]["group"] == "merchants"
    assert insight_groups[2]["insights"][0]["group"] == "spending"
    assert category_insight["visual"] == "comparison"
    assert category_insight["group"] == "categories"
    assert category_insight["tone"] == "danger"
    assert category_insight["icon"] == "bi-graph-up-arrow"
    assert category_insight["title"] == "Food"
    assert category_insight["summary"] == "+140.00 $"
    assert category_insight["previous_width"] == 30.0
    assert category_insight["current_width"] == 100.0
    assert activity_insight["visual"] == "activity"
    assert activity_insight["tone"] == "accent"
    assert activity_insight["stat_items"][2]["label"] == "Average"


def test_comparison_context_filters_year_and_period_by_tags(app, core_conn, monkeypatch):
    """Verify comparison contexts can be filtered by transaction tags."""
    seed_reporting_data(core_conn)
    monkeypatch.setattr(comparison_service, "date", FixedDate)
    args = MultiDict(
        [
            ("years", "2025"),
            ("years", "2026"),
            ("period_comparison", "month_last_year"),
            ("period_tags", "Tax"),
            ("year_tags", "Tax"),
        ]
    )

    with app.test_request_context("/comparison"):
        context = comparison_service.build_comparison_context(args)

    food_comparison = next(
        row for row in context["category_comparison"]
        if row["category"] == "Food"
    )
    period_totals = {
        metric["label"]: metric
        for metric in context["period_comparison"]["totals"]
    }

    assert context["selected_year_tags"] == ["Tax"]
    assert context["selected_period_tags"] == ["Tax"]
    assert context["monthly_spending"][2026][0] == 100.00
    assert food_comparison["totals"] == {2025: 60.00, 2026: 300.00}
    assert period_totals["Spending"]["current"] == 200.00
    assert period_totals["Spending"]["previous"] == 60.00
    assert context["period_comparison"]["merchant_rows"][0]["merchant"] == "METRO GROCERY"


def test_comparison_tag_cashflow_includes_tagged_transfer_credits(app, core_conn, monkeypatch):
    """Verify tagged comparison cash flow nets reimbursed transfer credits."""
    seed_reimbursable_comparison_data(core_conn)
    monkeypatch.setattr(comparison_service, "date", FixedDate)
    args = MultiDict(
        [
            ("period_comparison", "month_last_year"),
            ("period_tags", "Reimbursable"),
        ]
    )

    with app.test_request_context("/comparison"):
        context = comparison_service.build_comparison_context(args)

    period_totals = {
        metric["label"]: metric
        for metric in context["period_comparison"]["totals"]
    }

    assert context["selected_period_tags"] == ["Reimbursable"]
    assert period_totals["Spending"]["current"] == 200.00
    assert period_totals["Spending"]["previous"] == 100.00
    assert period_totals["Income and Credits"]["current"] == 150.00
    assert period_totals["Income and Credits"]["previous"] == 80.00
    assert period_totals["Net cash flow"]["current"] == -50.00
    assert period_totals["Net cash flow"]["previous"] == -20.00
    assert period_totals["Transactions"]["current"] == 2
    assert period_totals["Transactions"]["previous"] == 2


def test_comparison_period_transaction_count_excludes_transfers(app, core_conn, monkeypatch):
    """Verify comparison period activity excludes payment and transfer rows."""
    seed_reporting_data(core_conn)
    monkeypatch.setattr(comparison_service, "date", FixedDate)

    with app.test_request_context("/comparison"):
        context = comparison_service.build_comparison_context(
            MultiDict([("period_comparison", "month_last_year")])
        )

    assert context["period_comparison"]["current_transaction_count"] == 3
    assert context["period_comparison"]["previous_transaction_count"] == 3


def test_comparison_context_handles_empty_database(app, monkeypatch):
    """Verify comparison context falls back cleanly when no data exists."""
    monkeypatch.setattr(comparison_service, "date", FixedDate)

    with app.test_request_context("/comparison"):
        context = comparison_service.build_comparison_context(MultiDict())

    assert context["comparison_has_data"] is False
    assert context["available_years"] == []
    assert context["selected_years"] == [2026]
    assert context["selected_baseline_year"] is None
    assert context["category_comparison"] == []
    assert context["monthly_spending"][2026] == [0] * 12
    assert context["monthly_spending_statistics"] == [
        {
            "year": 2026,
            "statistics": {
                "count": 0,
                "total": 0.0,
                "mean": None,
                "median": None,
                "q1": None,
                "q3": None,
                "iqr": None,
                "stdev": None,
                "minimum": None,
                "maximum": None,
                "boxplot": None,
            },
            "boxplot": None,
        }
    ]
    assert context["monthly_spending_json"] == [{"year": 2026, "totals": [0] * 12}]
    assert context["year_unknown_warning"] is None
    assert context["period_comparison"]["current_transaction_count"] == 0
    assert context["period_comparison"]["previous_transaction_count"] == 0


def test_comparison_context_filters_invalid_years_and_falls_back_baseline(app, core_conn, monkeypatch):
    """Verify invalid years are ignored and invalid baselines use previous-year comparisons."""
    seed_reporting_data(core_conn)
    monkeypatch.setattr(comparison_service, "date", FixedDate)

    with app.test_request_context("/comparison"):
        invalid_year_context = comparison_service.build_comparison_context(
            MultiDict([("years", "1999"), ("baseline_year", "1999")])
        )
        fallback_context = comparison_service.build_comparison_context(
            MultiDict(
                [
                    ("years", "2025"),
                    ("years", "2026"),
                    ("baseline_year", "1999"),
                    ("year_categories", "Food"),
                ]
            )
        )

    food_comparison = next(
        row for row in fallback_context["category_comparison"]
        if row["category"] == "Food"
    )
    assert invalid_year_context["selected_years"] == [2026]
    assert invalid_year_context["selected_baseline_year"] is None
    assert fallback_context["selected_years"] == [2025, 2026]
    assert fallback_context["selected_baseline_year"] is None
    assert food_comparison["changes"][2026]["baseline_year"] == 2025
    assert food_comparison["changes"][2026]["change"] == 200.00


def test_comparison_context_warns_when_unknown_exceeds_threshold(app, core_conn, monkeypatch):
    """Verify UNKNOWN warnings fire for year and period comparison contexts."""
    seed_comparison_unknown_warning_data(core_conn)
    monkeypatch.setattr(comparison_service, "date", FixedDate)
    args = MultiDict(
        [
            ("years", "2026"),
            ("period_comparison", "month_previous"),
        ]
    )

    with app.test_request_context("/comparison"):
        context = comparison_service.build_comparison_context(args)

    assert "Category comparison may be unreliable" in context["year_unknown_warning"]["source"]
    assert context["year_unknown_warning"]["values"] == {
        "category": "UNKNOWN",
        "share": "55.0",
    }
    assert "UNKNOWN accounts for 55.0%" in context["year_unknown_warning"]["text"]
    assert "Category insights may be incomplete" in context["period_comparison"]["unknown_warning"]["source"]
    assert context["period_comparison"]["unknown_warning"]["values"] == {
        "category": "UNKNOWN",
        "share": "70.0",
    }
    assert "UNKNOWN accounts for 70.0%" in context["period_comparison"]["unknown_warning"]["text"]
