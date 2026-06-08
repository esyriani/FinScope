"""Money value helpers.

Provides conversion helpers for fixed-scale SQLAlchemy money values. Persistence
uses decimal-compatible schema types, while read models can explicitly convert
amounts to floats for JSON payloads and existing presentation code.
"""

from decimal import Decimal, ROUND_HALF_UP

from finance_app.core.config import settings

MONEY_QUANTUM = Decimal("0.01")


def quantize_money(value):
    """Return a Decimal rounded to the persisted two-decimal money scale."""
    if value in (None, ""):
        return None

    decimal_value = value if isinstance(value, Decimal) else Decimal(str(value))
    return decimal_value.quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)


def quantize_display_money(value, places=2):
    """Return a Decimal rounded for money display at the requested precision."""
    if value in (None, ""):
        return None

    decimal_value = value if isinstance(value, Decimal) else Decimal(str(value))
    quantum = Decimal("1") if places == 0 else Decimal("1").scaleb(-int(places))
    return decimal_value.quantize(quantum, rounding=ROUND_HALF_UP)


def money_to_decimal(value, default=Decimal("0")):
    """Return a Decimal for internal finance calculations."""
    if value in (None, ""):
        return default
    return value if isinstance(value, Decimal) else Decimal(str(value))


def optional_money_to_decimal(value):
    """Return a fixed-scale money value as a Decimal, preserving null values."""
    return None if value is None else money_to_decimal(value)


def rounded_money_decimal(value, default=Decimal("0")):
    """Return a two-decimal Decimal for internal finance calculations."""
    if value is None:
        return default
    return quantize_money(value)


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
    return float(rounded_money_decimal(value))


def format_money_display(value, places=2, symbol=None):
    """Format a money value with the configured currency symbol for display."""
    if value is None:
        return ""

    amount = quantize_display_money(value, places=places)
    formatted = f"{amount:,.{int(places)}f}".replace(",", " ")
    currency_symbol = settings.currency_symbol if symbol is None else str(symbol)
    return f"{formatted} {currency_symbol}".strip()


def format_signed_money_display(value, places=2, symbol=None):
    """Format a signed money value with the configured currency symbol."""
    amount = money_to_decimal(value)
    prefix = "+" if amount > 0 else "-" if amount < 0 else ""
    return f"{prefix}{format_money_display(abs(amount), places=places, symbol=symbol)}"
