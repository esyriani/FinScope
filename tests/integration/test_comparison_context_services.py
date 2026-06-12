"""Service-level context tests for comparison pages."""

from sqlalchemy import text
from tests.support.context_services import (
    FixedDate,
    seed_comparison_unknown_warning_data,
    seed_reimbursable_comparison_data,
    seed_reporting_data,
)
from tests.support.database import insert_account, insert_merchant, insert_transaction
from werkzeug.datastructures import MultiDict

from finance_app.core.constants import TRANSACTION_KIND_INCOME
from finance_app.modules.comparison import service as comparison_service


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

    food_comparison = next(row for row in context["category_comparison"] if row["category"] == "Food")
    period_totals = {metric["label"]: metric for metric in context["period_comparison"]["totals"]}

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
    assert [group["key"] for group in insight_groups] == ["categories", "merchants"]
    assert insight_groups[0]["insights"][0]["group"] == "categories"
    assert insight_groups[1]["insights"][0]["group"] == "merchants"
    assert [insight["score"] for insight in context["period_comparison"]["insights"]] == sorted(
        [insight["score"] for insight in context["period_comparison"]["insights"]],
        reverse=True,
    )
    assert category_insight["visual"] == "comparison"
    assert category_insight["group"] == "categories"
    assert category_insight["tone"] == "danger"
    assert category_insight["icon"] == "bi-graph-up-arrow"
    assert category_insight["title"] == "Food"
    assert category_insight["value"] == "Food +140.00 $ (+233.3%)"
    assert category_insight["detail"] == "Prior: 60.00 $. Current: 200.00 $"
    assert category_insight["summary"] == "+140.00 $"
    assert category_insight["badge"] == "+233.3%"
    assert category_insight["previous_label"] == "60.00 $"
    assert category_insight["current_label"] == "200.00 $"
    assert category_insight["previous_width"] == 30.0
    assert category_insight["current_width"] == 100.0
    assert category_insight["insight_type"] == "category_increase"
    assert category_insight["score"] == 59.04
    assert category_insight["rank_reason"] == (
        "largest absolute category increase; " "abs=70.0%; percent=100.0%; importance=100.0%; confidence=44.0%"
    )
    assert all(insight["score"] >= 10.0 for insight in context["period_comparison"]["insights"])


def test_period_comparison_ranked_insights_include_robust_anomaly_candidates(app, core_conn, monkeypatch):
    """Verify ranked period insights can use historical monthly anomaly inputs."""
    monkeypatch.setattr(comparison_service, "date", FixedDate)
    rows = [
        ("2025-12-02", "Metro Grocery", 48.00, "Food", "comparison-anomaly-history-1"),
        ("2026-01-02", "Metro Grocery", 50.00, "Food", "comparison-anomaly-history-2"),
        ("2026-02-02", "Metro Grocery", 52.00, "Food", "comparison-anomaly-history-3"),
        ("2026-03-02", "Metro Grocery", 49.00, "Food", "comparison-anomaly-history-4"),
        ("2026-04-02", "Metro Grocery", 51.00, "Food", "comparison-anomaly-history-5"),
        ("2026-05-02", "Metro Grocery", 220.00, "Food", "comparison-anomaly-current"),
    ]
    core_conn.execute(
        text("""
        INSERT INTO transactions (tx_date, description, amount, category, category_source, fingerprint)
        VALUES (:p0, :p1, :p2, :p3, 'rule', :p4)
        """),
        [dict(zip(("p0", "p1", "p2", "p3", "p4"), row)) for row in rows],
    )
    core_conn.commit()

    with app.test_request_context("/comparison"):
        period_context = comparison_service.build_period_comparison(
            core_conn,
            "month_previous",
            [],
            [],
            "UNKNOWN",
            20,
            ranked_insights=True,
            insight_ranking_options={
                "min_score": 0.0,
                "min_money_change": 0.0,
            },
        )

    insight_types = [insight["insight_type"] for insight in period_context["insights"]]
    category_anomaly = next(
        insight for insight in period_context["insights"] if insight["insight_type"] == "category_spending_high_anomaly"
    )
    merchant_anomaly = next(
        insight for insight in period_context["insights"] if insight["insight_type"] == "merchant_spending_high_anomaly"
    )

    assert "category_spending_high_anomaly" in insight_types
    assert "merchant_spending_high_anomaly" in insight_types
    assert category_anomaly["label"] == "Unusually high category spending"
    assert category_anomaly["title"] == "Food: higher than usual"
    assert category_anomaly["detail"] == "Food is 220.00 $ this period; typical recent spending is 50.00 $."
    assert category_anomaly["robust_anomaly"]["history_count"] == 5
    assert category_anomaly["robust_anomaly"]["is_anomaly"] is True
    assert merchant_anomaly["title"] == "METRO GROCERY: higher than usual"


