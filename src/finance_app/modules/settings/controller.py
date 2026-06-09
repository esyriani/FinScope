"""Flask routes for the settings feature."""

from typing import Any

from flask import Blueprint, flash, redirect, render_template, request, url_for

from finance_app.core.i18n import gettext
from finance_app.modules.auth.permissions import (
    PERMISSION_MANAGE_GLOBAL_SETTINGS,
    permission_required,
)
from finance_app.modules.settings.service import (
    build_settings_context,
    save_settings_from_form,
    validate_openai_model_from_form,
)

settings_bp = Blueprint("settings_page", __name__)


@settings_bp.route("/settings", methods=["GET", "POST"])
def settings_page() -> Any:
    """Render the settings page page."""
    if request.method == "POST":
        try:
            save_settings_from_form(request.form)
            flash(gettext("Settings saved."))
        except ValueError as exc:
            flash(gettext(str(exc)))

        return redirect(url_for("settings_page.settings_page"))

    return render_template("settings.html", **build_settings_context())


@settings_bp.route("/settings/openai-model/validate", methods=["POST"])
@permission_required(PERMISSION_MANAGE_GLOBAL_SETTINGS)
def validate_openai_model() -> Any:
    """Validate openai model."""
    flash(gettext(validate_openai_model_from_form(request.form)))
    return redirect(url_for("settings_page.settings_page"))
