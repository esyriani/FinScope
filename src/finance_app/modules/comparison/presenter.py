"""View-model builders for the comparison feature."""

from calendar import monthrange
from datetime import date
from typing import Any

from finance_app.core.i18n import format_month_year, gettext
from finance_app.core.money import money_to_float, rounded_money_float
from finance_app.modules.comparison.change_metrics import (
    change_state,
    direction_tone,
    format_change_label,
    percentage_change,
    period_change_sentence,
)
from finance_app.modules.comparison.insight_cards import format_signed_count, format_signed_money_text
from finance_app.modules.comparison.statistics import build_descriptive_statistics
from finance_app.modules.merchants.repository import merchant_identity_from_row


def build_monthly_spending(years: Any, rows: Any) -> Any:
    """Build monthly comparison values."""
    by_year = {year: [0.0 for _ in range(12)] for year in years}
    for row in rows:
        if row["year"] in by_year and 1 <= row["month"] <= 12:
            by_year[row["year"]][row["month"] - 1] = rounded_money_float(row["amount"])
    return by_year


def build_monthly_spending_statistics(years: Any, rows: Any) -> Any:
    """Build descriptive statistics for observed monthly comparison totals by year.

    The comparison chart keeps its existing zero-filled twelve-month shape.
    Statistics intentionally use only fetched monthly rows so months outside
    the imported data, especially future months in the current year, are not
    treated as real zero-activity periods.
    """
    values_by_year: dict[Any, list[Any]] = {year: [] for year in years}
    for row in rows:
        year = row["year"]
        if year in values_by_year:
            values_by_year[year].append(row["amount"])

    result = []
    for year in years:
        statistics = build_descriptive_statistics(values_by_year[year])
        result.append(
            {
                "year": year,
                "statistics": statistics,
                "boxplot": statistics["boxplot"],
            }
        )
    return result


def build_monthly_spending_comparison(years: Any, monthly_spending: Any, baseline_year: Any = None) -> Any:
    """Build monthly table rows with per-year totals and year deltas."""
    result = []
    for month_index in range(12):
        totals = {
            year: rounded_money_float((monthly_spending.get(year) or [0 for _ in range(12)])[month_index])
            for year in years
        }
        result.append(
            {
                "month_index": month_index,
                "totals": totals,
                "changes": build_year_changes(years, totals, baseline_year),
                "total": rounded_money_float(sum(totals.values())),
            }
        )
    return result


def build_category_comparison(years: Any, rows: Any, baseline_year: Any = None) -> Any:
    """Build category comparison."""
    categories: dict[Any, dict[Any, Any]] = {}
    for row in rows:
        category = row["category"]
        categories.setdefault(category, {year: 0 for year in years})
        if row["year"] in categories[category]:
            categories[category][row["year"]] = rounded_money_float(row["amount"])

    result = []
    for category, totals in categories.items():
        result.append(
            {
                "category": category,
                "totals": totals,
                "changes": build_year_changes(years, totals, baseline_year),
                "total": rounded_money_float(sum(totals.values())),
            }
        )

    return sorted(result, key=lambda row: row["total"], reverse=True)


def build_year_changes(years: Any, totals: Any, baseline_year: Any = None) -> Any:
    """Build year changes."""
    changes: dict[Any, Any] = {}
    for year in years:
        if baseline_year:
            compare_year = baseline_year if year != baseline_year else None
        else:
            compare_year = year - 1 if year - 1 in totals else None

        if compare_year is None:
            changes[year] = None
            continue

        current = totals.get(year, 0)
        previous = totals.get(compare_year, 0)
        percent = percentage_change(current, previous)
        change = round(current - previous, 2)
        changes[year] = {
            "baseline_year": compare_year,
            "change": change,
            "abs_change": abs(change),
            "percent": percent,
            "amount_label": format_signed_money_text(change),
            "percent_label": format_change_label(current, previous, percent),
            "direction": "up" if change > 0 else "down" if change < 0 else "flat",
            "state": change_state(current, previous),
        }

    return changes


