"""Tests for shared analytics helper calculations."""

import pytest

from finance_app.core.analytics import (
    QUICK_VIEW_ALL,
    QUICK_VIEW_CATEGORIZED,
    QUICK_VIEW_UNKNOWN,
    build_cash_flow_summary,
    build_data_quality,
    build_quick_view_options,
    percentage,
)


def quality_summary(**overrides):
    """Build a complete data-quality summary mapping for helper tests."""
    summary = {
        "transaction_count": 10,
        "categorized_count": 10,
        "uncategorized_count": 0,
        "unknown_needs_review_count": 0,
        "needs_review_count": 0,
        "untagged_count": 0,
        "untagged_spending_total": 0,
        "unknown_spending_total": 0,
        "unknown_income_total": 0,
        "manually_reviewed_count": 0,
        "rule_count": 0,
        "history_count": 0,
        "ai_count": 0,
        "manual_source_count": 0,
    }
    summary.update(overrides)
    return summary


def test_build_quick_view_options_keeps_active_zero_count_view():
    """Verify empty quick-view options still include the selected view."""
    active_view = (QUICK_VIEW_UNKNOWN + "-request")[: -len("-request")]

    options = build_quick_view_options(
        active_view,
        {
            "categorized_count": 0,
            "needs_review_count": 0,
            "unknown_count": 0,
            "all_count": 0,
        },
    )

    assert options == [
        {
            "value": QUICK_VIEW_UNKNOWN,
            "label": "Unknown",
            "count": 0,
            "active": True,
        }
    ]


def test_build_quick_view_options_marks_only_the_active_view():
    """Verify non-empty quick views preserve counts and one active flag."""
    options = build_quick_view_options(
        QUICK_VIEW_CATEGORIZED,
        {
            "categorized_count": 3,
            "needs_review_count": 0,
            "unknown_count": 1,
            "all_count": 4,
        },
    )

    assert [(option["value"], option["count"], option["active"]) for option in options] == [
        (QUICK_VIEW_CATEGORIZED, 3, True),
        (QUICK_VIEW_UNKNOWN, 1, False),
        (QUICK_VIEW_ALL, 4, False),
    ]


@pytest.mark.parametrize(
    ("total_income", "total_spending", "expected"),
    [
        (
            1000.00,
            250.00,
            {
                "status": "surplus",
                "net_cashflow": 750.00,
                "savings_rate": 75.0,
                "savings_rate_label": "75.0%",
                "savings_detail": "Spending is 25.0% of income.",
            },
        ),
        (
            1000.00,
            1000.00,
            {
                "status": "balanced",
                "net_cashflow": 0.00,
                "savings_rate": 0.0,
                "savings_rate_label": "0.0%",
                "savings_detail": "Spending is 100.0% of income.",
            },
        ),
        (
            250.00,
            300.00,
            {
                "status": "deficit",
                "net_cashflow": -50.00,
                "savings_rate": -20.0,
                "savings_rate_label": "-20.0%",
                "savings_detail": "Spending is 120.0% of income.",
            },
        ),
        (
            0.00,
            12.34,
            {
                "status": "deficit",
                "net_cashflow": -12.34,
                "savings_rate": None,
                "savings_rate_label": "n/a",
                "savings_detail": "No income in this view.",
            },
        ),
    ],
)
def test_build_cash_flow_summary_calculates_status_and_rates(total_income, total_spending, expected):
    """Verify cash-flow summary arithmetic and boundary status labels."""
    summary = build_cash_flow_summary(total_income, total_spending)

    for key, expected_value in expected.items():
        assert summary[key] == expected_value


