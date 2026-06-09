"""Form parsing helpers for category rule editors."""

from finance_app.modules.categories.repository import normalize_optional_account_id, normalize_rule_direction
from finance_app.modules.categories.service import normalize_merchant_description


def parse_amount_bounds(min_value: object, max_value: object) -> tuple[float | None, float | None]:
    """Parse optional minimum and maximum rule amount bounds."""
    amount_min = parse_optional_amount(min_value)
    amount_max = parse_optional_amount(max_value)

    if amount_min is not None and amount_max is not None and amount_min > amount_max:
        amount_min, amount_max = amount_max, amount_min

    return amount_min, amount_max


def parse_optional_amount(value: object) -> float | None:
    """Parse an optional amount field from a form value."""
    text = str(value or "").strip()
    if not text:
        return None

    text = text.replace(" ", "").replace("$", "")
    if "," in text and "." not in text:
        text = text.replace(",", ".")
    else:
        text = text.replace(",", "")

    try:
        return round(float(text), 2)
    except ValueError:
        raise ValueError("Amount bounds must be valid numbers.") from None


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


def amount_bounds_label(amount_min: float | None, amount_max: float | None) -> str:
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