def test_comparison_context_applies_insight_card_limit_setting(app, core_conn, monkeypatch):
    """Verify the comparison page uses the runtime insight-card limit."""
    seed_reporting_data(core_conn)
    monkeypatch.setattr(comparison_service, "date", FixedDate)
    core_conn.execute(text("""
        UPDATE user_settings
        SET value = '1'
        WHERE key = 'comparison_insight_card_limit'
          AND user_id = (SELECT id FROM users WHERE username = 'owner')
        """))
    core_conn.commit()

    args = MultiDict(
        [
            ("period_comparison", "month_last_year"),
            ("period_categories", "Food"),
        ]
    )

    with app.test_request_context("/comparison"):
        context = comparison_service.build_comparison_context(args)

    assert len(context["period_comparison"]["insights"]) == 1
    assert context["comparison_insight_card_limit"] == 1


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

    food_comparison = next(row for row in context["category_comparison"] if row["category"] == "Food")
    period_totals = {metric["label"]: metric for metric in context["period_comparison"]["totals"]}

    assert context["selected_year_tags"] == ["Tax"]
    assert context["selected_period_tags"] == ["Tax"]
    assert context["monthly_spending"][2026][0] == 100.00
    assert food_comparison["totals"] == {2025: 60.00, 2026: 300.00}
    assert period_totals["Spending"]["current"] == 200.00
    assert period_totals["Spending"]["previous"] == 60.00
    assert context["period_comparison"]["merchant_rows"][0]["merchant"] == "METRO GROCERY"


def test_comparison_context_filters_year_and_period_by_account(app, core_conn, monkeypatch):
    """Verify comparison analytics can be scoped to one account."""
    card_id = insert_account(core_conn, "Rewards Visa", account_type="credit_card")
    checking_id = insert_account(core_conn, "Daily Checking")
    for row in [
        (card_id, "2025-05-02", "Metro Grocery", 60.00, "comparison-account-card-2025"),
        (card_id, "2026-04-02", "Metro Grocery", 25.00, "comparison-account-card-prior"),
        (card_id, "2026-05-02", "Metro Grocery", 80.00, "comparison-account-card-current"),
        (checking_id, "2025-05-02", "Checking Store", 900.00, "comparison-account-checking-2025"),
        (checking_id, "2026-04-02", "Checking Store", 700.00, "comparison-account-checking-prior"),
        (checking_id, "2026-05-02", "Checking Store", 600.00, "comparison-account-checking-current"),
    ]:
        insert_transaction(
            core_conn,
            row[2],
            row[3],
            "Food",
            account_id=row[0],
            tx_date=row[1],
            fingerprint=row[4],
            category_source="rule",
            needs_review=0,
        )
    monkeypatch.setattr(comparison_service, "date", FixedDate)
    args = MultiDict(
        [
            ("years", "2025"),
            ("years", "2026"),
            ("baseline_year", "2025"),
            ("period_comparison", "month_previous"),
            ("account_id", str(card_id)),
        ]
    )

    with app.test_request_context("/comparison"):
        context = comparison_service.build_comparison_context(args)

    food_comparison = next(row for row in context["category_comparison"] if row["category"] == "Food")
    period_totals = {metric["label"]: metric for metric in context["period_comparison"]["totals"]}

    assert context["selected_account_id"] == card_id
    assert context["selected_account_name"] == "Rewards Visa"
    assert {account["name"] for account in context["account_options"]} == {"Daily Checking", "Rewards Visa"}
    assert context["available_years"] == [2026, 2025]
    assert context["monthly_spending"][2025][4] == 60.00
    assert context["monthly_spending"][2026][3] == 25.00
    assert context["monthly_spending"][2026][4] == 80.00
    assert food_comparison["totals"] == {2025: 60.00, 2026: 105.00}
    assert period_totals["Spending"]["current"] == 80.00
    assert period_totals["Spending"]["previous"] == 25.00
    assert period_totals["Transactions"]["current"] == 1
    assert context["period_comparison"]["merchant_rows"][0]["merchant"] == "METRO GROCERY"
    assert "Account: Rewards Visa" in context["period_filter_context"]
    assert f"account_id={card_id}" in context["period_clear_url"]
    assert f"account_id={card_id}" in context["year_clear_url"]


