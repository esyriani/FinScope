"""Recurring transaction inference helpers for the recurring feature."""

from calendar import monthrange
from collections.abc import Iterable, Mapping
from dataclasses import replace
from datetime import date, datetime, timedelta
from decimal import Decimal
from statistics import median
from typing import Any

from finance_app.core.money import MoneyValue, money_to_decimal, rounded_money_decimal, rounded_money_float
from finance_app.modules.merchants.repository import merchant_identity_from_row
from finance_app.modules.transactions.urls import transactions_date_range_url

from .parsing import month_number
from .patterns import recurring_pattern_key
from .settings import RECURRENCE_DETECTION_DEFAULTS


def infer_recurring_items(
    rows: Iterable[Mapping[str, Any]],
    month_start: date,
    month_end: date,
    month_transactions: Iterable[Mapping[str, Any]],
    recurrence_settings: Any = None,
    recurring_pattern_metadata: Mapping[str, Mapping[str, Any]] | None = None,
    conn: Any = None,
    account_id: int | None = None,
    merchant_search: str = "",
) -> list[dict[str, Any]]:
    """Infer recurring items."""
    recurrence_settings = recurrence_settings or RECURRENCE_DETECTION_DEFAULTS
    recurring_pattern_metadata = recurring_pattern_metadata or {}
    groups: dict[tuple[str, str], dict[str, Any]] = {}
    # First group historical transactions by normalized merchant and cash-flow
    # direction; recurrence is inferred from this merchant/type history.
    for row in rows:
        merchant = merchant_identity_from_row(row, conn=conn)
        if not merchant["key"]:
            continue

        amount = money_to_decimal(row["amount"])
        tx_type = "spending" if amount > 0 else "income" if amount < 0 else "neutral"
        if tx_type == "neutral":
            continue

        key = (merchant["key"], tx_type)
        group = groups.setdefault(
            key,
            {
                "merchant_id": merchant["id"],
                "merchant": merchant["name"],
                "merchant_key": merchant["key"],
                "cleaned_keys": set(),
                "type": tx_type,
                "amounts": [],
                "days": [],
                "dates": [],
                "months": set(),
                "category_totals": {},
                "occurrences": [],
            },
        )
        tx_date = datetime.strptime(row["tx_date"], "%Y-%m-%d").date()
        amount_abs = abs(amount)
        group["cleaned_keys"].add(merchant["cleaned_key"])
        group["amounts"].append(amount_abs)
        group["days"].append(tx_date.day)
        group["dates"].append(tx_date)
        group["months"].add(tx_date.strftime("%Y-%m"))
        group["category_totals"][row["category"]] = (
            group["category_totals"].get(row["category"], Decimal("0")) + amount_abs
        )
        group["occurrences"].append(
            {
                "date": row["tx_date"],
                "description": row["description"],
                "amount": rounded_money_float(amount_abs),
                "type": tx_type,
                "category": row["category"],
                "account_name": row["account_name"],
                "url": transactions_date_range_url(
                    row["tx_date"],
                    row["tx_date"],
                    account_id=account_id,
                    merchant_search=merchant_search,
                ),
            }
        )

    month_transactions_by_key: dict[tuple[str, str], list[Mapping[str, Any]]] = {}
    # Current-month transactions are pre-normalized by the recurring presenter,
    # so matching can stay focused on date and amount tolerances.
    for transaction in month_transactions:
        month_transactions_by_key.setdefault(
            (transaction["merchant_key"], transaction["type"]),
            [],
        ).append(transaction)

    recurring: list[dict[str, Any]] = []
    last_day = monthrange(month_start.year, month_start.month)[1]
    evaluation_date = recurrence_evaluation_date(month_start, month_end)

    for group in groups.values():
        if len(group["months"]) < recurrence_settings.minimum_occurrences:
            continue

        pattern_key, pattern_metadata, match_type = recurring_pattern_metadata_for_group(
            group,
            recurring_pattern_metadata,
        )
        if pattern_metadata.get("user_status") == "ignored" or pattern_metadata.get("active") == 0:
            continue

        # Use the dominant historical category and median amount as defaults,
        # then let user-edited pattern metadata override those estimates.
        category = max(
            group["category_totals"],
            key=lambda item: (group["category_totals"][item], item),
        )
        typical_amount = rounded_money_decimal(
            pattern_metadata["typical_amount"]
            if pattern_metadata.get("typical_amount") is not None
            else median(group["amounts"])
        )
        candidates = month_transactions_by_key.get((group["merchant_key"], group["type"]), [])
        pattern_recurrence_settings = recurrence_settings_for_pattern(recurrence_settings, pattern_metadata)
        last_seen = max(
            (seen_date for seen_date in group["dates"] if seen_date <= month_end),
            default=max(group["dates"]),
        )
        observed_months = len(group["months"])
        frequency = pattern_metadata.get("frequency") or recurring_frequency_label(group["dates"], group["months"])
        expected_day = min(last_day, max(1, round(median(group["days"]))))
        expected_day_override = pattern_metadata.get("expected_day")
        expected_day = expected_day_override or expected_day
        expected_dates = recurring_expected_dates(
            month_start,
            month_end,
            group["dates"],
            expected_day,
            frequency,
            anchor_to_expected_day=expected_day_override is not None,
        )
        pattern_merchant_id = pattern_metadata.get("merchant_id") or group["merchant_id"]
        pattern_merchant = pattern_metadata.get("merchant") or group["merchant"]
        consumed_candidate_indexes: set[int] = set()
        latest_seen = last_seen
        for expected_date in expected_dates:
            indexed_candidates = [
                (index, candidate)
                for index, candidate in enumerate(candidates)
                if index not in consumed_candidate_indexes
            ]
            match = classify_recurring_match(
                [candidate for _index, candidate in indexed_candidates],
                expected_date,
                typical_amount,
                evaluation_date,
                pattern_recurrence_settings,
                last_seen=latest_seen,
                frequency=frequency,
            )
            consumed_index = recurring_match_candidate_index(match, indexed_candidates)
            if consumed_index is not None:
                consumed_candidate_indexes.add(consumed_index)
                matched_date = datetime.strptime(match["matched_date"], "%Y-%m-%d").date()
                latest_seen = max(latest_seen, matched_date)

            recurring.append(
                {
                    "id": f"recurring-{len(recurring)}",
                    "pattern_key": pattern_key,
                    "merchant_id": group["merchant_id"],
                    "pattern_merchant_id": pattern_merchant_id,
                    "match_type": match_type,
                    "date": expected_date.isoformat(),
                    "merchant": group["merchant"],
                    "pattern_merchant": pattern_merchant,
                    "amount": rounded_money_float(typical_amount),
                    "type": group["type"],
                    "category": category,
                    "last_seen": latest_seen.isoformat(),
                    "observed_months": observed_months,
                    "frequency": frequency,
                    "confidence": recurring_confidence_label(observed_months, pattern_recurrence_settings),
                    "user_status": pattern_metadata.get("user_status") or "detected",
                    "active": pattern_metadata.get("active", 1),
                    "status": match["status"],
                    "match_details": match,
                    "amount_change": recurring_amount_change_details(typical_amount, match),
                    "occurrences": recent_recurring_occurrences(group["occurrences"]),
                    "url": transactions_date_range_url(
                        expected_date.isoformat(),
                        expected_date.isoformat(),
                        account_id=account_id,
                        merchant_search=merchant_search,
                    ),
                }
            )

    recurring.sort(key=lambda item: (item["date"], item["type"], item["merchant"]))
    return recurring


