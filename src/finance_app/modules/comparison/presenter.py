"""View-model builders for the comparison feature."""

from calendar import monthrange
from datetime import date

from finance_app.core.i18n import format_month_year, gettext
from finance_app.core.money import money_to_float, rounded_money_float
from finance_app.modules.merchants.normalization import normalize_merchant
from finance_app.modules.comparison.constants import UNKNOWN_WARNING_THRESHOLD


def build_monthly_spending(years, rows):
    """Build monthly spending."""
    by_year = {
        year: [0 for _ in range(12)]
        for year in years
    }
    for row in rows:
        if row["year"] in by_year and 1 <= row["month"] <= 12:
            by_year[row["year"]][row["month"] - 1] = rounded_money_float(row["spending"])
    return by_year



def build_category_comparison(years, rows, baseline_year=None):
    """Build category comparison."""
    categories = {}
    for row in rows:
        category = row["category"]
        categories.setdefault(category, {year: 0 for year in years})
        if row["year"] in categories[category]:
            categories[category][row["year"]] = rounded_money_float(row["spending"])

    result = []
    for category, totals in categories.items():
        result.append({
            "category": category,
            "totals": totals,
            "changes": build_year_changes(years, totals, baseline_year),
            "total": rounded_money_float(sum(totals.values())),
        })

    return sorted(result, key=lambda row: row["total"], reverse=True)



def build_year_changes(years, totals, baseline_year=None):
    """Build year changes."""
    changes = {}
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



def period_comparison_ranges(comparison_key, today):
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



def safe_date(year, month, day):
    """Return a safe date."""
    return date(year, month, min(day, monthrange(year, month)[1]))



def build_period_category_rows(current_rows, previous_rows):
    """Build period category rows."""
    current = {row["category"]: money_to_float(row["spending"]) for row in current_rows}
    previous = {row["category"]: money_to_float(row["spending"]) for row in previous_rows}
    return build_period_rows("category", sorted(set(current) | set(previous)), current, previous)



def build_period_merchant_rows(current_rows, previous_rows, conn):
    """Build period merchant rows."""
    current = build_merchant_period_totals(current_rows, conn)
    previous = build_merchant_period_totals(previous_rows, conn)
    rows = build_period_rows("merchant", sorted(set(current) | set(previous)), current, previous)

    category_by_merchant = build_merchant_primary_categories(conn, current_rows, previous_rows)
    for row in rows:
        row["category"] = category_by_merchant.get(row["merchant"], "n/a")

    return rows



def build_merchant_period_totals(rows, conn):
    """Build merchant period totals."""
    totals = {}
    for row in rows:
        merchant = normalize_merchant(row["description"], conn=conn).merchant_key
        if not merchant:
            continue
        totals[merchant] = totals.get(merchant, 0) + money_to_float(row["amount"])
    return totals



def build_merchant_primary_categories(conn, *row_groups):
    """Build merchant primary categories."""
    category_totals = {}
    for rows in row_groups:
        for row in rows:
            merchant = normalize_merchant(row["description"], conn=conn).merchant_key
            if not merchant:
                continue
            totals = category_totals.setdefault(merchant, {})
            totals[row["category"]] = totals.get(row["category"], 0) + money_to_float(row["amount"])

    result = {}
    for merchant, totals in category_totals.items():
        result[merchant] = max(totals, key=lambda category: (totals[category], category))
    return result



def build_period_rows(label_key, labels, current, previous):
    """Build period rows."""
    rows = []
    for label in labels:
        metric = build_period_metric(
            label,
            current.get(label, 0),
            previous.get(label, 0),
            "spending",
            "",
        )
        rows.append({
            label_key: label,
            **metric,
        })

    rows.sort(key=lambda row: abs(row["change"]), reverse=True)
    return rows



def build_period_metric(label, current, previous, noun, previous_label, value_type="money"):
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
    return {
        "label": label,
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
        "sentence": period_change_sentence(label, noun, change, percent, previous, current, previous_label),
    }



