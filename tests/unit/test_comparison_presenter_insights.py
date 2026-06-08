"""Tests for comparison insight presenter helpers."""

from decimal import Decimal

from finance_app.modules.comparison.presenter import (
    build_period_insights,
    merchant_behavior_insight_candidates,
    robust_anomaly_insight_candidates,
    select_ranked_insight_candidates,
    spending_mix_shift_candidate,
)


def public_fields(insight):
    """Return the card fields that are rendered or already part of the public context."""
    return {
        key: insight[key]
        for key in (
            "label",
            "value",
            "detail",
            "visual",
            "group",
            "tone",
            "icon",
            "title",
            "summary",
            "badge",
        )
    }


def ranked_candidate(title, insight_type, score, absolute_change, direction="increase"):
    """Build a minimal insight candidate for ranked-selection tests."""
    return {
        "title": title,
        "label": title,
        "insight_type": insight_type,
        "score": score,
        "selection_metrics": {
            "metric": "money",
            "absolute_change": absolute_change,
            "direction": direction,
        },
    }


def period_money_row(label_key, label, current, previous):
    """Build a period row fixture for insight presenter tests."""
    change = round(current - previous, 2)
    return {
        label_key: label,
        "current": current,
        "previous": previous,
        "change": change,
        "abs_change": abs(change),
        "percent": None if previous == 0 else round(((current - previous) / previous) * 100, 1),
        "amount_label": f"{change:+.2f} $",
        "percent_label": "n/a" if previous == 0 else f"{((current - previous) / previous) * 100:+.1f}%",
        "direction": "up" if change > 0 else "down" if change < 0 else "flat",
        "state": "new" if current > 0 and previous == 0 else "dropped" if current == 0 and previous > 0 else "changed",
    }


def test_build_period_insights_preserves_card_order_and_public_fields():
    """Verify the refactored presenter keeps existing comparison cards stable."""
    period_row = {
        "current": 200.0,
        "previous": 60.0,
        "change": 140.0,
        "abs_change": 140.0,
        "percent": 233.3,
        "amount_label": "+140.00 $",
        "percent_label": "+233.3%",
        "direction": "up",
        "state": "changed",
    }
    category_rows = [{**period_row, "category": "Food"}]
    merchant_rows = [{**period_row, "merchant": "METRO GROCERY", "category": "Food"}]

    insights = build_period_insights(
        category_rows,
        merchant_rows,
        {"transaction_count": 1, "spending": 200},
        {"transaction_count": 1, "spending": 60},
    )

    assert [insight["label"] for insight in insights] == [
        "Largest category increase",
        "Largest merchant increase",
        "Transaction activity",
    ]
    assert public_fields(insights[0]) == {
        "label": "Largest category increase",
        "value": "Food +140.00 $ (+233.3%)",
        "detail": "Prior: 60.00 $. Current: 200.00 $",
        "visual": "comparison",
        "group": "categories",
        "tone": "danger",
        "icon": "bi-graph-up-arrow",
        "title": "Food",
        "summary": "+140.00 $",
        "badge": "+233.3%",
    }
    assert insights[0]["previous_label"] == "60.00 $"
    assert insights[0]["current_label"] == "200.00 $"
    assert insights[0]["previous_width"] == 30.0
    assert insights[0]["current_width"] == 100.0
    assert insights[0]["insight_type"] == "category_increase"
    assert insights[0]["score"] == 59.04
    assert insights[0]["rank_reason"] == (
        "largest absolute category increase; " "abs=70.0%; percent=100.0%; importance=100.0%; confidence=44.0%"
    )

    assert public_fields(insights[2]) == {
        "label": "Transaction activity",
        "value": "1 transaction",
        "detail": "0 versus prior period. Average transaction: 200.00 $",
        "visual": "activity",
        "group": "spending",
        "tone": "accent",
        "icon": "bi-activity",
        "title": "Transactions",
        "summary": "1",
        "badge": "0",
    }
    assert insights[2]["stat_items"] == [
        {"label": "Current", "value": "1"},
        {"label": "Prior", "value": "1"},
        {"label": "Average", "value": "200.00 $"},
    ]
    assert insights[2]["insight_type"] == "transaction_activity"
    assert insights[2]["score"] == 0.0
    assert insights[2]["rank_reason"] == (
        "transaction count change; " "abs=0.0%; percent=0.0%; importance=100.0%; confidence=44.0%"
    )


