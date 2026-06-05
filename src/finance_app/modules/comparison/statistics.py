"""Descriptive statistics helpers for comparison reports.

Provides pure calculation helpers for period and grouped comparison values.
Callers provide already-filtered values; this module does not query storage or
decide which transactions belong to a reporting scope.
"""

from decimal import Decimal

from finance_app.core.money import money_to_decimal, rounded_money_float


def build_descriptive_statistics(values):
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


def empty_descriptive_statistics():
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


def normalize_statistics_values(values):
    """Return non-null input values as Decimals for stable calculations."""
    return [
        money_to_decimal(value)
        for value in values
        if value is not None
    ]


def percentile(sorted_values, fraction):
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


def sample_standard_deviation(values, mean):
    """Return sample standard deviation, or ``None`` when fewer than two values exist."""
    count = len(values)
    if count < 2:
        return None

    variance = sum((value - mean) ** 2 for value in values) / (count - 1)
    return variance.sqrt()


def build_grouped_descriptive_statistics(rows, label_key, value_key):
    """Return descriptive statistics for row values grouped by a label.

    Args:
        rows: Iterable of mapping rows.
        label_key: Mapping key containing the group label, such as category.
        value_key: Mapping key containing the numeric value to summarize.

    Returns:
        A list of dictionaries with ``label`` and ``statistics`` keys, sorted by
        descending absolute total and then by label text.
    """
    grouped_values = {}
    for row in rows:
        label = str(row.get(label_key) or "n/a")
        grouped_values.setdefault(label, []).append(row.get(value_key))

    grouped = [
        {
            "label": label,
            "statistics": build_descriptive_statistics(values),
        }
        for label, values in grouped_values.items()
    ]
    grouped.sort(
        key=lambda row: (
            -abs(row["statistics"]["total"]),
            row["label"].casefold(),
        )
    )
    return grouped