def percentage_change(current, previous):
    """Handle percentage change."""
    if previous == 0:
        return None
    return round(((current - previous) / previous) * 100, 1)



def change_state(current, previous):
    """Build state."""
    if current == 0 and previous == 0:
        return "no_activity"
    if current > 0 and previous == 0:
        return "new"
    if current == 0 and previous > 0:
        return "dropped"
    if current == previous:
        return "no_change"
    return "changed"



def format_change_label(current, previous, percent):
    """Format change label."""
    state = change_state(current, previous)
    labels = {
        "no_activity": "No activity",
        "new": "New",
        "dropped": "Dropped",
        "no_change": "No change",
    }
    if state in labels:
        return gettext(labels[state])
    return f"{percent:+.1f}%" if percent is not None else "n/a"



def period_change_sentence(label, noun, change, percent, previous, current, previous_label):
    """Build change sentence."""
    if previous == 0 and current == 0:
        return gettext(
            "{label} {noun} is unchanged versus {period}.",
            label=label,
            noun=noun,
            period=previous_label,
        )
    if previous == 0:
        return gettext(
            "{label} {noun} is new versus {period}.",
            label=label,
            noun=noun,
            period=previous_label,
        )

    direction = "up" if change > 0 else "down" if change < 0 else "unchanged"
    if direction == "unchanged":
        return gettext(
            "{label} {noun} is unchanged versus {period}.",
            label=label,
            noun=noun,
            period=previous_label,
        )

    return gettext(
        "{label} {noun} is {direction} {amount}, or {percent}%, versus {period}.",
        label=label,
        noun=noun,
        direction=gettext(direction),
        amount=format_money_text(abs(change)),
        percent=abs(percent),
        period=previous_label,
    )



def build_period_insights(category_rows, merchant_rows, current_summary, previous_summary):
    """Build period insights."""
    insights = []
    category_increase = largest_change(category_rows, "up")
    category_decrease = largest_change(category_rows, "down")
    merchant_increase = largest_change(merchant_rows, "up")
    merchant_decrease = largest_change(merchant_rows, "down")

    if category_increase:
        insights.append(change_insight("Largest category increase", category_increase, "category"))
    if category_decrease:
        insights.append(change_insight("Largest category decrease", category_decrease, "category"))
    if merchant_increase:
        insights.append(change_insight("Largest merchant increase", merchant_increase, "merchant"))
    if merchant_decrease:
        insights.append(change_insight("Largest merchant decrease", merchant_decrease, "merchant"))

    new_merchants = [row for row in merchant_rows if row["state"] == "new"]
    new_categories = [row for row in category_rows if row["state"] == "new"]
    if new_merchants or new_categories:
        total_new_spending = sum(row["current"] for row in new_merchants)
        new_merchant_label = gettext(
            "{count} new merchant" if len(new_merchants) == 1 else "{count} new merchants",
            count=len(new_merchants),
        )
        new_category_label = gettext(
            "{count} new category" if len(new_categories) == 1 else "{count} new categories",
            count=len(new_categories),
        )
        insights.append({
            "label": "New spending this period",
            "value": f"{new_merchant_label}, {new_category_label}",
            "detail": gettext(
                "{amount} total new spending",
                amount=format_money_text(total_new_spending),
            ),
            "visual": "aggregate",
            "group": "spending",
            "tone": "danger",
            "icon": "bi-stars",
            "title": gettext("New spending"),
            "summary": format_money_text(total_new_spending),
            "badge": gettext("New"),
            "stat_items": [
                {"label": "Merchants", "value": str(len(new_merchants))},
                {"label": "Categories", "value": str(len(new_categories))},
            ],
        })

    dropped_merchants = [row for row in merchant_rows if row["state"] == "dropped"]
    dropped_categories = [row for row in category_rows if row["state"] == "dropped"]
    if dropped_merchants or dropped_categories:
        total_dropped_spending = sum(row["previous"] for row in dropped_merchants)
        insights.append({
            "label": "Dropped spending from prior period",
            "value": gettext(
                (
                    "{count} merchant no longer appears"
                    if len(dropped_merchants) == 1
                    else "{count} merchants no longer appear"
                ),
                count=len(dropped_merchants),
            ),
            "detail": gettext(
                (
                    "{amount} less spending across {count} category"
                    if len(dropped_categories) == 1
                    else "{amount} less spending across {count} categories"
                ),
                amount=format_money_text(total_dropped_spending),
                count=len(dropped_categories),
            ),
            "visual": "aggregate",
            "group": "spending",
            "tone": "success",
            "icon": "bi-dash-circle",
            "title": gettext("Dropped spending"),
            "summary": format_money_text(total_dropped_spending),
            "badge": gettext("Lower"),
            "stat_items": [
                {"label": "Merchants", "value": str(len(dropped_merchants))},
                {"label": "Categories", "value": str(len(dropped_categories))},
            ],
        })

    current_count = current_summary["transaction_count"] or 0
    previous_count = previous_summary["transaction_count"] or 0
    count_change = current_count - previous_count
    average = money_to_float(current_summary["spending"]) / current_count if current_count else 0
    insights.append({
        "label": "Transaction activity",
        "value": gettext(
            "{count} transaction" if current_count == 1 else "{count} transactions",
            count=current_count,
        ),
        "detail": gettext(
            "{change} versus prior period. Average transaction: {amount}",
            change=format_signed_count(count_change),
            amount=format_money_text(average),
        ),
        "visual": "activity",
        "group": "spending",
        "tone": "accent",
        "icon": "bi-activity",
        "title": "Transactions",
        "summary": f"{current_count}",
        "badge": format_signed_count(count_change),
        "current_width": comparison_bar_width(current_count, previous_count),
        "previous_width": comparison_bar_width(previous_count, current_count),
        "stat_items": [
            {"label": "Current", "value": f"{current_count}"},
            {"label": "Prior", "value": f"{previous_count}"},
            {"label": "Average", "value": format_money_text(average)},
        ],
    })

    return insights[:7]



