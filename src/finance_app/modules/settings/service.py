"""Application orchestration for the settings feature."""

from flask_login import current_user

from finance_app.core.config import settings as app_settings
from finance_app.core.constants import (
    ACCOUNT_TYPES,
    STATEMENT_IMPORT_MODES,
    STATEMENT_TYPE_PARSER_TYPES,
    THEME_MODE_DARK,
    THEME_MODE_LIGHT,
)
from finance_app.core.i18n import SUPPORTED_LANGUAGES, normalize_language
from finance_app.database.engine import db_core_transaction
from finance_app.modules.recurring.settings import RECURRENCE_DETECTION_DEFAULTS
from finance_app.modules.settings.forms import (
    clean_openai_model,
    format_decimal,
    format_probability,
    normalize_minimum_int,
    normalize_theme_mode,
    parse_general_settings_form,
    parse_global_settings_form,
)
from finance_app.modules.auth.permissions import current_user_can, PERMISSION_MANAGE_GLOBAL_SETTINGS
from finance_app.modules.settings.runtime import (
    GENERAL_SETTING_KEYS,
    get_all_settings,
    get_statement_type_options,
    seed_runtime_settings,
    sync_statement_types,
    upsert_setting,
    upsert_user_setting,
)


GENERAL_SETTING_SAVE_KEYS = GENERAL_SETTING_KEYS


GLOBAL_STRING_SETTING_KEYS = (
    "openai_model",
    "recurrence_minimum_occurrences",
    "recurrence_date_tolerance_days",
    "recurrence_missed_cycles_before_inactive",
)

BOOLEAN_SETTING_KEYS = (
    "auto_llm_categorization_enabled",
    "transaction_ai_rerun_enabled",
)


PROBABILITY_SETTING_KEYS = (
    "llm_confidence_threshold",
    "llm_review_threshold",
    "verify_threshold",
    "recurrence_amount_tolerance_percent",
)


DECIMAL_SETTING_KEYS = (
    "recurrence_amount_tolerance_absolute",
)


def build_settings_context():
    """Build settings context."""
    can_manage_global_settings = current_user_can(PERMISSION_MANAGE_GLOBAL_SETTINGS)
    with db_core_transaction() as conn:
        seed_runtime_settings(conn)
        current = get_all_settings(conn)
        statement_types = get_statement_type_options(conn) if can_manage_global_settings else []

    return {
        "default_table_page_size": current.get("default_table_page_size", str(app_settings.default_table_page_size)),
        "comparison_max_years": normalize_minimum_int(
            current.get("comparison_max_years", str(app_settings.default_comparison_max_years)),
            2,
            app_settings.default_comparison_max_years,
        ),
        "home_top_category_limit": current.get("home_top_category_limit", str(app_settings.default_home_top_category_limit)),
        "merchant_table_limit": current.get("merchant_table_limit", str(app_settings.default_merchant_table_limit)),
        "rule_preview_limit": current.get("rule_preview_limit", str(app_settings.default_rule_preview_limit)),
        "rule_audit_transaction_limit": current.get(
            "rule_audit_transaction_limit",
            str(app_settings.default_rule_audit_transaction_limit),
        ),
        "llm_confidence_threshold": current.get(
            "llm_confidence_threshold",
            format_probability(app_settings.default_llm_confidence_threshold),
        ),
        "llm_review_threshold": current.get(
            "llm_review_threshold",
            format_probability(app_settings.default_llm_review_threshold),
        ),
        "verify_threshold": current.get("verify_threshold", format_probability(app_settings.default_verify_threshold)),
        "auto_llm_categorization_enabled": str(
            current.get("auto_llm_categorization_enabled", "1")
        ).strip().lower() not in {"0", "false", "no", "off"},
        "transaction_ai_rerun_enabled": str(
            current.get(
                "transaction_ai_rerun_enabled",
                "1" if app_settings.default_transaction_ai_rerun_enabled else "0",
            )
        ).strip().lower() not in {"0", "false", "no", "off"},
        "openai_model": current.get("openai_model", app_settings.default_categorization_model),
        "recurrence_minimum_occurrences": current.get(
            "recurrence_minimum_occurrences",
            str(RECURRENCE_DETECTION_DEFAULTS.minimum_occurrences),
        ),
        "recurrence_date_tolerance_days": current.get(
            "recurrence_date_tolerance_days",
            str(RECURRENCE_DETECTION_DEFAULTS.date_tolerance_days),
        ),
        "recurrence_amount_tolerance_absolute": current.get(
            "recurrence_amount_tolerance_absolute",
            format_decimal(RECURRENCE_DETECTION_DEFAULTS.amount_tolerance_absolute),
        ),
        "recurrence_amount_tolerance_percent": current.get(
            "recurrence_amount_tolerance_percent",
            format_probability(RECURRENCE_DETECTION_DEFAULTS.amount_tolerance_percent),
        ),
        "recurrence_missed_cycles_before_inactive": current.get(
            "recurrence_missed_cycles_before_inactive",
            str(RECURRENCE_DETECTION_DEFAULTS.missed_cycles_before_inactive),
        ),
        "theme_mode": normalize_theme_mode(current.get("theme_mode", THEME_MODE_DARK)),
        "theme_mode_dark": THEME_MODE_DARK,
        "theme_mode_light": THEME_MODE_LIGHT,
        "ui_language": normalize_language(current.get("ui_language", app_settings.locale)),
        "supported_languages": SUPPORTED_LANGUAGES,
        "statement_types": statement_types,
        "statement_type_parser_types": STATEMENT_TYPE_PARSER_TYPES,
        "statement_import_modes": STATEMENT_IMPORT_MODES,
        "account_types": ACCOUNT_TYPES,
        "can_manage_global_settings": can_manage_global_settings,
    }


