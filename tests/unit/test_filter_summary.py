"""Tests for shared filter-summary presentation helpers."""

from finance_app.core.filter_summary import (
    merchant_filter_input_label,
    period_summary_value,
    selected_account_label,
    selected_option_label,
    selected_values_label,
    value_or_default,
)


def test_filter_summary_helpers_shape_selected_labels_without_translating_user_data():
    """Verify selected filter labels are shaped consistently for page contexts."""
    account_options = [{"id": 7, "name": "Daily Checking"}]
    tuple_options = (("all", "All time"), ("custom", "Custom range"))
    mapping_options = [{"value": "net", "label": "Net cash flow"}]

    assert selected_account_label(account_options, 7) == "Daily Checking"
    assert selected_account_label(account_options, None) == "All accounts"
    assert selected_option_label(tuple_options, "custom", "All time") == "Custom range"
    assert selected_option_label(mapping_options, "net", "Spending") == "Net cash flow"
    assert (
        selected_values_label(
            ["Food", "__untagged__"], "All tags", translated_value_labels={"__untagged__": "Untagged"}
        )
        == "Food, Untagged"
    )
    assert value_or_default("") == "All"
    assert merchant_filter_input_label(42, "typed text", "METRO GROCERY") == "METRO GROCERY"
    assert merchant_filter_input_label(None, "typed text", "METRO GROCERY") == "typed text"
    assert period_summary_value("Custom range", "custom", "custom", "2026-01-01", "") == (
        "Custom range (2026-01-01 to Any)"
    )