def test_build_period_insights_scores_all_existing_candidate_types():
    """Verify every existing insight candidate receives internal scoring fields."""
    category_rows = [
        {
            "category": "Food",
            "current": 200.0,
            "previous": 60.0,
            "change": 140.0,
            "abs_change": 140.0,
            "percent": 233.3,
            "amount_label": "+140.00 $",
            "percent_label": "+233.3%",
            "direction": "up",
            "state": "changed",
        },
        {
            "category": "Travel",
            "current": 20.0,
            "previous": 100.0,
            "change": -80.0,
            "abs_change": 80.0,
            "percent": -80.0,
            "amount_label": "-80.00 $",
            "percent_label": "-80.0%",
            "direction": "down",
            "state": "changed",
        },
        {
            "category": "Books",
            "current": 40.0,
            "previous": 0.0,
            "change": 40.0,
            "abs_change": 40.0,
            "percent": None,
            "amount_label": "+40.00 $",
            "percent_label": "New",
            "direction": "up",
            "state": "new",
        },
        {
            "category": "Fitness",
            "current": 0.0,
            "previous": 80.0,
            "change": -80.0,
            "abs_change": 80.0,
            "percent": -100.0,
            "amount_label": "-80.00 $",
            "percent_label": "Dropped",
            "direction": "down",
            "state": "dropped",
        },
    ]
    merchant_rows = [
        {
            "merchant": "METRO GROCERY",
            "category": "Food",
            "current": 200.0,
            "previous": 60.0,
            "change": 140.0,
            "abs_change": 140.0,
            "percent": 233.3,
            "amount_label": "+140.00 $",
            "percent_label": "+233.3%",
            "direction": "up",
            "state": "changed",
        },
        {
            "merchant": "AIRLINE",
            "category": "Travel",
            "current": 10.0,
            "previous": 100.0,
            "change": -90.0,
            "abs_change": 90.0,
            "percent": -90.0,
            "amount_label": "-90.00 $",
            "percent_label": "-90.0%",
            "direction": "down",
            "state": "changed",
        },
        {
            "merchant": "BOOKSHOP",
            "category": "Books",
            "current": 40.0,
            "previous": 0.0,
            "change": 40.0,
            "abs_change": 40.0,
            "percent": None,
            "amount_label": "+40.00 $",
            "percent_label": "New",
            "direction": "up",
            "state": "new",
        },
        {
            "merchant": "FITNESS CLUB",
            "category": "Fitness",
            "current": 0.0,
            "previous": 80.0,
            "change": -80.0,
            "abs_change": 80.0,
            "percent": -100.0,
            "amount_label": "-80.00 $",
            "percent_label": "Dropped",
            "direction": "down",
            "state": "dropped",
        },
    ]

    insights = build_period_insights(
        category_rows,
        merchant_rows,
        {"transaction_count": 8, "spending": 260},
        {"transaction_count": 6, "spending": 240},
    )

    assert [insight["insight_type"] for insight in insights] == [
        "category_increase",
        "category_decrease",
        "merchant_increase",
        "merchant_decrease",
        "new_spending",
        "dropped_spending",
        "transaction_activity",
    ]
    for insight in insights:
        assert isinstance(insight["score"], float)
        assert insight["rank_reason"]
        assert "abs=" in insight["rank_reason"]
        assert "percent=" in insight["rank_reason"]
        assert "importance=" in insight["rank_reason"]
        assert "confidence=" in insight["rank_reason"]

    assert insights[0]["score"] == 65.58
    assert insights[0]["rank_reason"] == (
        "largest absolute category increase; " "abs=53.8%; percent=100.0%; importance=76.9%; confidence=92.5%"
    )
    assert insights[4]["rank_reason"].startswith("new merchant spending total;")
    assert insights[5]["rank_reason"].startswith("dropped merchant spending total;")
    assert insights[6]["score"] > 0.0