def recurring_pattern_metadata_for_group(
    group: Mapping[str, Any],
    recurring_pattern_metadata: Mapping[str, Mapping[str, Any]],
) -> tuple[str, Mapping[str, Any], str]:
    """Return matching recurring metadata for a merchant/type group.

    Merchant-bound metadata wins when a durable merchant exists. Keyword-fuzzy
    metadata remains a fallback for patterns that intentionally stay text based.
    """
    tx_type = group["type"]
    candidates: list[tuple[str, str]] = []
    if group["merchant_id"]:
        candidates.append((recurring_pattern_key(group["merchant_key"], tx_type), "merchant"))
    candidates.append((recurring_pattern_key(group["merchant"], tx_type), "keyword"))
    candidates.extend(
        (recurring_pattern_key(cleaned_key, tx_type), "keyword")
        for cleaned_key in sorted(group["cleaned_keys"])
        if cleaned_key and cleaned_key != group["merchant"]
    )

    for pattern_key, default_match_type in candidates:
        metadata = recurring_pattern_metadata.get(pattern_key)
        if metadata:
            return pattern_key, metadata, metadata.get("match_type") or default_match_type

    return candidates[0][0], {}, candidates[0][1]


def recurrence_settings_for_pattern(recurrence_settings: Any, pattern_metadata: Mapping[str, Any]) -> Any:
    """Build settings for pattern."""
    overrides: dict[str, Any] = {}
    if pattern_metadata.get("date_tolerance_days") is not None:
        overrides["date_tolerance_days"] = pattern_metadata["date_tolerance_days"]
    if pattern_metadata.get("amount_tolerance") is not None:
        overrides["amount_tolerance_absolute"] = pattern_metadata["amount_tolerance"]
        overrides["amount_tolerance_percent"] = 0
    return replace(recurrence_settings, **overrides) if overrides else recurrence_settings


