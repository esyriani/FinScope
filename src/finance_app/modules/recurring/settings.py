"""Settings structures for the recurring feature."""

from dataclasses import dataclass


@dataclass(frozen=True)
class RecurrenceDetectionSettings:
    """Documented defaults for deterministic recurring activity detection.

    Field names intentionally map to the product setting names:
    - minimumOccurrences: pattern eligibility threshold
    - dateToleranceDays: strict match window around the expected date
    - amountToleranceAbsolute: minimum absolute amount tolerance in dollars
    - amountTolerancePercent: percentage amount tolerance relative to typical amount
    - missedCyclesBeforeInactive: reserved for a future inactive status
    """

    minimum_occurrences: int = 3
    date_tolerance_days: int = 5
    amount_tolerance_absolute: float = 10
    amount_tolerance_percent: float = 0.15
    missed_cycles_before_inactive: int = 2


RECURRENCE_DETECTION_DEFAULTS = RecurrenceDetectionSettings()

RECURRENCE_DETECTION_SETTING_NAMES = {
    "minimumOccurrences": "recurrence_minimum_occurrences",
    "dateToleranceDays": "recurrence_date_tolerance_days",
    "amountToleranceAbsolute": "recurrence_amount_tolerance_absolute",
    "amountTolerancePercent": "recurrence_amount_tolerance_percent",
    "missedCyclesBeforeInactive": "recurrence_missed_cycles_before_inactive",
}
