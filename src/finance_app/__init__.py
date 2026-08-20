"""Flask application factory for FinScope."""

from flask import Flask

from finance_app.core.assets import register_asset_helpers, register_static_asset_mimetypes
from finance_app.core.client_i18n import (
    client_translation_messages,
    register_core_client_translation_messages,
)
from finance_app.core.config import settings
from finance_app.core.constants import STATIC_DIR, TEMPLATE_DIR
from finance_app.core.csrf import register_csrf
from finance_app.core.filters import register_filters
from finance_app.core.i18n import (
    SUPPORTED_LANGUAGES,
    client_translations,
    gettext,
)
from finance_app.database.engine import register_core_db
from finance_app.modules import register_blueprints
from finance_app.modules.auth import register_auth
from finance_app.modules.auth.permissions import current_user_can
from finance_app.modules.categories.tag_filters import UNTAGGED_TAG_FILTER
from finance_app.modules.client_i18n import register_module_client_translation_messages
from finance_app.runtime_context import current_runtime_template_context, load_request_runtime_context


def create_app() -> Flask:
    """Create and configure the Flask application."""
    register_static_asset_mimetypes()
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
    register_core_client_translation_messages()
    register_module_client_translation_messages()

    @app.before_request
    def load_runtime_context() -> None:
        """Load shared runtime context for the current request."""
        load_request_runtime_context()

    @app.context_processor
    def inject_runtime_settings() -> dict[str, object]:
        """Expose runtime UI settings to every template render."""
        runtime_context = current_runtime_template_context()
        return {
            "ui_theme": runtime_context.ui_theme,
            "ui_language": runtime_context.ui_language,
            "ui_locale": runtime_context.ui_locale,
            "supported_languages": SUPPORTED_LANGUAGES,
            "_": gettext,
            "client_i18n": client_translations(runtime_context.ui_language, client_translation_messages()),
            "currency_symbol": settings.currency_symbol,
            "untagged_tag_filter_value": UNTAGGED_TAG_FILTER,
            "category_filter_builtin_exclusions": runtime_context.category_filter_builtin_exclusions,
            "current_user_can": current_user_can,
        }

    register_filters(app)
    register_asset_helpers(app)
    register_csrf(app)
    register_blueprints(app)

    return app
