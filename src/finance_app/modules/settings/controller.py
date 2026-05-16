"""Flask routes for the settings feature."""

from flask import Blueprint, flash, redirect, render_template, request, url_for

from finance_app.modules.settings.service import (
    build_settings_context,
    save_settings_from_form,
    validate_openai_model_from_form,
)


settings_bp = Blueprint("settings_page", __name__)


@settings_bp.route("/settings", methods=["GET", "POST"])
def settings_page():
    """Render the settings page page."""
    if request.method == "POST":
        try:
            save_settings_from_form(request.form)
            flash("Settings saved.")
        except ValueError as exc:
            flash(str(exc))

        return redirect(url_for("settings_page.settings_page"))

    return render_template("settings.html", **build_settings_context())


@settings_bp.route("/settings/openai-model/validate", methods=["POST"])
def validate_openai_model():
    """Validate openai model."""
    flash(validate_openai_model_from_form(request.form))
    return redirect(url_for("settings_page.settings_page"))
