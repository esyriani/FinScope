"""Form parsing and validation helpers for the settings feature."""

import re

from finance_app.core.constants import THEME_MODE_DARK, THEME_MODE_LIGHT
from finance_app.core.i18n import normalize_language
from finance_app.modules.recurring.settings import RECURRENCE_DETECTION_DEFAULTS


def parse_settings_form(form, app_settings):
    """Parse the full owner settings form."""
    return {
        **parse_general_settings_form(form, app_settings),
        **parse_global_settings_form(form, app_settings),
    }


def parse_general_settings_form(form, app_settings):
    """Parse user-specific General settings from a submitted form."""
    return {
        "default_table_page_size": parse_positive_int(
            form.get("default_table_page_size"),
            app_settings.default_table_page_size,
        ),
        "comparison_max_years": parse_positive_int(
            form.get("comparison_max_years"),
            app_settings.default_comparison_max_years,
            minimum=2,
            label="Comparison default years",
        ),
        "home_top_category_limit": parse_positive_int(
            form.get("home_top_category_limit"),
            app_settings.default_home_top_category_limit,
        ),
        "merchant_table_limit": parse_positive_int(
            form.get("merchant_table_limit"),
            app_settings.default_merchant_table_limit,
        ),
        "rule_preview_limit": parse_positive_int(
            form.get("rule_preview_limit"),
            app_settings.default_rule_preview_limit,
        ),
        "rule_audit_transaction_limit": parse_positive_int(
            form.get("rule_audit_transaction_limit"),
            app_settings.default_rule_audit_transaction_limit,
            label="Rule audit transaction limit",
        ),
        "theme_mode": normalize_theme_mode(form.get("theme_mode", THEME_MODE_DARK)),
        "ui_language": normalize_language(form.get("ui_language", app_settings.locale)),
    }


def parse_global_settings_form(form, app_settings):
    """Parse owner-only advanced settings from a submitted form."""
    return {
        "llm_confidence_threshold": parse_probability(
            form.get("llm_confidence_threshold"),
            "LLM confidence threshold",
        ),
        "llm_review_threshold": parse_probability(
            form.get("llm_review_threshold"),
            "LLM review threshold",
        ),
        "verify_threshold": parse_probability(
            form.get("verify_threshold"),
            "Verify threshold",
        ),
        "auto_llm_categorization_enabled": parse_checkbox(
            form.get("auto_llm_categorization_enabled")
        ),
        "transaction_ai_rerun_enabled": parse_checkbox(
            form.get("transaction_ai_rerun_enabled")
        ),
        "openai_model": clean_openai_model(form.get("openai_model"))
        or app_settings.default_categorization_model,
        "recurrence_minimum_occurrences": parse_positive_int(
            form.get("recurrence_minimum_occurrences"),
            RECURRENCE_DETECTION_DEFAULTS.minimum_occurrences,
            label="Recurring minimum occurrences",
        ),
        "recurrence_date_tolerance_days": parse_positive_int(
            form.get("recurrence_date_tolerance_days"),
            RECURRENCE_DETECTION_DEFAULTS.date_tolerance_days,
            label="Recurring date tolerance days",
        ),
        "recurrence_amount_tolerance_absolute": parse_non_negative_float(
            form.get("recurrence_amount_tolerance_absolute"),
            "Recurring absolute amount tolerance",
        ),
        "recurrence_amount_tolerance_percent": parse_probability(
            form.get("recurrence_amount_tolerance_percent"),
            "Recurring amount tolerance percent",
        ),
        "recurrence_missed_cycles_before_inactive": parse_positive_int(
            form.get("recurrence_missed_cycles_before_inactive"),
            RECURRENCE_DETECTION_DEFAULTS.missed_cycles_before_inactive,
            label="Recurring missed cycles before inactive",
        ),
        "statement_types": parse_statement_types_form(form),
    }


def parse_positive_int(value, fallback, minimum=1, label="Numeric settings"):
    """Parse positive int."""
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        raise ValueError(f"{label} must be a valid positive integer.") from None

    if parsed < minimum:
        raise ValueError(f"{label} must be at least {minimum}.")

    return parsed if parsed > 0 else fallback


def normalize_minimum_int(value, minimum, fallback):
    """Normalize minimum int."""
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        parsed = fallback
    return str(max(minimum, parsed))


def parse_probability(value, label):
    """Parse probability."""
    try:
        parsed = float(str(value).strip())
    except (TypeError, ValueError):
        raise ValueError(f"{label} must be a number between 0 and 1.") from None

    if parsed < 0 or parsed > 1:
        raise ValueError(f"{label} must be between 0 and 1.")

    return parsed


def parse_non_negative_float(value, label):
    """Parse non negative float."""
    try:
        parsed = float(str(value).strip())
    except (TypeError, ValueError):
        raise ValueError(f"{label} must be a non-negative number.") from None

    if parsed < 0:
        raise ValueError(f"{label} must be at least 0.")

    return parsed


def parse_checkbox(value):
    """Return whether a checkbox-style form value is enabled."""
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def format_probability(value):
    """Format probability."""
    return f"{float(value):.2f}"


def format_decimal(value):
    """Format decimal."""
    return f"{float(value):g}"


def clean_openai_model(value):
    """Clean openai model."""
    text = str(value or "").strip()
    if not text:
        return ""
    return text if re.fullmatch(r"[A-Za-z0-9._:/+-]+", text) else ""


def normalize_theme_mode(value):
    """Normalize theme mode."""
    return THEME_MODE_DARK if str(value or "").strip().lower() == THEME_MODE_DARK else THEME_MODE_LIGHT


def parse_statement_types_form(form):
    """Parse statement types form."""
    ids = form.getlist("statement_type_ids")
    names = form.getlist("statement_type_names")
    parser_types = form.getlist("statement_type_parser_types")
    import_modes = form.getlist("statement_type_import_modes")
    default_account_types = form.getlist("statement_type_default_account_types")
    if len(import_modes) < len(names):
        import_modes.extend([""] * (len(names) - len(import_modes)))
    if len(default_account_types) < len(names):
        default_account_types.extend([""] * (len(names) - len(default_account_types)))
    statement_types = []

    for type_id, name, parser_type, import_mode, default_account_type in zip(
        ids,
        names,
        parser_types,
        import_modes,
        default_account_types,
    ):
        normalized_name = str(name or "").strip()
        if not normalized_name:
            continue
        statement_types.append(
            {
                "id": type_id,
                "name": normalized_name,
                "parser_type": parser_type,
                "import_mode": import_mode,
                "default_account_type": default_account_type,
            }
        )

    if not statement_types:
        raise ValueError("Add at least one statement type.")

    return statement_types