def period_comparison_ranges(comparison_key: Any, today: Any) -> Any:
    """Build comparison ranges."""
    month_start = today.replace(day=1)

    if comparison_key == "month_last_year":
        previous_start = safe_date(today.year - 1, today.month, 1)
        previous_end = safe_date(today.year - 1, today.month, today.day)
        return {
            "current_start": month_start.isoformat(),
            "current_end": today.isoformat(),
            "previous_start": previous_start.isoformat(),
            "previous_end": previous_end.isoformat(),
            "current_label": gettext("{month} to date", month=format_month_year(today)),
            "previous_label": gettext("{month} to same day", month=format_month_year(previous_start)),
            "previous_short_label": gettext("same month last year"),
        }

    if comparison_key == "ytd_last_year":
        previous_end = safe_date(today.year - 1, today.month, today.day)
        return {
            "current_start": date(today.year, 1, 1).isoformat(),
            "current_end": today.isoformat(),
            "previous_start": date(today.year - 1, 1, 1).isoformat(),
            "previous_end": previous_end.isoformat(),
            "current_label": gettext("{year} year to date", year=today.year),
            "previous_label": gettext("{year} same period", year=today.year - 1),
            "previous_short_label": gettext("same period last year"),
        }

    previous_month_year = today.year if today.month > 1 else today.year - 1
    previous_month = today.month - 1 if today.month > 1 else 12
    previous_start = date(previous_month_year, previous_month, 1)
    previous_end = safe_date(previous_month_year, previous_month, today.day)
    return {
        "current_start": month_start.isoformat(),
        "current_end": today.isoformat(),
        "previous_start": previous_start.isoformat(),
        "previous_end": previous_end.isoformat(),
        "current_label": gettext("{month} to date", month=format_month_year(today)),
        "previous_label": gettext("{month} to same day", month=format_month_year(previous_start)),
        "previous_short_label": gettext("last month"),
    }


def safe_date(year: Any, month: Any, day: Any) -> Any:
    """Return a safe date."""
    return date(year, month, min(day, monthrange(year, month)[1]))


def build_period_category_rows(
    current_rows: Any,
    previous_rows: Any,
    analysis_noun: str = "spending",
    positive_tone: str = "danger",
) -> Any:
    """Build period category rows."""
    current = {row["category"]: money_to_float(row["amount"]) for row in current_rows}
    previous = {row["category"]: money_to_float(row["amount"]) for row in previous_rows}
    return build_period_rows(
        "category", sorted(set(current) | set(previous)), current, previous, analysis_noun, positive_tone
    )


def build_period_merchant_rows(
    current_rows: Any,
    previous_rows: Any,
    conn: Any,
    analysis_noun: str = "spending",
    positive_tone: str = "danger",
) -> Any:
    """Build period merchant rows."""
    current = build_merchant_period_totals(current_rows, conn)
    previous = build_merchant_period_totals(previous_rows, conn)
    rows = build_period_rows(
        "merchant", sorted(set(current) | set(previous)), current, previous, analysis_noun, positive_tone
    )

    category_by_merchant = build_merchant_primary_categories(conn, current_rows, previous_rows)
    for row in rows:
        row["category"] = category_by_merchant.get(row["merchant"], "n/a")

    return rows


def build_merchant_period_totals(rows: Any, conn: Any) -> Any:
    """Build merchant period totals."""
    totals: dict[Any, Any] = {}
    for row in rows:
        merchant = merchant_name_from_row(row, conn)
        if not merchant:
            continue
        totals[merchant] = totals.get(merchant, 0) + money_to_float(row["amount"])
    return totals


def build_merchant_primary_categories(conn: Any, *row_groups: Any) -> Any:
    """Build merchant primary categories."""
    category_totals: dict[Any, dict[Any, Any]] = {}
    for rows in row_groups:
        for row in rows:
            merchant = merchant_name_from_row(row, conn)
            if not merchant:
                continue
            totals = category_totals.setdefault(merchant, {})
            totals[row["category"]] = totals.get(row["category"], 0) + money_to_float(row["amount"])

    result = {}
    for merchant, totals in category_totals.items():
        result[merchant] = max(totals, key=lambda category: (abs(totals[category]), category))
    return result


def build_period_category_history(rows: Any) -> Any:
    """Build category history values from historical monthly analysis rows."""
    history: dict[Any, list[Any]] = {}
    for row in rows:
        history.setdefault(row["category"], []).append(row["amount"])
    return history


def build_period_merchant_history(rows: Any, conn: Any) -> Any:
    """Build merchant history values from historical monthly transaction rows."""
    monthly_totals: dict[Any, Any] = {}
    for row in rows:
        merchant = merchant_name_from_row(row, conn)
        if not merchant:
            continue
        key = (merchant, row["year"], row["month"])
        monthly_totals[key] = monthly_totals.get(key, 0) + money_to_float(row["amount"])

    history: dict[Any, list[Any]] = {}
    for (merchant, _year, _month), total in monthly_totals.items():
        history.setdefault(merchant, []).append(total)
    return history


def build_period_merchant_activity_history(rows: Any, conn: Any, current_start: Any) -> Any:
    """Build merchant monthly activity metadata from historical transaction rows."""
    current_period_index = month_index_from_date(current_start)
    monthly_totals: dict[Any, Any] = {}
    for row in rows:
        merchant = merchant_name_from_row(row, conn)
        if not merchant:
            continue
        key = (merchant, row["year"], row["month"])
        monthly_totals[key] = monthly_totals.get(key, 0) + money_to_float(row["amount"])

    by_merchant: dict[Any, list[dict[str, Any]]] = {}
    for (merchant, year, month), total in monthly_totals.items():
        period_index = month_index(year, month)
        by_merchant.setdefault(merchant, []).append(
            {
                "year": year,
                "month": month,
                "period_index": period_index,
                "total": rounded_money_float(total),
            }
        )

    result = {}
    for merchant, periods in by_merchant.items():
        periods.sort(key=lambda period: period["period_index"], reverse=True)
        last_period = periods[0]
        result[merchant] = {
            "periods": periods,
            "history_count": len(periods),
            "last_activity_months_ago": current_period_index - last_period["period_index"],
            "last_activity_label": f"{last_period['year']}-{last_period['month']:02d}",
        }
    return result


