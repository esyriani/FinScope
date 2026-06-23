"""Tests for settings form parsing helpers."""

from types import SimpleNamespace

import pytest
from werkzeug.datastructures import MultiDict

from finance_app.modules.settings.forms import (
    clean_openai_model,
    format_decimal,
    format_probability,
    normalize_theme_mode,
    parse_general_settings_form,
    parse_global_settings_form,
    parse_statement_types_form,
)


def settings_defaults():
    """Return app settings defaults needed by form parsers."""
    return SimpleNamespace(
        default_table_page_size=25,
        default_comparison_max_years=5,
        default_comparison_insight_card_limit=4,
        default_home_top_category_limit=6,
        default_dashboard_top_driver_limit=5,
        default_merchant_table_limit=10,
        default_merchant_suggestion_limit=5,
        default_rule_preview_limit=20,
        default_rule_audit_transaction_limit=200,
        default_recurrence_minimum_occurrences=3,
        default_recurrence_date_tolerance_days=5,
        default_recurrence_missed_cycles_before_inactive=2,
        default_categorization_model="gpt-default",
        default_ui_language="en",
        locale="en",
    )


def test_parse_general_settings_form_normalizes_user_settings():
    """Verify general settings parsing keeps bounded numeric and locale values."""
    form = MultiDict(
        [
            ("default_table_page_size", "50"),
            ("comparison_max_years", "4"),
            ("comparison_insight_card_limit", "3"),
            ("home_top_category_limit", "7"),
            ("dashboard_top_driver_limit", "4"),
            ("merchant_table_limit", "8"),
            ("merchant_suggestion_limit", "6"),
            ("rule_preview_limit", "9"),
            ("rule_audit_transaction_limit", "100"),
            ("theme_mode", "dark"),
            ("ui_language", "fr-CA"),
        ]
    )

    parsed = parse_general_settings_form(form, settings_defaults())

    assert parsed["default_table_page_size"] == 50
    assert parsed["comparison_max_years"] == 4
    assert parsed["dashboard_top_driver_limit"] == 4
    assert parsed["merchant_suggestion_limit"] == 6
    assert parsed["theme_mode"] == "dark"
    assert parsed["ui_language"] == "fr"


def test_parse_global_settings_form_normalizes_owner_settings():
    """Verify global settings parsing handles probabilities, toggles, and statement types."""
    form = MultiDict(
        [
            ("llm_confidence_threshold", "0.80"),
            ("llm_review_threshold", "0.60"),
            ("verify_threshold", "0.90"),
            ("transaction_ai_rerun_enabled", ""),
            ("confirm_ai_token_usage_enabled", "1"),
            ("openai_model", "gpt-4.1-mini"),
            ("recurrence_minimum_occurrences", "3"),
            ("recurrence_date_tolerance_days", "5"),
            ("recurrence_amount_tolerance_absolute", "10"),
            ("recurrence_amount_tolerance_percent", "0.15"),
            ("recurrence_missed_cycles_before_inactive", "2"),
            ("statement_type_ids", "1"),
            ("statement_type_names", "Checking"),
            ("statement_type_parser_types", "bank_account"),
            ("statement_type_import_modes", "ledger"),
            ("statement_type_default_account_types", "checking"),
        ]
    )

    parsed = parse_global_settings_form(form, settings_defaults())

    assert parsed["llm_confidence_threshold"] == 0.8
    assert parsed["transaction_ai_rerun_enabled"] is False
    assert parsed["confirm_ai_token_usage_enabled"] is True
    assert parsed["openai_model"] == "gpt-4.1-mini"
    assert parsed["statement_types"] == [
        {
            "id": "1",
            "name": "Checking",
            "parser_type": "bank_account",
            "import_mode": "ledger",
            "default_account_type": "checking",
        }
    ]


def test_parse_statement_types_form_requires_one_named_type():
    """Verify blank statement type submissions are rejected."""
    form = MultiDict(
        [
            ("statement_type_ids", ""),
            ("statement_type_names", ""),
            ("statement_type_parser_types", ""),
        ]
    )

    with pytest.raises(ValueError, match="Add at least one statement type"):
        parse_statement_types_form(form)


def test_settings_formatters_and_cleaners():
    """Verify display formatting and constrained text cleaners."""
    assert format_probability("0.5") == "0.50"
    assert format_decimal("10.500") == "10.5"
    assert clean_openai_model("gpt-4.1-mini") == "gpt-4.1-mini"
    assert clean_openai_model("bad model!") == ""
    assert normalize_theme_mode("dark") == "dark"
    assert normalize_theme_mode("unknown") == "light"
