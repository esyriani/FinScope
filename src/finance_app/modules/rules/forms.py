"""Form parsing helpers for category rule editors."""

from decimal import Decimal, InvalidOperation

from finance_app.core.money import parse_money_text
from finance_app.modules.categories.repository import normalize_optional_account_id, normalize_rule_direction
from finance_app.modules.categories.service import normalize_merchant_description


def parse_amount_bounds(min_value: object, max_value: object) -> tuple[Decimal | None, Decimal | None]:
    """Parse optional minimum and maximum rule amount bounds."""
    amount_min = parse_optional_amount(min_value)
    amount_max = parse_optional_amount(max_value)

    if amount_min is not None and amount_max is not None and amount_min > amount_max:
        amount_min, amount_max = amount_max, amount_min

    return amount_min, amount_max


def parse_optional_amount(value: object) -> Decimal | None:
    """Parse an optional amount field from a form value."""
    text = str(value or "").strip()
    if not text:
        return None
    text = normalize_form_amount_text(text)

    try:
        amount = parse_money_text(text)
    except (InvalidOperation, ValueError):
        raise ValueError("Amount bounds must be valid numbers.") from None
    if amount is None:
        raise ValueError("Amount bounds must be valid numbers.")
    return amount


def normalize_form_amount_text(value: str) -> str:
    """Return amount text with the rule-form decimal comma convention applied."""
    if "," not in value or "." in value:
        return value
    parts = value.split(",")
    if len(parts) == 2 and len(parts[1].strip()) > 2:
        return f"{parts[0]}.{parts[1]}"
    return value


def normalize_rule_keyword(value: object, fallback: object = "") -> str:
    """Normalize a rule keyword or derive it from a fallback."""
    keyword = normalize_merchant_description(value)
    if keyword:
        return keyword
    return normalize_merchant_description(fallback)


def parse_rule_account_id(value: object) -> int | None:
    """Parse an optional rule account constraint from form data."""
    return normalize_optional_account_id(value)


def parse_rule_direction(value: object) -> str:
    """Parse a rule direction constraint from form data."""
    return normalize_rule_direction(value)


def amount_bounds_label(amount_min: object | None, amount_max: object | None) -> str:
    """Format amount bounds for flash messages."""
    if amount_min is None and amount_max is None:
        return ""
    if amount_min is not None and amount_max is not None and amount_min == amount_max:
        return f" at amount {amount_min:.2f}"
    if amount_min is None:
        return f" up to {amount_max:.2f}"
    if amount_max is None:
        return f" from {amount_min:.2f}"
    return f" from {amount_min:.2f} to {amount_max:.2f}"
