"""Tests for comparison descriptive statistics helpers."""

from decimal import Decimal

from finance_app.modules.comparison.statistics import (
    build_descriptive_statistics,
    build_grouped_descriptive_statistics,
)


def test_build_descriptive_statistics_handles_empty_values():
    """Verify empty inputs return nullable statistics without raising."""
    stats = build_descriptive_statistics([])

    assert stats == {
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
    }


def test_build_descriptive_statistics_summarizes_distribution():
    """Verify descriptive statistics use inclusive quartiles and sample STDEV."""
    stats = build_descriptive_statistics(
        [
            Decimal("10.00"),
            Decimal("20.00"),
            Decimal("30.00"),
            Decimal("40.00"),
            Decimal("50.00"),
            None,
        ]
    )

    assert stats["count"] == 5
    assert stats["total"] == 150.00
    assert stats["mean"] == 30.00
    assert stats["median"] == 30.00
    assert stats["q1"] == 20.00
    assert stats["q3"] == 40.00
    assert stats["iqr"] == 20.00
    assert stats["stdev"] == 15.81
    assert stats["minimum"] == 10.00
    assert stats["maximum"] == 50.00
    assert stats["boxplot"] == [10.00, 20.00, 30.00, 40.00, 50.00]


def test_build_descriptive_statistics_handles_single_value():
    """Verify single-value distributions have no sample standard deviation."""
    stats = build_descriptive_statistics([Decimal("42.50")])

    assert stats["count"] == 1
    assert stats["mean"] == 42.50
    assert stats["median"] == 42.50
    assert stats["iqr"] == 0.00
    assert stats["stdev"] is None
    assert stats["boxplot"] == [42.50, 42.50, 42.50, 42.50, 42.50]


def test_build_grouped_descriptive_statistics_sorts_by_total_then_label():
    """Verify grouped summaries are sorted by absolute total and label."""
    rows = [
        {"category": "Food", "monthly_total": Decimal("20.00")},
        {"category": "Food", "monthly_total": Decimal("40.00")},
        {"category": "Travel", "monthly_total": Decimal("100.00")},
        {"category": "Utilities", "monthly_total": Decimal("-100.00")},
        {"category": "", "monthly_total": Decimal("5.00")},
    ]

    grouped = build_grouped_descriptive_statistics(rows, "category", "monthly_total")

    assert [row["label"] for row in grouped] == [
        "Travel",
        "Utilities",
        "Food",
        "n/a",
    ]
    assert grouped[0]["statistics"]["total"] == 100.00
    assert grouped[2]["statistics"]["mean"] == 30.00
    assert grouped[2]["statistics"]["stdev"] == 14.14
