"""Runtime setting key catalog and default values.

Defines the owner-managed and personal setting keys shared by database seeding,
settings forms, and runtime readers. Request-aware lookup logic remains in the
settings feature module.
"""

from finance_app.core.config import settings

CONFIRM_AI_TOKEN_USAGE_SETTING_KEY = "confirm_ai_token_usage_enabled"

SETTINGS_DEFAULTS: dict[str, str] = {
    "default_table_page_size": str(settings.default_table_page_size),
    "comparison_max_years": str(settings.default_comparison_max_years),
    "comparison_insight_card_limit": str(settings.default_comparison_insight_card_limit),
    "home_top_category_limit": str(settings.default_home_top_category_limit),
    "dashboard_top_driver_limit": str(settings.default_dashboard_top_driver_limit),
    "pinned_report_limit": str(settings.default_pinned_report_limit),
    "merchant_table_limit": str(settings.default_merchant_table_limit),
    "merchant_suggestion_limit": str(settings.default_merchant_suggestion_limit),
    "rule_preview_limit": str(settings.default_rule_preview_limit),
    "rule_audit_transaction_limit": str(settings.default_rule_audit_transaction_limit),
    "theme_mode": settings.default_theme_mode,
    "ui_language": settings.default_ui_language,
    "llm_confidence_threshold": str(settings.default_llm_confidence_threshold),
    "llm_review_threshold": str(settings.default_llm_review_threshold),
    "verify_threshold": str(settings.default_verify_threshold),
    "transaction_ai_rerun_enabled": "1" if settings.default_transaction_ai_rerun_enabled else "0",
    CONFIRM_AI_TOKEN_USAGE_SETTING_KEY: "1" if settings.default_confirm_ai_token_usage_enabled else "0",
    "openai_model": settings.default_categorization_model,
    "recurrence_minimum_occurrences": str(settings.default_recurrence_minimum_occurrences),
    "recurrence_date_tolerance_days": str(settings.default_recurrence_date_tolerance_days),
    "recurrence_amount_tolerance_absolute": str(settings.default_recurrence_amount_tolerance_absolute),
    "recurrence_amount_tolerance_percent": str(settings.default_recurrence_amount_tolerance_percent),
    "recurrence_missed_cycles_before_inactive": str(settings.default_recurrence_missed_cycles_before_inactive),
}

GENERAL_SETTING_KEYS: tuple[str, ...] = (
    "default_table_page_size",
    "comparison_max_years",
    "comparison_insight_card_limit",
    "home_top_category_limit",
    "dashboard_top_driver_limit",
    "pinned_report_limit",
    "merchant_table_limit",
    "merchant_suggestion_limit",
    "rule_preview_limit",
    "rule_audit_transaction_limit",
    "theme_mode",
    "ui_language",
)
GLOBAL_SETTING_KEYS: tuple[str, ...] = (
    "llm_confidence_threshold",
    "llm_review_threshold",
    "verify_threshold",
    "transaction_ai_rerun_enabled",
    CONFIRM_AI_TOKEN_USAGE_SETTING_KEY,
    "openai_model",
    "recurrence_minimum_occurrences",
    "recurrence_date_tolerance_days",
    "recurrence_amount_tolerance_absolute",
    "recurrence_amount_tolerance_percent",
    "recurrence_missed_cycles_before_inactive",
)
EDITABLE_SETTING_KEYS = GENERAL_SETTING_KEYS + GLOBAL_SETTING_KEYS
