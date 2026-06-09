"""Statistics helpers for comparison reports.

Provides pure calculation helpers for period, grouped, and robust anomaly
comparison values. Callers provide already-filtered values; this module does
not query storage or decide which transactions belong to a reporting scope.
"""

from collections.abc import Iterable, Mapping, Sequence
from decimal import Decimal
from typing import Any

from finance_app.core.money import money_to_decimal, rounded_money_float

MIN_ROBUST_HISTORY_COUNT = 3
ROBUST_Z_SCORE_SCALE = Decimal("0.6745")
ROBUST_ANOMALY_THRESHOLD = 3.5


def build_descriptive_statistics(values: Iterable[Any]) -> dict[str, Any]:
    """Return descriptive statistics for a sequence of numeric values.

    Args:
        values: Money-like or numeric values. ``None`` values are ignored.

    Returns:
        A dictionary with count, total, mean, median, quartiles, IQR, sample
        standard deviation, minimum, maximum, and boxplot values. Empty inputs
        return count zero with nullable statistics.
    """
    normalized = sorted(normalize_statistics_values(values))
    count = len(normalized)
    if not count:
        return empty_descriptive_statistics()

    total = sum(normalized, Decimal("0"))
    mean = total / count
    median = percentile(normalized, Decimal("0.50"))
    q1 = percentile(normalized, Decimal("0.25"))
    q3 = percentile(normalized, Decimal("0.75"))
    iqr = q3 - q1
    minimum = normalized[0]
    maximum = normalized[-1]
    stdev = sample_standard_deviation(normalized, mean)
    boxplot = [minimum, q1, median, q3, maximum]

    return {
        "count": count,
        "total": rounded_money_float(total),
        "mean": rounded_money_float(mean),
        "median": rounded_money_float(median),
        "q1": rounded_money_float(q1),
        "q3": rounded_money_float(q3),
        "iqr": rounded_money_float(iqr),
        "stdev": rounded_money_float(stdev) if stdev is not None else None,
        "minimum": rounded_money_float(minimum),
        "maximum": rounded_money_float(maximum),
        "boxplot": [rounded_money_float(value) for value in boxplot],
    }