def test_comparison_context_filters_year_and_period_by_merchant(app, core_conn, monkeypatch):
    """Verify comparison analytics can be scoped by exact or typed merchant."""
    metro_id = insert_merchant(core_conn, "METRO GROCERY")
    pharmacy_id = insert_merchant(core_conn, "METRO PHARMACY")
    for merchant_id, tx_date, amount, fingerprint in [
        (metro_id, "2025-05-02", 60.00, "comparison-merchant-metro-2025"),
        (metro_id, "2026-04-02", 25.00, "comparison-merchant-metro-prior"),
        (metro_id, "2026-05-02", 80.00, "comparison-merchant-metro-current"),
        (pharmacy_id, "2025-05-02", 900.00, "comparison-merchant-pharmacy-2025"),
        (pharmacy_id, "2026-04-02", 700.00, "comparison-merchant-pharmacy-prior"),
        (pharmacy_id, "2026-05-02", 600.00, "comparison-merchant-pharmacy-current"),
    ]:
        insert_transaction(
            core_conn,
            "Card purchase",
            amount,
            "Food",
            merchant_id=merchant_id,
            tx_date=tx_date,
            fingerprint=fingerprint,
            category_source="rule",
            needs_review=0,
        )
    insert_transaction(
        core_conn,
        "Metro Grocery receipt",
        20.00,
        "Food",
        tx_date="2026-05-03",
        fingerprint="comparison-merchant-partial-description",
        category_source="rule",
        needs_review=0,
    )
    for description, amount, tx_date, fingerprint in [
        ("UDEM - PAIE payroll", 15.00, "2026-05-04", "comparison-merchant-spaced-current"),
        ("UDEM PAIE tuition", 7.00, "2026-04-04", "comparison-merchant-spaced-previous"),
        ("UDEM PAIE prior year", 5.00, "2025-05-04", "comparison-merchant-spaced-year"),
    ]:
        insert_transaction(
            core_conn,
            description,
            amount,
            "Food",
            tx_date=tx_date,
            fingerprint=fingerprint,
            category_source="rule",
            needs_review=0,
        )
    monkeypatch.setattr(comparison_service, "date", FixedDate)

    with app.test_request_context("/comparison"):
        exact_context = comparison_service.build_comparison_context(
            MultiDict(
                [
                    ("years", "2025"),
                    ("years", "2026"),
                    ("baseline_year", "2025"),
                    ("period_comparison", "month_previous"),
                    ("merchant_id", str(metro_id)),
                    ("merchant_query", "METRO GROCERY"),
                ]
            )
        )
        partial_context = comparison_service.build_comparison_context(
            MultiDict(
                [
                    ("years", "2025"),
                    ("years", "2026"),
                    ("baseline_year", "2025"),
                    ("period_comparison", "month_previous"),
                    ("merchant_query", "metro grocery"),
                ]
            )
        )
        spaced_context = comparison_service.build_comparison_context(
            MultiDict(
                [
                    ("years", "2025"),
                    ("years", "2026"),
                    ("baseline_year", "2025"),
                    ("period_comparison", "month_previous"),
                    ("merchant_query", "UDEM PAIE"),
                ]
            )
        )

    exact_food = next(row for row in exact_context["category_comparison"] if row["category"] == "Food")
    exact_period_totals = {metric["label"]: metric for metric in exact_context["period_comparison"]["totals"]}
    partial_food = next(row for row in partial_context["category_comparison"] if row["category"] == "Food")
    partial_period_totals = {metric["label"]: metric for metric in partial_context["period_comparison"]["totals"]}
    spaced_food = next(row for row in spaced_context["category_comparison"] if row["category"] == "Food")
    spaced_period_totals = {metric["label"]: metric for metric in spaced_context["period_comparison"]["totals"]}

    assert exact_context["selected_merchant_id"] == metro_id
    assert exact_context["selected_merchant_label"] == "METRO GROCERY"
    assert exact_context["available_years"] == [2026, 2025]
    assert exact_context["monthly_spending"][2025][4] == 60.00
    assert exact_context["monthly_spending"][2026][3] == 25.00
    assert exact_context["monthly_spending"][2026][4] == 80.00
    assert exact_food["totals"] == {2025: 60.00, 2026: 105.00}
    assert exact_period_totals["Spending"]["current"] == 80.00
    assert exact_period_totals["Spending"]["previous"] == 25.00
    assert exact_context["period_comparison"]["merchant_rows"][0]["merchant"] == "METRO GROCERY"
    assert "Merchant: METRO GROCERY" in exact_context["period_filter_context"]
    assert "Merchant: METRO GROCERY" in exact_context["year_filter_context"]
    assert f"merchant_id={metro_id}" in exact_context["period_clear_url"]
    assert "merchant_query=METRO+GROCERY" in exact_context["period_clear_url"]
    assert f"merchant_id={metro_id}" in exact_context["year_clear_url"]
    assert "merchant_query=METRO+GROCERY" in exact_context["year_clear_url"]

    assert partial_context["selected_merchant_id"] is None
    assert partial_context["selected_merchant_label"] == "metro grocery"
    assert partial_food["totals"] == {2025: 60.00, 2026: 125.00}
    assert partial_period_totals["Spending"]["current"] == 100.00
    assert partial_period_totals["Spending"]["previous"] == 25.00
    assert "Merchant: metro grocery" in partial_context["period_filter_context"]
    assert "merchant_query=metro+grocery" in partial_context["period_clear_url"]

    assert spaced_context["selected_merchant_id"] is None
    assert spaced_context["selected_merchant_label"] == "UDEM PAIE"
    assert spaced_food["totals"] == {2025: 5.00, 2026: 22.00}
    assert spaced_period_totals["Spending"]["current"] == 15.00
    assert spaced_period_totals["Spending"]["previous"] == 7.00
    assert "Merchant: UDEM PAIE" in spaced_context["period_filter_context"]
    assert "merchant_query=UDEM+PAIE" in spaced_context["period_clear_url"]