def recurring_expected_dates(
    month_start: date,
    month_end: date,
    historical_dates: Iterable[date],
    expected_day: int,
    frequency: str | None,
    *,
    anchor_to_expected_day: bool = False,
) -> list[date]:
    """Return expected recurrence dates for the selected month."""
    interval_days = recurring_frequency_interval_days(frequency)
    last_day = monthrange(month_start.year, month_start.month)[1]
    fallback_date = month_start.replace(day=min(last_day, max(1, expected_day)))
    if interval_days is None:
        return [fallback_date]

    if anchor_to_expected_day:
        anchor = fallback_date
        while anchor - timedelta(days=interval_days) >= month_start:
            anchor -= timedelta(days=interval_days)
    else:
        prior_dates = sorted({historical_date for historical_date in historical_dates if historical_date < month_start})
        anchor = prior_dates[-1] if prior_dates else fallback_date
        while anchor < month_start:
            anchor += timedelta(days=interval_days)

    dates = []
    expected_date = anchor
    while expected_date <= month_end:
        if expected_date >= month_start:
            dates.append(expected_date)
        expected_date += timedelta(days=interval_days)

    return dates or [fallback_date]


def recurring_frequency_interval_days(frequency: str | None) -> int | None:
    """Return fixed interval days for frequencies that can occur more than once per month."""
    return {
        "Weekly": 7,
        "Biweekly": 14,
    }.get(frequency or "")


def recurring_match_candidate_index(
    match: Mapping[str, Any],
    indexed_candidates: Iterable[tuple[int, Mapping[str, Any]]],
) -> int | None:
    """Return the original candidate index consumed by a matched recurring row."""
    matched_date = match.get("matched_date")
    if not matched_date:
        return None

    matched_amount = rounded_money_decimal(match.get("matched_amount"))
    for index, candidate in indexed_candidates:
        if candidate.get("date") == matched_date and rounded_money_decimal(candidate.get("amount")) == matched_amount:
            return index

    return None


