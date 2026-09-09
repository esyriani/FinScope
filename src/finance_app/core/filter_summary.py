"""Shared presentation helpers for page filter summaries.

These helpers shape selected filter labels for service-built view models. They
return plain strings and dictionaries for Jinja templates, avoid database or
request parsing, and translate only static UI labels rather than user data.
"""

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from finance_app.core.i18n import gettext

FilterSummaryItem = dict[str, str]


def filter_summary_item(label: object, value: object) -> FilterSummaryItem:
    """Return one template-ready filter summary item."""
    return {"label": str(label), "value": str(value)}


def selected_option_label(
    options: Iterable[object],
    selected_value: object,
    default_label: object,
    *,
    value_key: str = "value",
    label_key: str = "label",
) -> str:
    """Return a translated option label matching a selected form value."""
    for option in options:
        option_value, option_label = option_fields(option, value_key, label_key)
        if values_match(option_value, selected_value):
            return gettext(option_label)
    return gettext(default_label)


def selected_account_label(
    account_options: Iterable[Mapping[str, Any]],
    selected_account_id: int | None,
    *,
    empty_label: object = "All accounts",
) -> str:
    """Return a selected account name or the translated empty account label."""
    if selected_account_id is None:
        return gettext(empty_label)

    for account in account_options:
        if values_match(account.get("id"), selected_account_id):
            return str(account.get("name") or "")
    return gettext(empty_label)


def selected_values_label(
    values: Iterable[object] | None,
    empty_label: object,
    *,
    translated_value_labels: Mapping[str, object] | None = None,
) -> str:
    """Return a comma-separated summary for selected free-form values."""
    labels: list[str] = []
    value_label_map = translated_value_labels or {}
    for value in values or []:
        text = str(value or "").strip()
        if not text:
            continue
        replacement_label = value_label_map.get(text)
        labels.append(gettext(replacement_label) if replacement_label is not None else text)
    return ", ".join(labels) if labels else gettext(empty_label)


def value_or_default(value: object, default_label: object = "All") -> str:
    """Return a non-empty display value or a translated default label."""
    text = str(value or "").strip()
    return text if text else gettext(default_label)


def merchant_filter_input_label(
    selected_merchant_id: int | None,
    merchant_query: object,
    selected_merchant_label: object,
) -> str:
    """Return the display value for a merchant autocomplete filter input."""
    if selected_merchant_id is not None:
        return str(selected_merchant_label or "").strip()
    return str(merchant_query or "").strip()


def period_summary_value(
    label: object,
    selected_period: object,
    custom_period: object,
    date_from: object = "",
    date_to: object = "",
) -> str:
    """Return the display value used for a date-period filter summary."""
    period_label = str(label or "")
    if selected_period == custom_period and (date_from or date_to):
        start_label = str(date_from or gettext("Any"))
        end_label = str(date_to or gettext("Any"))
        return f"{period_label} ({start_label} {gettext('to')} {end_label})"
    return period_label


def option_fields(option: object, value_key: str, label_key: str) -> tuple[object, object]:
    """Return option value and label fields from tuple or mapping options."""
    if isinstance(option, Mapping):
        return option.get(value_key), option.get(label_key, "")
    if isinstance(option, Sequence) and not isinstance(option, (str, bytes)) and len(option) >= 2:
        return option[0], option[1]
    return None, ""


def values_match(left: object, right: object) -> bool:
    """Return whether two option values represent the same selected value."""
    if left == right:
        return True
    if left is None or right is None:
        return False
    return str(left) == str(right)