def test_comparison_context_income_mode_supports_income_only_merchant(app, core_conn, monkeypatch):
    """Verify income-mode comparison populates details for income-only merchants."""
    merchant_id = insert_merchant(core_conn, "UDEM PAIE")
    for tx_date, amount, fingerprint in [
        ("2025-05-04", -800.00, "comparison-income-merchant-2025"),
        ("2026-04-04", -900.00, "comparison-income-merchant-prior"),
        ("2026-05-04", -1200.00, "comparison-income-merchant-current"),
    ]:
        insert_transaction(
            core_conn,
            "Payroll deposit",
            amount,
            "Income",
            merchant_id=merchant_id,
            tx_date=tx_date,
            fingerprint=fingerprint,
            category_source="rule",
            needs_review=0,
            transaction_kind=TRANSACTION_KIND_INCOME,
        )
    insert_transaction(
        core_conn,
        "Bookstore expense noise",
        75.00,
        "Education",
        tx_date="2026-05-05",
        fingerprint="comparison-income-merchant-expense-noise",
        category_source="rule",
        needs_review=0,
    )
    monkeypatch.setattr(comparison_service, "date", FixedDate)

    with app.test_request_context("/comparison"):
        context = comparison_service.build_comparison_context(
            MultiDict(
                [
                    ("years", "2025"),
                    ("years", "2026"),
                    ("baseline_year", "2025"),
                    ("period_comparison", "month_previous"),
                    ("merchant_query", "UDEM PAIE"),
                    ("analysis_mode", "income"),
                ]
            )
        )

    income_comparison = next(row for row in context["category_comparison"] if row["category"] == "Income")
    period_totals = {metric["label"]: metric for metric in context["period_comparison"]["totals"]}
    period_income = context["period_comparison"]["category_rows"][0]
    merchant_income = context["period_comparison"]["merchant_rows"][0]

    assert context["selected_analysis_mode"] == "income"
    assert context["selected_analysis_mode_option"]["noun"] == "income and credits"
    assert context["monthly_spending"][2025][4] == 800.00
    assert context["monthly_spending"][2026][3] == 900.00
    assert context["monthly_spending"][2026][4] == 1200.00
    assert income_comparison["totals"] == {2025: 800.00, 2026: 2100.00}
    assert income_comparison["changes"][2026]["change"] == 1300.00
    assert period_totals["Spending"]["current"] == 0.00
    assert period_totals["Income and Credits"]["current"] == 1200.00
    assert period_totals["Income and Credits"]["previous"] == 900.00
    assert period_totals["Income and Credits"]["tone"] == "success"
    assert period_income["category"] == "Income"
    assert period_income["current"] == 1200.00
    assert period_income["previous"] == 900.00
    assert period_income["tone"] == "success"
    assert merchant_income["merchant"] == "UDEM PAIE"
    assert merchant_income["current"] == 1200.00
    assert merchant_income["previous"] == 900.00
    assert merchant_income["tone"] == "success"
    assert "Analysis: income and credits" in context["period_filter_context"]
    assert "analysis_mode=income" in context["period_clear_url"]


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

    period_totals = {metric["label"]: metric for metric in context["period_comparison"]["totals"]}

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
        context = comparison_service.build_comparison_context(MultiDict([("period_comparison", "month_last_year")]))

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

    food_comparison = next(row for row in fallback_context["category_comparison"] if row["category"] == "Food")
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