def test_robust_anomaly_insight_candidates_build_plain_language_cards():
    """Verify high and low robust anomaly candidates use existing card fields."""
    category_rows = [
        period_money_row("category", "Food", 220.0, 55.0),
        period_money_row("category", "Travel", 20.0, 190.0),
    ]
    merchant_rows = [
        period_money_row("merchant", "METRO GROCERY", 220.0, 55.0),
        period_money_row("merchant", "AIRLINE", 20.0, 190.0),
    ]

    insights = robust_anomaly_insight_candidates(
        category_rows,
        merchant_rows,
        {
            "Food": [48, 50, 52, 49, 51],
            "Travel": [190, 200, 210, 195, 205],
        },
        {
            "METRO GROCERY": [48, 50, 52, 49, 51],
            "AIRLINE": [190, 200, 210, 195, 205],
        },
    )

    assert [insight["insight_type"] for insight in insights] == [
        "category_spending_high_anomaly",
        "category_spending_low_anomaly",
        "merchant_spending_high_anomaly",
        "merchant_spending_low_anomaly",
    ]
    assert public_fields(insights[0]) == {
        "label": "Unusually high category spending",
        "value": "Food: higher than usual",
        "detail": "Food is 220.00 $ this period; typical recent spending is 50.00 $.",
        "visual": "comparison",
        "group": "categories",
        "tone": "danger",
        "icon": "bi-graph-up-arrow",
        "title": "Food: higher than usual",
        "summary": "+170.00 $",
        "badge": "Higher than usual",
    }
    assert public_fields(insights[1]) == {
        "label": "Unusually low category spending",
        "value": "Travel: lower than usual",
        "detail": "Travel is 20.00 $ this period; typical recent spending is 200.00 $.",
        "visual": "comparison",
        "group": "categories",
        "tone": "success",
        "icon": "bi-graph-down-arrow",
        "title": "Travel: lower than usual",
        "summary": "-180.00 $",
        "badge": "Lower than usual",
    }
    assert "median" not in insights[0]["detail"].casefold()
    assert "mad" not in insights[0]["detail"].casefold()
    assert "z-score" not in insights[0]["detail"].casefold()
    assert insights[0]["robust_anomaly"]["history_count"] == 5
    assert insights[0]["robust_anomaly"]["difference"] == Decimal("170.0")
    assert insights[0]["robust_anomaly"]["is_anomaly"] is True
    assert insights[0]["selection_metrics"]["metric"] == "money"
    assert insights[0]["selection_metrics"]["entity_key"] == "Food"
    assert insights[0]["previous_label"] == "50.00 $"
    assert insights[0]["current_label"] == "220.00 $"


def test_robust_anomaly_insight_candidates_require_five_history_periods():
    """Verify fewer than five historical periods suppresses anomaly candidates."""
    category_rows = [period_money_row("category", "Food", 220.0, 55.0)]

    insights = robust_anomaly_insight_candidates(
        category_rows,
        [],
        {"Food": [48, 50, 52, 49]},
        {},
    )

    assert insights == []


def test_build_period_insights_only_includes_anomalies_in_ranked_mode():
    """Verify robust anomaly cards are opt-in through ranked selection."""
    category_rows = [period_money_row("category", "Food", 220.0, 55.0)]
    merchant_rows = []
    category_history = {"Food": [48, 50, 52, 49, 51]}

    default_insights = build_period_insights(
        category_rows,
        merchant_rows,
        {"transaction_count": 6, "spending": 220},
        {"transaction_count": 6, "spending": 55},
        category_history=category_history,
    )
    ranked_insights = build_period_insights(
        category_rows,
        merchant_rows,
        {"transaction_count": 6, "spending": 220},
        {"transaction_count": 6, "spending": 55},
        category_history=category_history,
        ranked=True,
        ranking_options={"min_score": 0.0, "min_money_change": 0.0, "deduplicate": False},
    )

    assert "category_spending_high_anomaly" not in [insight["insight_type"] for insight in default_insights]
    assert "category_spending_high_anomaly" in [insight["insight_type"] for insight in ranked_insights]


def test_spending_mix_shift_candidate_detects_large_mix_shift():
    """Verify large category share movement creates one stat-card candidate."""
    category_rows = [
        period_money_row("category", "Food", 10.0, 90.0),
        period_money_row("category", "Travel", 90.0, 10.0),
    ]

    insight = spending_mix_shift_candidate(category_rows)

    assert public_fields(insight) == {
        "label": "Spending mix changed",
        "value": "Spending moved across categories",
        "detail": "The largest category share changes are shown below.",
        "visual": "aggregate",
        "group": "spending",
        "tone": "accent",
        "icon": "bi-pie-chart",
        "title": "Category mix changed",
        "summary": "2 categories shifted",
        "badge": "Mix shift",
    }
    assert insight["insight_type"] == "spending_mix_shift"
    assert insight["stat_items"] == [
        {"label": "Category", "value": "Food: -80.0 points"},
        {"label": "Category", "value": "Travel: +80.0 points"},
    ]
    assert insight["mix_shift"]["js_distance"] > 0.5
    assert insight["mix_shift"]["current_total"] == 100.0
    assert insight["mix_shift"]["previous_total"] == 100.0
    assert "jsd=" in insight["rank_reason"]


