"""Tests for comparison descriptive statistics helpers."""

from decimal import Decimal

import pytest

from finance_app.modules.comparison.statistics import (
    build_descriptive_statistics,
    build_grouped_descriptive_statistics,
    median_absolute_deviation,
    robust_anomaly_score,
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


def test_median_absolute_deviation_summarizes_normal_distribution():
    """Verify MAD uses Decimal medians without converting money values to float."""
    stats = median_absolute_deviation(
        [
            Decimal("10.00"),
            Decimal("20.00"),
            Decimal("30.00"),
            Decimal("40.00"),
        ]
    )

    assert stats == {
        "count": 4,
        "median": Decimal("25.00"),
        "mad": Decimal("10.00"),
    }


def test_robust_anomaly_score_detects_high_anomaly():
    """Verify a high current value receives positive robust anomaly metadata."""
    result = robust_anomaly_score(
        Decimal("180.00"),
        [
            Decimal("98.00"),
            Decimal("99.00"),
            Decimal("100.00"),
            Decimal("101.00"),
            Decimal("102.00"),
        ],
    )

    assert result["status"] == "ok"
    assert result["history_count"] == 5
    assert result["median"] == Decimal("100.00")
    assert result["mad"] == Decimal("1.00")
    assert result["difference"] == Decimal("80.00")
    assert result["direction"] == "high"
    assert result["scale"] == Decimal("1.00")
    assert result["scale_source"] == "mad"
    assert result["z_score"] == pytest.approx(53.96)
    assert result["score"] == pytest.approx(53.96)
    assert result["threshold"] == 3.5
    assert result["is_anomaly"] is True


def test_robust_anomaly_score_detects_low_anomaly():
    """Verify a low current value receives negative robust z-score metadata."""
    result = robust_anomaly_score(
        Decimal("20.00"),
        [
            Decimal("98.00"),
            Decimal("99.00"),
            Decimal("100.00"),
            Decimal("101.00"),
            Decimal("102.00"),
        ],
    )

    assert result["status"] == "ok"
    assert result["difference"] == Decimal("-80.00")
    assert result["direction"] == "low"
    assert result["z_score"] == pytest.approx(-53.96)
    assert result["score"] == pytest.approx(53.96)
    assert result["is_anomaly"] is True


def test_robust_anomaly_score_handles_zero_mad_identical_current():
    """Verify identical current and history values produce a zero z-score."""
    result = robust_anomaly_score(
        Decimal("100.00"),
        [
            Decimal("100.00"),
            Decimal("100.00"),
            Decimal("100.00"),
        ],
    )

    assert result["status"] == "zero_mad"
    assert result["median"] == Decimal("100.00")
    assert result["mad"] == Decimal("0.00")
    assert result["difference"] == Decimal("0.00")
    assert result["direction"] == "flat"
    assert result["scale"] is None
    assert result["scale_source"] == "zero_mad"
    assert result["z_score"] == 0.0
    assert result["score"] == 0.0
    assert result["is_anomaly"] is False


def test_robust_anomaly_score_handles_zero_mad_nonzero_difference():
    """Verify zero-MAD nonzero differences stay unscored by robust statistics."""
    result = robust_anomaly_score(
        Decimal("101.00"),
        [
            Decimal("100.00"),
            Decimal("100.00"),
            Decimal("100.00"),
        ],
    )

    assert result["status"] == "zero_mad_nonzero_difference"
    assert result["median"] == Decimal("100.00")
    assert result["mad"] == Decimal("0.00")
    assert result["difference"] == Decimal("1.00")
    assert result["direction"] == "high"
    assert result["scale"] is None
    assert result["scale_source"] == "zero_mad"
    assert result["z_score"] is None
    assert result["score"] == 0.0
    assert result["is_anomaly"] is False


def test_robust_anomaly_score_avoids_huge_score_for_small_zero_mad_difference():
    """Verify tiny money differences do not become inflated robust anomalies."""
    result = robust_anomaly_score(
        Decimal("100.01"),
        [
            Decimal("100.00"),
            Decimal("100.00"),
            Decimal("100.00"),
        ],
    )

    assert result["status"] == "zero_mad_nonzero_difference"
    assert result["difference"] == Decimal("0.01")
    assert result["z_score"] is None
    assert result["score"] == 0.0
    assert result["is_anomaly"] is False


def test_robust_anomaly_score_handles_insufficient_history():
    """Verify short histories return baseline metadata without a score."""
    result = robust_anomaly_score(
        Decimal("130.00"),
        [
            Decimal("100.00"),
            Decimal("101.00"),
        ],
    )

    assert result["status"] == "insufficient_history"
    assert result["history_count"] == 2
    assert result["minimum_history_count"] == 3
    assert result["median"] == Decimal("100.50")
    assert result["mad"] == Decimal("0.50")
    assert result["difference"] == Decimal("29.50")
    assert result["direction"] == "high"
    assert result["z_score"] is None
    assert result["score"] == 0.0
    assert result["is_anomaly"] is False


def test_robust_anomaly_score_handles_empty_history():
    """Verify empty histories return an unscored result with no baseline."""
    result = robust_anomaly_score(Decimal("130.00"), [])

    assert result["status"] == "empty_history"
    assert result["history_count"] == 0
    assert result["minimum_history_count"] == 3
    assert result["current"] == Decimal("130.00")
    assert result["median"] is None
    assert result["mad"] is None
    assert result["difference"] is None
    assert result["direction"] is None
    assert result["scale"] is None
    assert result["scale_source"] == "none"
    assert result["z_score"] is None
    assert result["score"] == 0.0
    assert result["is_anomaly"] is False