def largest_change(rows, direction):
    """Handle largest change."""
    candidates = [row for row in rows if row["direction"] == direction]
    return max(candidates, key=lambda row: row["abs_change"], default=None)



def change_insight(label, row, label_key):
    """Build insight."""
    name = row[label_key]
    if row["state"] == "new":
        value = gettext("{name}: new spending this period", name=name)
    elif row["state"] == "dropped":
        value = gettext("{name}: dropped from current period", name=name)
    else:
        value = f"{name} {format_signed_money_text(row['change'])} ({row['percent_label']})"

    return {
        "label": label,
        "value": value,
        "detail": gettext(
            "Prior: {prior}. Current: {current}",
            prior=format_money_text(row["previous"]),
            current=format_money_text(row["current"]),
        ),
        "visual": "comparison",
        "group": "categories" if label_key == "category" else "merchants",
        "tone": change_insight_tone(row),
        "icon": change_insight_icon(row),
        "title": name,
        "summary": row["amount_label"],
        "badge": gettext(row["percent_label"]),
        "previous_label": format_money_text(row["previous"]),
        "current_label": format_money_text(row["current"]),
        "previous_width": comparison_bar_width(row["previous"], row["current"]),
        "current_width": comparison_bar_width(row["current"], row["previous"]),
    }



def change_insight_tone(row):
    """Return the visual tone for a period insight row."""
    return "danger" if row["direction"] == "up" else "success" if row["direction"] == "down" else "muted"



def change_insight_icon(row):
    """Return a Bootstrap icon class for a period insight row."""
    if row["state"] == "new":
        return "bi-plus-circle"
    if row["state"] == "dropped":
        return "bi-dash-circle"
    if row["direction"] == "down":
        return "bi-graph-down-arrow"
    return "bi-graph-up-arrow"