def test_spending_mix_shift_candidate_suppresses_small_mix_shift():
    """Verify minor category share changes stay below the mix-shift threshold."""
    category_rows = [
        period_money_row("category", "Food", 50.0, 55.0),
        period_money_row("category", "Travel", 50.0, 45.0),
    ]

    insight = spending_mix_shift_candidate(category_rows)

    assert insight is None


def test_spending_mix_shift_candidate_requires_sufficient_spending():
    """Verify low-spending periods do not create mix-shift candidates."""
    category_rows = [
        period_money_row("category", "Food", 30.0, 45.0),
        period_money_row("category", "Travel", 30.0, 15.0),
    ]

    insight = spending_mix_shift_candidate(category_rows)

    assert insight is None


def test_spending_mix_shift_candidate_handles_category_only_in_current_period():
    """Verify a new current-period category contributes to mix-shift details."""
    category_rows = [
        period_money_row("category", "Books", 50.0, 0.0),
        period_money_row("category", "Food", 50.0, 100.0),
    ]

    insight = spending_mix_shift_candidate(category_rows)

    assert insight["insight_type"] == "spending_mix_shift"
    assert insight["stat_items"] == [
        {"label": "Category", "value": "Books: +50.0 points"},
        {"label": "Category", "value": "Food: -50.0 points"},
    ]
    assert insight["mix_shift"]["share_changes"][0]["category"] == "Books"
    assert insight["mix_shift"]["share_changes"][0]["previous_share"] == 0.0
    assert insight["mix_shift"]["share_changes"][0]["current_share"] == 0.5


def test_merchant_behavior_candidates_detect_new_merchant():
    """Verify a current-only merchant creates a new merchant behavior card."""
    merchant_rows = [
        period_money_row("merchant", "NEW BAKERY", 75.0, 0.0),
    ]

    insights = merchant_behavior_insight_candidates(merchant_rows)

    assert [insight["insight_type"] for insight in insights] == ["merchant_new"]
    assert public_fields(insights[0]) == {
        "label": "New merchant activity",
        "value": "NEW BAKERY: new this period",
        "detail": "NEW BAKERY has 75.00 $ in current-period spending and did not appear in the prior period.",
        "visual": "aggregate",
        "group": "merchants",
        "tone": "danger",
        "icon": "bi-plus-circle",
        "title": "New merchant",
        "summary": "75.00 $",
        "badge": "New",
    }
    assert insights[0]["stat_items"] == [
        {"label": "Merchant", "value": "NEW BAKERY"},
        {"label": "Current", "value": "75.00 $"},
        {"label": "Prior", "value": "0.00 $"},
    ]
    assert insights[0]["merchant_behavior"] == {
        "behavior": "new",
        "merchant": "NEW BAKERY",
        "current": 75.0,
        "previous": 0.0,
    }


def test_merchant_behavior_candidates_detect_dropped_merchant():
    """Verify a prior-only merchant creates a missing merchant behavior card."""
    merchant_rows = [
        period_money_row("merchant", "OLD SUBSCRIPTION", 0.0, 120.0),
    ]

    insights = merchant_behavior_insight_candidates(merchant_rows)

    assert [insight["insight_type"] for insight in insights] == ["merchant_dropped"]
    assert public_fields(insights[0]) == {
        "label": "Missing merchant activity",
        "value": "OLD SUBSCRIPTION: missing this period",
        "detail": "OLD SUBSCRIPTION had 120.00 $ in prior-period spending and is missing from the current period.",
        "visual": "aggregate",
        "group": "merchants",
        "tone": "success",
        "icon": "bi-dash-circle",
        "title": "Missing merchant",
        "summary": "120.00 $",
        "badge": "Missing",
    }
    assert insights[0]["stat_items"] == [
        {"label": "Merchant", "value": "OLD SUBSCRIPTION"},
        {"label": "Current", "value": "0.00 $"},
        {"label": "Prior", "value": "120.00 $"},
    ]
    assert insights[0]["merchant_behavior"]["behavior"] == "dropped"


