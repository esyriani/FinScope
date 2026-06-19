"""Flask application factory for FinScope."""

from flask import Flask, g, request

from finance_app.core.assets import register_asset_helpers
from finance_app.core.config import settings
from finance_app.core.constants import STATIC_DIR, TEMPLATE_DIR, THEME_MODE_DARK, THEME_MODE_LIGHT
from finance_app.core.csrf import register_csrf
from finance_app.core.filters import register_filters
from finance_app.core.i18n import (
    SUPPORTED_LANGUAGES,
    client_translations,
    gettext,
    locale_for_language,
    normalize_language,
)
from finance_app.database.engine import register_core_db
from finance_app.modules import register_blueprints
from finance_app.modules.auth import register_auth
from finance_app.modules.auth.permissions import current_user_can
from finance_app.modules.categories.service import get_builtin_category_names
from finance_app.modules.categories.tag_filters import UNTAGGED_TAG_FILTER
from finance_app.modules.settings.runtime import get_setting_with_fallback

CLIENT_TRANSLATION_MESSAGES = (
    "Amount changed",
    "Annual",
    "Amount difference: {difference}.",
    "Actual",
    "Auto-detect",
    "Review AI usage",
    "AI usage estimate ready.",
    "Batches",
    "Continue",
    "Context usage",
    "Estimated total",
    "Estimated total tokens",
    "Exceeds model limit",
    "Expected response",
    "Expected response tokens",
    "High AI usage",
    "Input",
    "Input tokens",
    "It fits within the selected model's context limit.",
    "It is within the selected model's context limit.",
    "Largest batch",
    "AI requests",
    "Loading AI usage estimate...",
    "Low AI usage",
    "May exceed model limit",
    "Model",
    "Moderate AI usage",
    "Near model limit",
    "No estimate available.",
    "No AI request would be sent for this action.",
    "Not available",
    "One or more estimates are approximate.",
    "Records included",
    "Review the estimated AI usage before continuing.",
    "Run AI",
    "Technical details",
    "The command will be split into {count} model requests.",
    "This AI command is estimated to use {tokens}.",
    "This estimate is above the selected model's context limit.",
    "This estimate is close to the selected model's context limit.",
    "This estimate exceeds the selected model's context limit.",
    "This estimate is near the selected model's context limit.",
    "This estimate may exceed the selected model's context limit.",
    "Token estimate could not be loaded.",
    "Tokenizer",
    "Total",
    "Transactions",
    "Unknown",
    "{count} tokens",
    "{used} / {limit} tokens",
    "{used} / {limit} tokens, about {percent}% of the context window",
    "Biweekly",
    "Best current-month match: {date}.",
    "Cash flow",
    "Category",
    "Categories",
    "Categorize selected ({count})",
    "Chart {number}",
    "Could not save recurring pattern.",
    "CSV",
    "Cancel",
    "Checking account",
    "Choose date format",
    "Choose how imported rows are interpreted.",
    "Choose the date format before importing.",
    "Choose which rows to export from this table.",
    "Choose whether imports add new transactions or update transactions already imported.",
    "Close",
    "Confirmed by you",
    "Confirm recurring if this pattern is useful; remove it if it is noise.",
    "Confirm recurring",
    "Confirmed recurring.",
    "Credit card",
    "Could not load every table page for export.",
    "Date",
    "Date difference: {difference}.",
    "Date format detected from unambiguous rows.",
    "Date format selected for this import.",
    "days",
    "Default role for accounts created from this statement type.",
    "Detected because this merchant appeared in {months} distinct months with a typical amount of {amount}.",
    "Description",
    "Difference",
    "Displayed rows",
    "Edit",
    "Edited by you",
    "Excel",
    "Entire table",
    "Enter a keyword to preview matches.",
    "Expand",
    "Expand {label}",
    "Export {label}",
    "Export displayed rows only? Choose Cancel to export the entire table.",
    "Export rows",
    "Frequency",
    "High",
    "AI categorization completed: {summary}",
    "AI request issue in batch {start}-{end}: {error_type}: {detail}",
    "Processed {current} of {total}; {updated} categorized.",
    "Processing...",
    "Processing {start}-{end} of {total}; {updated} categorized so far.",
    "Starting AI categorization for {total} unknown transactions.",
    "Starting batch {start}-{end} of {total}.",
    "Batch {start}-{end} failed: {error_type}: {detail}",
    "Batch {start}-{end} kept {unknown} transaction unknown for review.",
    "Batch {start}-{end} kept {unknown} transactions unknown for review.",
    "Starting selected transaction recategorization for {total} transactions.",
    "Starting selected recategorization batch {start}-{end} of {total}.",
    "Recategorizing {start}-{end} of {total}; {updated} updated so far.",
    "Recategorized {current} of {total}; {updated} updated.",
    "Finished selected recategorization batch {start}-{end}: {processed} processed; {updated} updated total.",
    "Selected transaction recategorization completed: {summary}",
    "Cancellation requested; stopping before the next batch.",
    "Cancellation requested; waiting for the current batch to finish.",
    "Income",
    "Income and credits",
    "Inactive",
    "Ignored",
    "Irregular recurring",
    "Likely occurred",
    "Loading preview...",
    "Low",
    "Merchant suggestions unavailable.",
    "Medium",
    "Monthly-like",
    "No file selected",
    "Monthly spending distribution",
    "Names appear in upload and statement history.",
    "No log entries yet.",
    "No preview rows available.",
    "No date-format choice is needed for this file.",
    "No unknown transactions needed AI categorization.",
    "No action needed unless the pattern changed.",
    "No active transactions match this rule.",
    "No current-month merchant and direction match was found near the expected date. Expected date uses a +/-{days} day tolerance.",
    "No merchants found.",
    "No recent occurrences available.",
    "Not reviewed yet",
    "No transactions selected. The category will apply to the whole group.",
    "Net cash flow",
    "Needs attention: {count}",
    "Next",
    "Occurred",
    "Opening statement...",
    "Overdue",
    "Personal",
    "Previous",
    "Preview could not be loaded.",
    "Preview unavailable.",
    "Preparing statement preview...",
    "Min",
    "Q1",
    "Median",
    "Mean",
    "Q3",
    "Max",
    "n/a",
    "Expected",
    "Possibly inactive",
    "Quarterly",
    "Recurring activity",
    "Recurring items - {date}",
    "Recurring pattern changes saved.",
    "Refresh ({seconds})",
    "Remove",
    "Remove {label}",
    "Review this pattern before relying on it.",
    "Spending",
    "Table {number}",
    "Tag",
    "Tags",
    "Composition",
    "The processing progress could not be refreshed.",
    "The processing table could not be refreshed.",
    "Finished batch {start}-{end}: {processed} processed; {updated} categorized total.",
    "Job cancelled before it started.",
    "Job cancelled: {result}",
    "Job failed: {error}",
    "This pattern has missed multiple expected cycles.",
    "This recurring pattern has saved user edits.",
    "Tolerances: +/-{days} days and +/-{amount}.",
    "Typical",
    "Transactions - {date}",
    "Wait for it to appear, or edit the expected date if the timing changed.",
    "Waiting for progress update.",
    "Weekly",
    "Showing {start}-{end} of {total} rows",
    "{percent}% complete",
    "{count} active matching transaction.",
    "{count} active matching transactions.",
    "{count} distinct month",
    "{count} distinct months",
    "{count} recurring item",
    "{count} recurring items",
    "{count} selected. The category will apply only to selected transactions.",
    "{count} selected",
)


