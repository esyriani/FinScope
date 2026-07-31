"""Money value helpers.

Provides conversion helpers for fixed-scale SQLAlchemy money values. Persistence
uses decimal-compatible schema types, while read models can explicitly convert
amounts to floats for JSON payloads and existing presentation code.
"""

import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
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

    amount = _decimal_value(value).quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)
    return abs(amount) if amount == 0 else amount


def canonical_money_text(value: MoneyValue | None) -> str:
    """Return a fixed-scale string for stable money identities."""
    amount = quantize_money(value)
    return "" if amount is None else f"{amount:.2f}"


def parse_money_text(value: object) -> Decimal | None:
    """Parse common statement or form money text to a fixed-scale Decimal."""
    if value is None:
        return None

    text = str(value).strip()
    if text in {"", "-", "--", "N/A"}:
        return None

    negative = False
    text = text.replace("\xa0", " ")
    text = re.sub(r"(?i)\bCAD\b", "", text)
    text = re.sub(r"(?i)CA\$", "", text)
    text = text.replace("$", "").strip()

    if text.startswith("(") and text.endswith(")"):
        negative = True
        text = text[1:-1]

    if text.endswith("-"):
        negative = True
        text = text[:-1]

    if text.startswith("-"):
        negative = True
        text = text[1:]

    text = re.sub(r"[^0-9,.]", "", text)
    if not text:
        return None

    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif "," in text:
        parts = text.split(",")
        if len(parts[-1]) in {1, 2}:
            text = "".join(parts[:-1]) + "." + parts[-1]
        else:
            text = text.replace(",", "")

    try:
        amount = _decimal_value(text)
    except InvalidOperation as exc:
        raise ValueError(f"Invalid money value: {value}") from exc
    if negative:
        amount = -amount
    return cast(Decimal, quantize_money(amount))


def quantize_display_money(value: MoneyValue | None, places: int = 2) -> Decimal | None:
    """Return a Decimal rounded for money display at the requested precision."""
    if value is None or value == "":
        return None

    quantum = Decimal("1") if places == 0 else Decimal("1").scaleb(-int(places))
    amount = _decimal_value(value).quantize(quantum, rounding=ROUND_HALF_UP)
    return abs(amount) if amount == 0 else amount


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
    amount = _decimal_value(value).quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)
    return abs(amount) if amount == 0 else amount


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