def comparison_bar_width(value, comparison_value):
    """Return a percent width for comparing two non-negative visual bars."""
    maximum = max(abs(value or 0), abs(comparison_value or 0))
    if maximum == 0:
        return 0
    return round((abs(value or 0) / maximum) * 100, 1)



def build_period_insight_groups(insights):
    """Group period insights into carousel sections."""
    grouped = {
        "categories": {
            "key": "categories",
            "label": "Categories",
            "insights": [],
        },
        "merchants": {
            "key": "merchants",
            "label": "Merchants",
            "insights": [],
        },
        "spending": {
            "key": "spending",
            "label": "Spending and transactions",
            "insights": [],
        },
    }

    for insight in insights:
        group_key = insight.get("group", "spending")
        if group_key not in grouped:
            group_key = "spending"
        grouped[group_key]["insights"].append(insight)

    return [group for group in grouped.values() if group["insights"]]



def build_period_unknown_warning(category_rows, current_spending, previous_spending, unknown_category):
    """Build period unknown warning."""
    unknown = next((row for row in category_rows if row["category"] == unknown_category), None)
    if not unknown:
        return None

    current_share = percentage_share(unknown["current"], current_spending)
    previous_share = percentage_share(unknown["previous"], previous_spending)
    largest_share = max(current_share, previous_share)
    if largest_share < UNKNOWN_WARNING_THRESHOLD:
        return None

    return build_unknown_warning_message(
        "Category insights may be incomplete because {category} accounts for {share}% of selected spending.",
        unknown_category,
        largest_share,
    )



def build_year_unknown_warning(category_comparison, unknown_category):
    """Build year unknown warning."""
    total = sum(row["total"] for row in category_comparison)
    unknown = next((row for row in category_comparison if row["category"] == unknown_category), None)
    if not unknown or not total:
        return None

    share = percentage_share(unknown["total"], total)
    largest_category = max(category_comparison, key=lambda row: row["total"], default=None)
    if share < UNKNOWN_WARNING_THRESHOLD and largest_category != unknown:
        return None

    return build_unknown_warning_message(
        "Category comparison may be unreliable because {category} accounts for {share}% of selected spending.",
        unknown_category,
        share,
    )



def build_unknown_warning_message(source, category, share):
    """Build a translatable warning message with its interpolation values."""
    values = {
        "category": category,
        "share": f"{share:.1f}",
    }
    return {
        "source": source,
        "values": values,
        "text": gettext(source, **values),
    }



def percentage_share(value, total):
    """Handle percentage share."""
    value = money_to_float(value)
    total = money_to_float(total)
    return round((value / total) * 100, 1) if total else 0



def format_signed_count(value):
    """Format signed count."""
    value = int(round(value or 0))
    return f"{value:+d}" if value else "0"



def format_money_text(value):
    """Format money text."""
    return f"{value:,.2f} $".replace(",", " ")



def format_signed_money_text(value):
    """Format signed money text."""
    value = rounded_money_float(value)
    prefix = "+" if value > 0 else "-" if value < 0 else ""
    return f"{prefix}{format_money_text(abs(value))}"

def build_category_context(categories):
    """Build category context."""
    return ", ".join(categories) if categories else gettext("All categories")


def build_tag_context(tags):
    """Build tag context."""
    return ", ".join(tags) if tags else gettext("All tags")



def build_period_filter_context(option_label, categories, tags=None):
    """Build period filter context."""
    return gettext(
        "{period} - Categories: {categories} - Tags: {tags}",
        period=gettext(option_label),
        categories=build_category_context(categories),
        tags=build_tag_context(tags or []),
    )



def build_year_filter_context(years, baseline_year, categories, tags=None):
    """Build year filter context."""
    baseline_label = str(baseline_year) if baseline_year else gettext("previous year")
    return gettext(
        "Years: {years} - Baseline: {baseline} - Categories: {categories} - Tags: {tags}",
        years=", ".join(str(year) for year in years),
        baseline=baseline_label,
        categories=build_category_context(categories),
        tags=build_tag_context(tags or []),
    )


