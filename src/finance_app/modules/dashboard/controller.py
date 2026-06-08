"""Flask routes for the dashboard feature."""

from flask import Blueprint, render_template, request

from finance_app.modules.dashboard.service import build_dashboard_context

dashboard_bp = Blueprint("dashboard", __name__)


@dashboard_bp.route("/dashboard")
def dashboard():
    """Render the dashboard page."""
    return render_template("dashboard.html", **build_dashboard_context(request.args))