def recurring_amount_change_details(
    typical_amount: MoneyValue | None,
    match: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Build amount change details."""
    if match.get("status") != "amount_changed" or match.get("matched_amount") is None:
        return None

    typical_amount = rounded_money_decimal(typical_amount)
    actual_amount = rounded_money_decimal(match["matched_amount"])
    difference = rounded_money_decimal(actual_amount - typical_amount)
    percent = round(float((difference / typical_amount) * Decimal("100")), 1) if typical_amount else None
    return {
        "typical_amount": rounded_money_float(typical_amount),
        "actual_amount": rounded_money_float(actual_amount),
        "difference": rounded_money_float(difference),
        "percent": percent,
    }


def recurrence_evaluation_date(month_start: date, month_end: date) -> date:
    # For historical months, judge missing recurring items at month end; for the
    # active month, judge them as of today; future months cannot be overdue yet.
    """Build evaluation date."""
    today = date.today()
    if today < month_start:
        return month_start
    if today > month_end:
        return month_end
    return today


def classify_recurring_status(
    candidates: Iterable[Mapping[str, Any]],
    expected_date: date,
    typical_amount: MoneyValue | None,
    evaluation_date: date,
    recurrence_settings: Any = None,
    last_seen: date | None = None,
    frequency: str | None = None,
) -> str:
    """Classify recurring status."""
    return classify_recurring_match(
        candidates,
        expected_date,
        typical_amount,
        evaluation_date,
        recurrence_settings,
        last_seen=last_seen,
        frequency=frequency,
    )["status"]


def classify_recurring_match(
    candidates: Iterable[Mapping[str, Any]],
    expected_date: date,
    typical_amount: MoneyValue | None,
    evaluation_date: date,
    recurrence_settings: Any = None,
    last_seen: date | None = None,
    frequency: str | None = None,
) -> dict[str, Any]:
    """Classify current-month recurrence evidence using deterministic tolerances.

    A candidate already has the same normalized merchant and transaction direction.
    Strict occurrence requires both date proximity and amount proximity. If the
    merchant appears near the expected date but the amount moved outside tolerance,
    that is more useful to show as an amount change than as a generic match.
    Status priority is: matched evidence first, then missing patterns become
    possibly inactive, overdue, or expected based on missed expected cycles.
    """
    recurrence_settings = recurrence_settings or RECURRENCE_DETECTION_DEFAULTS
    amount_tolerance = recurrence_amount_tolerance(typical_amount, recurrence_settings)
    base_match = {
        "date_difference_days": None,
        "amount_difference": None,
        "matched_date": None,
        "matched_amount": None,
        "date_tolerance_days": recurrence_settings.date_tolerance_days,
        "likely_date_tolerance_days": likely_recurring_date_tolerance_days(recurrence_settings),
        "amount_tolerance": rounded_money_float(amount_tolerance),
        "missed_cycles_before_inactive": recurrence_settings.missed_cycles_before_inactive,
        "missed_cycles": None,
        "inactive_reason": None,
    }

    if not candidates:
        return missing_recurring_match(
            base_match,
            expected_date,
            evaluation_date,
            recurrence_settings,
            last_seen,
            frequency,
        )

    strict_matches: list[dict[str, Any]] = []
    date_matches: list[dict[str, Any]] = []
    likely_matches: list[dict[str, Any]] = []
    all_matches: list[dict[str, Any]] = []
    likely_date_tolerance_days = base_match["likely_date_tolerance_days"]

    for candidate in candidates:
        candidate_date = datetime.strptime(candidate["date"], "%Y-%m-%d").date()
        date_difference = (candidate_date - expected_date).days
        amount_difference = rounded_money_decimal(
            money_to_decimal(candidate["amount"]) - money_to_decimal(typical_amount)
        )
        candidate_match = {
            **base_match,
            "date_difference_days": date_difference,
            "amount_difference": rounded_money_float(amount_difference),
            "matched_date": candidate["date"],
            "matched_amount": rounded_money_float(candidate["amount"]),
        }
        within_date = abs(date_difference) <= recurrence_settings.date_tolerance_days
        within_amount = abs(amount_difference) <= amount_tolerance
        within_likely_date = abs(date_difference) <= likely_date_tolerance_days
        all_matches.append(candidate_match)

        if within_date and within_amount:
            strict_matches.append(candidate_match)
        if within_date:
            date_matches.append(candidate_match)
        elif within_likely_date and within_amount:
            likely_matches.append(candidate_match)

    # Prefer strict evidence, then near-date amount changes, then a soft-window
    # same-merchant occurrence with a plausible amount. Far-away merchant visits
    # are treated as missing evidence rather than weak matches.
    if strict_matches:
        best = min(strict_matches, key=recurring_match_sort_key)
        return {**best, "status": "occurred"}

    if date_matches:
        best = min(date_matches, key=recurring_match_sort_key)
        return {**best, "status": "amount_changed"}

    if likely_matches:
        best = min(likely_matches, key=recurring_match_sort_key)
        return {**best, "status": "likely_occurred"}

    return missing_recurring_match(
        base_match,
        expected_date,
        evaluation_date,
        recurrence_settings,
        last_seen,
        frequency,
    )


def likely_recurring_date_tolerance_days(recurrence_settings: Any) -> int:
    """Return the outer date window for weak same-merchant recurrence matches."""
    return max(0, int(recurrence_settings.date_tolerance_days)) * 2


def missing_recurring_match(
    base_match: Mapping[str, Any],
    expected_date: date,
    evaluation_date: date,
    recurrence_settings: Any,
    last_seen: date | None,
    frequency: str | None,
) -> dict[str, Any]:
    """Return missing recurrence status details for unmatched current-month evidence."""
    overdue_after = expected_date + timedelta(days=recurrence_settings.date_tolerance_days)
    inactive_details = possible_inactive_details(
        expected_date,
        evaluation_date,
        last_seen,
        frequency,
        recurrence_settings,
    )
    if inactive_details:
        return {
            **base_match,
            **inactive_details,
            "status": "possibly_inactive",
        }
    return {
        **base_match,
        "status": "overdue" if evaluation_date > overdue_after else "expected",
    }


def recurring_match_sort_key(match: Mapping[str, Any]) -> tuple[int, float, str]:
    """Build match sort key."""
    return (
        abs(match["date_difference_days"] or 0),
        abs(match["amount_difference"] or 0),
        match["matched_date"] or "",
    )


def recurrence_amount_tolerance(typical_amount: MoneyValue | None, recurrence_settings: Any = None) -> Decimal:
    """Build amount tolerance."""
    recurrence_settings = recurrence_settings or RECURRENCE_DETECTION_DEFAULTS
    typical_amount = abs(money_to_decimal(typical_amount))
    return max(
        money_to_decimal(recurrence_settings.amount_tolerance_absolute),
        typical_amount * money_to_decimal(recurrence_settings.amount_tolerance_percent),
    )


def possible_inactive_details(
    expected_date: date,
    evaluation_date: date,
    last_seen: date | None,
    frequency: str | None,
    recurrence_settings: Any,
) -> dict[str, Any] | None:
    """Return deterministic inactive details when a missing pattern looks stale.

    A row can become possibly inactive only after its expected date plus the date
    tolerance has passed. Monthly-like items use missed month cycles, so the
    default setting of two missed cycles roughly maps to 60-90 days depending on
    month length. Other known frequencies use conservative day intervals.
    """
    if not last_seen:
        return None

    inactive_after = expected_date + timedelta(days=recurrence_settings.date_tolerance_days)
    if evaluation_date <= inactive_after:
        return None

    missed_cycles = missed_recurring_cycles(last_seen, expected_date, frequency)
    if missed_cycles >= recurrence_settings.missed_cycles_before_inactive:
        return {
            "missed_cycles": missed_cycles,
            "inactive_reason": "This pattern has missed multiple expected cycles.",
        }

    return None


def missed_recurring_cycles(last_seen: date, expected_date: date, frequency: str | None) -> int:
    """Handle missed recurring cycles."""
    if last_seen >= expected_date:
        return 0

    if frequency in {"Monthly-like", "Irregular recurring", None, ""}:
        return max(0, ((expected_date.year - last_seen.year) * 12) + expected_date.month - last_seen.month)

    if frequency == "Annual":
        return max(0, expected_date.year - last_seen.year)

    interval_days = {
        "Weekly": 7,
        "Biweekly": 14,
        "Quarterly": 91,
    }.get(frequency or "", 30)
    return max(0, (expected_date - last_seen).days // interval_days)


def recurring_confidence_label(observed_months: int, recurrence_settings: Any = None) -> str:
    """Return an explainable confidence label without exposing raw scores.

    This first implementation uses only the recurrence evidence already computed:
    how many distinct months the merchant/direction pattern appeared in. Date and
    amount variability can be added later without changing the table contract.
    """
    recurrence_settings = recurrence_settings or RECURRENCE_DETECTION_DEFAULTS
    high_confidence_months = recurrence_settings.minimum_occurrences + 3
    medium_confidence_months = recurrence_settings.minimum_occurrences + 1
    if observed_months >= high_confidence_months:
        return "High"
    if observed_months >= medium_confidence_months:
        return "Medium"
    return "Low"


def recent_recurring_occurrences(occurrences: Iterable[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """Handle recent recurring occurrences."""
    return sorted(
        occurrences,
        key=lambda occurrence: occurrence["date"],
        reverse=True,
    )[:12]


def recurring_frequency_label(dates: Iterable[date], months: Iterable[str]) -> str:
    """Classify recurrence frequency with deterministic interval rules.

    The row eligibility remains unchanged; this only labels the existing pattern.
    Exact interval detection is attempted first. Month-gap checks handle common
    monthly, quarterly, and annual patterns when day-of-month movement makes day
    intervals noisy.
    """
    unique_dates = sorted(set(dates))
    if len(unique_dates) < 2:
        return "Irregular recurring"

    intervals = [
        (current_date - previous_date).days for previous_date, current_date in zip(unique_dates, unique_dates[1:])
    ]

    if interval_match_ratio(intervals, 6, 8) >= 0.6:
        return "Weekly"
    if interval_match_ratio(intervals, 12, 16) >= 0.6:
        return "Biweekly"
    if interval_match_ratio(intervals, 350, 380) >= 0.6:
        return "Annual"
    if interval_match_ratio(intervals, 80, 100) >= 0.6:
        return "Quarterly"
    if interval_match_ratio(intervals, 27, 35) >= 0.6:
        return "Monthly-like"

    month_numbers = sorted(month_number(month_key) for month_key in months)
    month_gaps = [
        current_month - previous_month for previous_month, current_month in zip(month_numbers, month_numbers[1:])
    ]
    if month_gap_match_ratio(month_gaps, {12}) >= 0.6:
        return "Annual"
    if month_gap_match_ratio(month_gaps, {3}) >= 0.6:
        return "Quarterly"
    if month_gap_match_ratio(month_gaps, {1, 2}) >= 0.6:
        return "Monthly-like"

    return "Irregular recurring"


def interval_match_ratio(intervals: Iterable[int], minimum: int, maximum: int) -> float:
    """Handle interval match ratio."""
    intervals = list(intervals)
    if not intervals:
        return 0
    matches = len([interval for interval in intervals if minimum <= interval <= maximum])
    return matches / len(intervals)


def month_gap_match_ratio(month_gaps: Iterable[int], expected_gaps: set[int]) -> float:
    """Return gap match ratio."""
    month_gaps = list(month_gaps)
    if not month_gaps:
        return 0
    matches = len([gap for gap in month_gaps if gap in expected_gaps])
    return matches / len(month_gaps)