def create_app() -> Flask:
    """Create and configure the Flask application."""
    app = Flask(
        __name__,
        template_folder=TEMPLATE_DIR,
        static_folder=STATIC_DIR,
    )
    app.secret_key = settings.secret_key
    app.config["MAX_CONTENT_LENGTH"] = settings.max_content_length
    app.config["FINANCE_SETTINGS"] = settings
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["SESSION_COOKIE_SECURE"] = settings.secure_cookies
    app.config["REMEMBER_COOKIE_HTTPONLY"] = True
    app.config["REMEMBER_COOKIE_SAMESITE"] = "Lax"
    app.config["REMEMBER_COOKIE_SECURE"] = settings.secure_cookies

    register_core_db(app)
    register_auth(app)

    @app.before_request
    def load_runtime_language() -> None:
        """Load the selected UI language for the current request."""
        if request.endpoint == "static":
            g.ui_language = normalize_language(settings.locale)
            return
        language = get_setting_with_fallback("ui_language", settings.locale)
        g.ui_language = normalize_language(language)

    @app.context_processor
    def inject_runtime_settings() -> dict[str, object]:
        """Expose runtime UI settings to every template render."""
        theme_mode = get_setting_with_fallback("theme_mode", THEME_MODE_DARK)
        ui_language = normalize_language(getattr(g, "ui_language", settings.locale))
        return {
            "ui_theme": (THEME_MODE_DARK if str(theme_mode).strip().lower() == THEME_MODE_DARK else THEME_MODE_LIGHT),
            "ui_language": ui_language,
            "ui_locale": locale_for_language(ui_language),
            "supported_languages": SUPPORTED_LANGUAGES,
            "_": gettext,
            "client_i18n": client_translations(ui_language, CLIENT_TRANSLATION_MESSAGES),
            "currency_symbol": settings.currency_symbol,
            "untagged_tag_filter_value": UNTAGGED_TAG_FILTER,
            "category_filter_builtin_exclusions": get_builtin_category_names(),
            "current_user_can": current_user_can,
        }

    register_filters(app)
    register_asset_helpers(app)
    register_csrf(app)
    register_blueprints(app)

    return app
