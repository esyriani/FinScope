"""Flask routes for the recurring feature."""

from flask import Blueprint, jsonify, render_template, request
from flask.typing import ResponseReturnValue

from finance_app.modules.auth.permissions import PERMISSION_EDIT_RECURRING, permission_required
from finance_app.modules.recurring import service as recurring_service

recurring_bp = Blueprint("recurring", __name__)


@recurring_bp.route("/recurring")
def recurring() -> str:
    """Render the recurring page."""
    return render_template("recurring.html", **recurring_service.build_recurring_page_context(request.args))


@recurring_bp.route("/recurring/patterns/confirm", methods=["POST"])
@permission_required(PERMISSION_EDIT_RECURRING)
def confirm_recurring_pattern() -> ResponseReturnValue:
    """Handle the confirm recurring pattern route."""
    payload = request.get_json(silent=True) or {}
    try:
        result = recurring_service.confirm_recurring_pattern_action(payload)
    except ValueError as exc:
        return jsonify({"ok": False, "message": str(exc)}), 400

    return jsonify(result)


@recurring_bp.route("/recurring/patterns/ignore", methods=["POST"])
@permission_required(PERMISSION_EDIT_RECURRING)
def ignore_recurring_pattern() -> ResponseReturnValue:
    """Ignore recurring pattern."""
    payload = request.get_json(silent=True) or {}
    try:
        result = recurring_service.ignore_recurring_pattern_action(payload)
    except ValueError as exc:
        return jsonify({"ok": False, "message": str(exc)}), 400

    return jsonify(result)


@recurring_bp.route("/recurring/patterns/edit", methods=["POST"])
@permission_required(PERMISSION_EDIT_RECURRING)
def edit_recurring_pattern() -> ResponseReturnValue:
    """Edit recurring pattern."""
    payload = request.get_json(silent=True) or {}
    try:
        result = recurring_service.edit_recurring_pattern_action(payload)
    except ValueError as exc:
        return jsonify({"ok": False, "message": str(exc)}), 400

    return jsonify(result)