@pytest.mark.parametrize(
    ("summary", "expected_level", "expected_message"),
    [
        (
            quality_summary(transaction_count=0, categorized_count=0),
            "empty",
            "No transactions in this view.",
        ),
        (
            quality_summary(transaction_count=12, categorized_count=9, needs_review_count=3),
            "danger",
            "3 of 12 transactions need review. Category-level charts may be misleading.",
        ),
        (
            quality_summary(transaction_count=20, categorized_count=18, uncategorized_count=2),
            "warning",
            "2 of 20 transactions are unknown.",
        ),
        (
            quality_summary(transaction_count=10, categorized_count=7, uncategorized_count=3),
            "danger",
            "3 of 10 transactions are unknown. Category-level charts may be misleading.",
        ),
        (
            quality_summary(transaction_count=20, categorized_count=17, uncategorized_count=3),
            "warning",
            "3 of 20 transactions are unknown.",
        ),
        (
            quality_summary(),
            "good",
            "Category data is ready for analysis.",
        ),
    ],
)
def test_build_data_quality_classifies_empty_risky_and_ready_views(summary, expected_level, expected_message):
    """Verify data-quality severity thresholds and selected message branches."""
    quality = build_data_quality(summary)

    assert quality["level"] == expected_level
    assert quality["message"] == expected_message


def test_build_data_quality_combines_review_queue_and_unknown_sentence():
    """Verify review counts distinguish unknown rows already needing review."""
    quality = build_data_quality(
        quality_summary(
            transaction_count=5,
            categorized_count=3,
            uncategorized_count=2,
            unknown_needs_review_count=1,
            needs_review_count=1,
            untagged_count=2,
            untagged_spending_total=42.50,
        )
    )

    assert quality["review_label"] == "Review 2 transactions needing review"
    assert quality["unknown_review_sentence"] == "1 unknown transaction needs review."
    assert (
        quality["driver_warning"] == "Category and merchant drivers may be incomplete until unknown rows are reviewed."
    )
    assert quality["readiness_metrics"][1] == {
        "label": "Unknown needing review",
        "value": 1,
        "detail": "1 unknown transaction needs review.",
        "tone": "warning",
    }
    assert quality["readiness_metrics"][2]["detail"] == "$42.50 untagged spending"


@pytest.mark.parametrize(
    ("unknown_needs_review_count", "expected_sentence", "expected_tone"),
    [
        (0, "No unknown transactions need review.", "neutral"),
        (1, "1 unknown transaction needs review.", "warning"),
        (2, "2 unknown transactions need review.", "warning"),
    ],
)
def test_build_data_quality_handles_unknown_review_sentence_boundaries(
    unknown_needs_review_count,
    expected_sentence,
    expected_tone,
):
    """Verify unknown-review copy and tone at zero, singular, and plural boundaries."""
    quality = build_data_quality(
        quality_summary(
            transaction_count=5,
            categorized_count=5 - unknown_needs_review_count,
            uncategorized_count=unknown_needs_review_count,
            unknown_needs_review_count=unknown_needs_review_count,
            needs_review_count=unknown_needs_review_count,
        )
    )

    assert quality["unknown_review_sentence"] == expected_sentence
    assert quality["readiness_metrics"][1]["tone"] == expected_tone


def test_build_data_quality_uses_plural_review_label_for_zero_reviews():
    """Verify zero review counts do not use the singular CTA label."""
    quality = build_data_quality(quality_summary())

    assert quality["review_label"] == "Review 0 transactions needing review"


def test_build_data_quality_preserves_source_counts_and_untagged_fallback():
    """Verify secondary data-quality counters are preserved from query summaries."""
    summary = quality_summary(
        untagged_count=None,
        untagged_spending_count=3,
        manually_reviewed_count=2,
        rule_count=3,
        history_count=4,
        ai_count=5,
        manual_source_count=6,
    )
    del summary["untagged_count"]

    quality = build_data_quality(summary)

    assert quality["untagged_count"] == 3
    assert quality["manual_reviewed_count"] == 2
    assert quality["rule_count"] == 3
    assert quality["history_count"] == 4
    assert quality["ai_count"] == 5
    assert quality["manual_source_count"] == 6


@pytest.mark.parametrize(
    ("count", "total", "expected"),
    [
        (0, 0, 0),
        (1, 4, 25.0),
        (1, 3, 33.3),
    ],
)
def test_percentage_handles_zero_and_rounding_boundaries(count, total, expected):
    """Verify percentage helper avoids division by zero and rounds one decimal."""
    assert percentage(count, total) == expected