def test_merchant_behavior_candidates_detect_resurrected_merchant():
    """Verify a merchant returning after a long absence is identified separately."""
    merchant_rows = [
        period_money_row("merchant", "SEASONAL SHOP", 90.0, 0.0),
    ]
    activity_history = {
        "SEASONAL SHOP": {
            "history_count": 2,
            "last_activity_months_ago": 6,
            "last_activity_label": "2025-11",
            "periods": [],
        },
    }

    insights = merchant_behavior_insight_candidates(merchant_rows, activity_history)

    assert [insight["insight_type"] for insight in insights] == ["merchant_resurrected"]
    assert public_fields(insights[0]) == {
        "label": "Merchant returned",
        "value": "SEASONAL SHOP: returned after a gap",
        "detail": "SEASONAL SHOP returned with 90.00 $ after 6 months without spending.",
        "visual": "aggregate",
        "group": "merchants",
        "tone": "accent",
        "icon": "bi-arrow-clockwise",
        "title": "Merchant returned",
        "summary": "90.00 $",
        "badge": "Returned",
    }
    assert insights[0]["stat_items"] == [
        {"label": "Merchant", "value": "SEASONAL SHOP"},
        {"label": "Current", "value": "90.00 $"},
        {"label": "Last seen", "value": "6 months ago"},
    ]
    assert insights[0]["merchant_behavior"]["behavior"] == "resurrected"
    assert insights[0]["merchant_behavior"]["last_activity_months_ago"] == 6


def test_merchant_behavior_candidates_detect_major_rank_increase():
    """Verify a merchant moving materially up the spending rank creates a card."""
    merchant_rows = [
        period_money_row("merchant", "METRO GROCERY", 200.0, 20.0),
        period_money_row("merchant", "AIRLINE", 150.0, 180.0),
        period_money_row("merchant", "HOTEL", 100.0, 160.0),
        period_money_row("merchant", "HYDRO", 90.0, 140.0),
        period_money_row("merchant", "PHARMACY", 80.0, 130.0),
    ]

    insights = merchant_behavior_insight_candidates(merchant_rows)

    assert [insight["insight_type"] for insight in insights] == ["merchant_rank_increase"]
    assert public_fields(insights[0]) == {
        "label": "Merchant moved up",
        "value": "METRO GROCERY: rank increased",
        "detail": "METRO GROCERY moved from rank #5 to #1 by spending.",
        "visual": "aggregate",
        "group": "merchants",
        "tone": "danger",
        "icon": "bi-arrow-up-right-circle",
        "title": "Merchant rank increased",
        "summary": "4 places",
        "badge": "Moved up",
    }
    assert insights[0]["stat_items"] == [
        {"label": "Merchant", "value": "METRO GROCERY"},
        {"label": "Current rank", "value": "#1"},
        {"label": "Prior rank", "value": "#5"},
        {"label": "Current", "value": "200.00 $"},
    ]
    assert insights[0]["merchant_behavior"]["behavior"] == "rank_increase"
    assert insights[0]["merchant_behavior"]["rank_change"] == 4


def test_select_ranked_insight_candidates_orders_high_change_before_small_change():
    """Verify ranked selection orders candidates by deterministic score."""
    small_change = ranked_candidate("Small increase", "category_increase", 20.0, 20.0)
    high_change = ranked_candidate("High increase", "merchant_increase", 80.0, 500.0)

    selected = select_ranked_insight_candidates(
        [small_change, high_change],
        min_score=0.0,
        min_money_change=0.0,
    )

    assert selected == [high_change, small_change]


def test_select_ranked_insight_candidates_suppresses_tiny_baseline_percent_spike():
    """Verify tiny money changes are filtered even when their score is high."""
    tiny_spike = ranked_candidate("Tiny spike", "category_increase", 45.0, 0.99)
    meaningful_change = ranked_candidate("Meaningful change", "category_increase", 15.0, 10.0)

    selected = select_ranked_insight_candidates([tiny_spike, meaningful_change])

    assert selected == [meaningful_change]


def test_select_ranked_insight_candidates_deduplicates_duplicate_increases():
    """Verify similar increase cards keep only the highest-scored candidate."""
    category_card = ranked_candidate("Groceries", "category_increase", 60.0, 120.0)
    merchant_card = ranked_candidate("Groceries", "merchant_increase", 80.0, 140.0)

    selected = select_ranked_insight_candidates(
        [category_card, merchant_card],
        min_score=0.0,
        min_money_change=0.0,
    )

    assert selected == [merchant_card]


def test_select_ranked_insight_candidates_respects_max_card_count():
    """Verify ranked selection returns no more than the requested card count."""
    candidates = [
        ranked_candidate(f"Candidate {index}", "category_increase", score, score)
        for index, score in enumerate([50.0, 40.0, 30.0, 20.0, 10.0], start=1)
    ]

    selected = select_ranked_insight_candidates(
        candidates,
        max_count=3,
        min_score=0.0,
        min_money_change=0.0,
    )

    assert selected == candidates[:3]
