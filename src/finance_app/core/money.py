"""Money value helpers.

Provides conversion helpers for fixed-scale SQLAlchemy money values. Persistence
uses decimal-compatible schema types, while read models can explicitly convert
amounts to floats for JSON payloads and existing presentation code.
"""

from decimal import ROUND_HALF_UP, Decimal
from typing import TypeAlias, cast

from finance_app.core.config import settings

MONEY_QUANTUM = Decimal("0.01")
MoneyValue: TypeAlias = Decimal | int | float | str


def _decimal_value(value: MoneyValue) -> Decimal:
    """Return a Decimal using string conversion for non-Decimal scalar inputs."""
    return value if isinstance(value, Decimal) else Decimal(str(value))


def quantize_money(value: MoneyValue | None) -> Decimal | None:
    """Return a Decimal rounded to the persisted two-decimal money scale."""
    if value is None or value == "":
        return None

    return _decimal_value(value).quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)


def quantize_display_money(value: MoneyValue | None, places: int = 2) -> Decimal | None:
    """Return a Decimal rounded for money display at the requested precision."""
    if value is None or value == "":
        return None

    quantum = Decimal("1") if places == 0 else Decimal("1").scaleb(-int(places))
    return _decimal_value(value).quantize(quantum, rounding=ROUND_HALF_UP)


def money_to_decimal(value: MoneyValue | None, default: Decimal = Decimal("0")) -> Decimal:
    """Return a Decimal for internal finance calculations."""
    if value is None or value == "":
        return default
    return _decimal_value(value)


def optional_money_to_decimal(value: MoneyValue | None) -> Decimal | None:
    """Return a fixed-scale money value as a Decimal, preserving null values."""
    return None if value is None else money_to_decimal(value)


def rounded_money_decimal(value: MoneyValue | None, default: Decimal = Decimal("0")) -> Decimal:
    """Return a two-decimal Decimal for internal finance calculations."""
    if value is None or value == "":
        return default
    return _decimal_value(value).quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)


def money_to_float(value: MoneyValue | None, default: float = 0.0) -> float:
    """Return a presentation-safe float for a fixed-scale money value."""
    if value is None:
        return default
    return float(value)


def optional_money_to_float(value: MoneyValue | None) -> float | None:
    """Return a fixed-scale money value as a float, preserving null values."""
    return None if value is None else money_to_float(value)


def rounded_money_float(value: MoneyValue | None, default: float = 0.0) -> float:
    """Return a two-decimal presentation float for a fixed-scale money value."""
    if value is None:
        return default
    return float(rounded_money_decimal(value))


def format_money_display(value: MoneyValue | None, places: int = 2, symbol: str | None = None) -> str:
    """Format a money value with the configured currency symbol for display."""
    if value is None:
        return ""

    amount = cast(Decimal, quantize_display_money(value, places=places))
    formatted = f"{amount:,.{int(places)}f}".replace(",", " ")
    currency_symbol = settings.currency_symbol if symbol is None else str(symbol)
    return f"{formatted} {currency_symbol}".strip()


def format_signed_money_display(value: MoneyValue | None, places: int = 2, symbol: str | None = None) -> str:
    """Format a signed money value with the configured currency symbol."""
    amount = money_to_decimal(value)
    prefix = "+" if amount > 0 else "-" if amount < 0 else ""
    return f"{prefix}{format_money_display(abs(amount), places=places, symbol=symbol)}"
