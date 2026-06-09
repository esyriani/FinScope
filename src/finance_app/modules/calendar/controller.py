"""Flask routes for the calendar feature."""

from flask import Blueprint, render_template, request

from finance_app.modules.calendar.service import build_calendar_context

calendar_bp = Blueprint("calendar_page", __name__)


@calendar_bp.route("/calendar")
def calendar_view() -> str:
    """Render the calendar view page."""
    return render_template("calendar.html", **build_calendar_context(request.args))
