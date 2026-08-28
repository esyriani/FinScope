"""Register browser translation messages owned by feature modules."""

from finance_app.core.client_i18n import register_client_translation_messages
from finance_app.modules.calendar.client_i18n import CLIENT_TRANSLATION_MESSAGES as CALENDAR_CLIENT_MESSAGES
from finance_app.modules.categories.client_i18n import CLIENT_TRANSLATION_MESSAGES as CATEGORY_CLIENT_MESSAGES
from finance_app.modules.jobs.client_i18n import CLIENT_TRANSLATION_MESSAGES as JOBS_CLIENT_MESSAGES
from finance_app.modules.merchants.client_i18n import CLIENT_TRANSLATION_MESSAGES as MERCHANT_CLIENT_MESSAGES
from finance_app.modules.recurring.client_i18n import CLIENT_TRANSLATION_MESSAGES as RECURRING_CLIENT_MESSAGES
from finance_app.modules.reports.client_i18n import CLIENT_TRANSLATION_MESSAGES as REPORTS_CLIENT_MESSAGES
from finance_app.modules.review.client_i18n import CLIENT_TRANSLATION_MESSAGES as REVIEW_CLIENT_MESSAGES
from finance_app.modules.rules.client_i18n import CLIENT_TRANSLATION_MESSAGES as RULES_CLIENT_MESSAGES
from finance_app.modules.settings.client_i18n import CLIENT_TRANSLATION_MESSAGES as SETTINGS_CLIENT_MESSAGES
from finance_app.modules.upload.client_i18n import CLIENT_TRANSLATION_MESSAGES as UPLOAD_CLIENT_MESSAGES

MODULE_CLIENT_TRANSLATION_CATALOGS = (
    CALENDAR_CLIENT_MESSAGES,
    CATEGORY_CLIENT_MESSAGES,
    JOBS_CLIENT_MESSAGES,
    MERCHANT_CLIENT_MESSAGES,
    RECURRING_CLIENT_MESSAGES,
    REPORTS_CLIENT_MESSAGES,
    REVIEW_CLIENT_MESSAGES,
    RULES_CLIENT_MESSAGES,
    SETTINGS_CLIENT_MESSAGES,
    UPLOAD_CLIENT_MESSAGES,
)


def register_module_client_translation_messages() -> None:
    """Register browser translation message ids owned by feature modules."""
    for messages in MODULE_CLIENT_TRANSLATION_CATALOGS:
        register_client_translation_messages(messages)