def merchant_name_from_row(row: Any, conn: Any) -> Any:
    """Return the durable merchant label for linked rows, else a normalized fallback."""
    return merchant_identity_from_row(row, conn=conn)["name"]


def month_index_from_date(value: Any) -> Any:
    """Return a comparable month index for a date or ISO date string."""
    if isinstance(value, str):
        value = date.fromisoformat(value)
    return month_index(value.year, value.month)


def month_index(year: Any, month: Any) -> Any:
    """Return a comparable month index."""
    return (int(year) * 12) + int(month)


def build_period_rows(
    label_key: Any,
    labels: Any,
    current: Any,
    previous: Any,
    analysis_noun: str,
    positive_tone: str,
) -> Any:
    """Build period rows."""
    rows = []
    for label in labels:
        metric = build_period_metric(
            label,
            current.get(label, 0),
            previous.get(label, 0),
            analysis_noun,
            "",
            positive_tone=positive_tone,
        )
        rows.append(
            {
                label_key: label,
                **metric,
            }
        )

    rows.sort(key=lambda row: abs(row["change"]), reverse=True)
    return rows


def build_period_metric(
    label: Any,
    current: Any,
    previous: Any,
    noun: Any,
    previous_label: Any,
    value_type: Any = "money",
    positive_tone: str = "danger",
) -> Any:
    """Build period metric."""
    if value_type == "count":
        current = round(current or 0, 2)
        previous = round(previous or 0, 2)
        change = round(current - previous, 2)
    else:
        current = rounded_money_float(current)
        previous = rounded_money_float(previous)
        change = rounded_money_float(current - previous)
    percent = percentage_change(current, previous)
    direction = "up" if change > 0 else "down" if change < 0 else "flat"
    state = change_state(current, previous)
    tone = direction_tone(direction, positive_tone)
    return {
        "label": label,
        "noun": noun,
        "value_type": value_type,
        "current": current,
        "previous": previous,
        "change": change,
        "abs_change": abs(change),
        "percent": percent,
        "amount_label": format_signed_count(change) if value_type == "count" else format_signed_money_text(change),
        "percent_label": format_change_label(current, previous, percent),
        "direction": direction,
        "state": state,
        "tone": tone,
        "sentence": period_change_sentence(label, noun, change, percent, previous, current, previous_label),
    }


def build_category_context(categories: Any) -> Any:
    """Build category context."""
    return ", ".join(categories) if categories else gettext("All categories")


def build_tag_context(tags: Any) -> Any:
    """Build tag context."""
    return ", ".join(tags) if tags else gettext("All tags")


def build_account_context(account_name: Any = "") -> Any:
    """Build account context."""
    return str(account_name or "").strip() or gettext("All accounts")


def build_merchant_context(merchant_name: Any = "") -> Any:
    """Build merchant context."""
    return str(merchant_name or "").strip() or gettext("All merchants")


def build_period_filter_context(
    option_label: Any,
    categories: Any,
    tags: Any = None,
    account_name: Any = "",
    merchant_name: Any = "",
    analysis_noun: Any = "spending",
) -> Any:
    """Build period filter context."""
    return gettext(
        (
            "{period} - Analysis: {analysis} - Account: {account} - "
            "Merchant: {merchant} - Categories: {categories} - Tags: {tags}"
        ),
        period=gettext(option_label),
        analysis=gettext(analysis_noun),
        account=build_account_context(account_name),
        merchant=build_merchant_context(merchant_name),
        categories=build_category_context(categories),
        tags=build_tag_context(tags or []),
    )


def build_year_filter_context(
    years: Any,
    baseline_year: Any,
    categories: Any,
    tags: Any = None,
    account_name: Any = "",
    merchant_name: Any = "",
    analysis_noun: Any = "spending",
) -> Any:
    """Build year filter context."""
    baseline_label = str(baseline_year) if baseline_year else gettext("previous year")
    return gettext(
        (
            "Years: {years} - Baseline: {baseline} - Analysis: {analysis} - Account: {account} - "
            "Merchant: {merchant} - Categories: {categories} - Tags: {tags}"
        ),
        years=", ".join(str(year) for year in years),
        baseline=baseline_label,
        analysis=gettext(analysis_noun),
        account=build_account_context(account_name),
        merchant=build_merchant_context(merchant_name),
        categories=build_category_context(categories),
        tags=build_tag_context(tags or []),
    )
