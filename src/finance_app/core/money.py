"""Money value helpers.

Provides conversion helpers for fixed-scale SQLAlchemy money values. Persistence
uses decimal-compatible schema types, while read models can explicitly convert
amounts to floats for JSON payloads and existing presentation code.
"""

from decimal import Decimal, ROUND_HALF_UP


MONEY_QUANTUM = Decimal("0.01")


def quantize_money(value):
    """Return a Decimal rounded to the persisted two-decimal money scale."""
    if value in (None, ""):
        return None

    decimal_value = value if isinstance(value, Decimal) else Decimal(str(value))
    return decimal_value.quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)


def money_to_float(value, default=0.0):
    """Return a presentation-safe float for a fixed-scale money value."""
    if value is None:
        return default
    return float(value)


def optional_money_to_float(value):
    """Return a fixed-scale money value as a float, preserving null values."""
    return None if value is None else money_to_float(value)


def rounded_money_float(value, default=0.0):
    """Return a two-decimal presentation float for a fixed-scale money value."""
    if value is None:
        return default
    return float(quantize_money(value))