def save_settings_from_form(form):
    """Save user-bound General settings and owner-only advanced settings."""
    if not current_user.is_authenticated:
        raise ValueError("Please log in to continue.")

    general_values = parse_general_settings_form(form, app_settings)
    can_manage_global_settings = current_user_can(PERMISSION_MANAGE_GLOBAL_SETTINGS)
    global_values = (
        parse_global_settings_form(form, app_settings)
        if can_manage_global_settings
        else {}
    )

    with db_core_transaction() as conn:
        seed_runtime_settings(conn)
        for key in GENERAL_SETTING_SAVE_KEYS:
            upsert_user_setting(conn, current_user.id, key, str(general_values[key]))

        if not can_manage_global_settings:
            return

        for key in GLOBAL_STRING_SETTING_KEYS:
            upsert_setting(conn, key, str(global_values[key]))
        for key in BOOLEAN_SETTING_KEYS:
            upsert_setting(conn, key, "1" if global_values[key] else "0")
        for key in PROBABILITY_SETTING_KEYS:
            upsert_setting(conn, key, format_probability(global_values[key]))
        for key in DECIMAL_SETTING_KEYS:
            upsert_setting(conn, key, format_decimal(global_values[key]))

        sync_statement_types(conn, global_values["statement_types"])


def validate_openai_model_from_form(form):
    """Validate openai model from form."""
    model_name = clean_openai_model(form.get("openai_model")) or app_settings.default_categorization_model
    available, message = is_openai_model_available(model_name)
    return message if available else f"Model validation failed: {message}"


def is_openai_model_available(model_name):
    """Return whether openai model available."""
    if not app_settings.openai_api_key:
        return False, "Configure an OpenAI API key first."

    try:
        from openai import OpenAI
    except ImportError:
        return False, "Install the OpenAI Python package first."

    try:
        models = OpenAI(api_key=app_settings.openai_api_key, timeout=5).models.list()
    except Exception:
        return False, "Could not load models for the configured OpenAI API key."

    model_ids = {
        str(model.id)
        for model in getattr(models, "data", [])
    }
    if model_name in model_ids:
        return True, f"Model is available to this API key: {model_name}"

    return False, f"Model was not returned by the OpenAI models API: {model_name}"
