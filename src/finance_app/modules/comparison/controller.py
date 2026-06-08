"""Flask routes for the comparison feature."""

from flask import Blueprint, render_template, request

from finance_app.modules.comparison.service import build_comparison_context

comparison_bp = Blueprint("comparison", __name__)


@comparison_bp.route("/comparison")
def comparison():
    """Render the comparison page."""
    return render_template("comparison.html", **build_comparison_context(request.args))