def empty_descriptive_statistics() -> dict[str, Any]:
    """Return the descriptive statistics payload for an empty value set."""
    return {
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


def normalize_statistics_values(values: Iterable[Any]) -> list[Decimal]:
    """Return non-null input values as Decimals for stable calculations."""
    return [money_to_decimal(value) for value in values if value is not None]


def median_absolute_deviation(values: Iterable[Any]) -> dict[str, Any]:
    """Return median absolute deviation metadata for a sequence of values."""
    normalized = sorted(normalize_statistics_values(values))
    count = len(normalized)
    if not count:
        return {
            "count": 0,
            "median": None,
            "mad": None,
        }

    median_value = percentile(normalized, Decimal("0.50"))
    deviations = sorted(abs(value - median_value) for value in normalized)
    mad = percentile(deviations, Decimal("0.50"))
    return {
        "count": count,
        "median": median_value,
        "mad": mad,
    }


def robust_z_score(current: Any, history: Iterable[Any]) -> dict[str, Any]:
    """Return robust z-score metadata for a current value against history.

    History values define the baseline distribution. The returned z-score is a
    float boundary value for ranking, while monetary baseline metadata stays as
    Decimal values.
    """
    statistics = median_absolute_deviation(history)
    if current in (None, ""):
        return _build_robust_score_result(
            "missing_current",
            statistics["count"],
            None,
            statistics["median"],
            statistics["mad"],
        )

    current_value = money_to_decimal(current)
    history_count = statistics["count"]
    median_value = statistics["median"]
    mad = statistics["mad"]

    if history_count == 0:
        return _build_robust_score_result(
            "empty_history",
            history_count,
            current_value,
            median_value,
            mad,
        )

    difference = current_value - median_value
    if history_count < MIN_ROBUST_HISTORY_COUNT:
        return _build_robust_score_result(
            "insufficient_history",
            history_count,
            current_value,
            median_value,
            mad,
            difference=difference,
        )

    if mad == 0:
        if difference == 0:
            z_score = Decimal("0")
            scale = None
            scale_source = "zero_mad"
            status = "zero_mad"
        else:
            z_score = None
            scale = None
            scale_source = "zero_mad"
            status = "zero_mad_nonzero_difference"
    else:
        z_score = (ROBUST_Z_SCORE_SCALE * difference) / mad
        scale = mad
        scale_source = "mad"
        status = "ok"

    return _build_robust_score_result(
        status,
        history_count,
        current_value,
        median_value,
        mad,
        difference=difference,
        scale=scale,
        scale_source=scale_source,
        z_score=z_score,
    )


def robust_anomaly_score(current: Any, history: Iterable[Any]) -> dict[str, Any]:
    """Return robust anomaly score metadata for future insight ranking."""
    result = robust_z_score(current, history)
    z_score = result["z_score"]
    score = abs(z_score) if z_score is not None else 0.0
    return {
        **result,
        "score": score,
        "threshold": ROBUST_ANOMALY_THRESHOLD,
        "is_anomaly": score >= ROBUST_ANOMALY_THRESHOLD,
    }


def _build_robust_score_result(
    status: str,
    history_count: int,
    current: Decimal | None,
    median: Decimal | None,
    mad: Decimal | None,
    *,
    difference: Decimal | None = None,
    scale: Decimal | None = None,
    scale_source: str = "none",
    z_score: Decimal | None = None,
) -> dict[str, Any]:
    """Build the shared robust-score result payload."""
    z_score_float = float(z_score) if z_score is not None else None
    return {
        "status": status,
        "history_count": history_count,
        "minimum_history_count": MIN_ROBUST_HISTORY_COUNT,
        "current": current,
        "median": median,
        "mad": mad,
        "difference": difference,
        "direction": _anomaly_direction(difference),
        "scale": scale,
        "scale_source": scale_source,
        "z_score": z_score_float,
    }


def _anomaly_direction(difference: Decimal | None) -> str | None:
    """Return the direction of a current value relative to its baseline."""
    if difference is None:
        return None
    if difference == 0:
        return "flat"
    return "high" if difference > 0 else "low"


def percentile(sorted_values: Sequence[Decimal], fraction: Decimal) -> Decimal:
    """Return an interpolated inclusive percentile for sorted Decimal values."""
    if len(sorted_values) == 1:
        return sorted_values[0]

    position = Decimal(len(sorted_values) - 1) * fraction
    lower_index = int(position)
    upper_index = min(lower_index + 1, len(sorted_values) - 1)
    weight = position - lower_index
    lower_value = sorted_values[lower_index]
    upper_value = sorted_values[upper_index]
    return lower_value + ((upper_value - lower_value) * weight)


def sample_standard_deviation(values: Sequence[Decimal], mean: Decimal) -> Decimal | None:
    """Return sample standard deviation, or ``None`` when fewer than two values exist."""
    count = len(values)
    if count < 2:
        return None

    variance = sum(((value - mean) ** 2 for value in values), Decimal("0")) / (count - 1)
    return variance.sqrt()


def build_grouped_descriptive_statistics(
    rows: Iterable[Mapping[str, Any]],
    label_key: str,
    value_key: str,
) -> list[dict[str, Any]]:
    """Return descriptive statistics for row values grouped by a label.

    Args:
        rows: Iterable of mapping rows.
        label_key: Mapping key containing the group label, such as category.
        value_key: Mapping key containing the numeric value to summarize.

    Returns:
        A list of dictionaries with ``label`` and ``statistics`` keys, sorted by
        descending absolute total and then by label text.
    """
    grouped_values: dict[str, list[Any]] = {}
    for row in rows:
        label = str(row.get(label_key) or "n/a")
        grouped_values.setdefault(label, []).append(row.get(value_key))

    grouped: list[dict[str, Any]] = [
        {
            "label": label,
            "statistics": build_descriptive_statistics(values),
        }
        for label, values in grouped_values.items()
    ]
    grouped.sort(
        key=lambda row: (
            -abs(float(row["statistics"]["total"])),
            str(row["label"]).casefold(),
        )
    )
    return grouped
