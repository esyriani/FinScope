"""Flask routes for the home feature."""

from flask import Blueprint, render_template

from finance_app.modules.home.service import build_home_context

home_bp = Blueprint("home", __name__)


@home_bp.route("/")
def home() -> str:
    """Render the home page."""
    return render_template("home.html", **build_home_context())
