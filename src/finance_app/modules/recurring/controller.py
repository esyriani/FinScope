"""Flask routes for the recurring feature."""

from flask import Blueprint, jsonify, render_template, request
from flask.typing import ResponseReturnValue

from finance_app.database.engine import db_core_transaction
from finance_app.modules.auth.permissions import PERMISSION_EDIT_RECURRING, permission_required
from finance_app.modules.recurring.forms import parse_expected_day, recurring_pattern_payload
from finance_app.modules.recurring.patterns import normalize_active, upsert_recurring_pattern
from finance_app.modules.recurring.service import build_recurring_page_context

recurring_bp = Blueprint("recurring", __name__)


@recurring_bp.route("/recurring")
def recurring() -> str:
    """Render the recurring page."""
    return render_template("recurring.html", **build_recurring_page_context(request.args))


@recurring_bp.route("/recurring/patterns/confirm", methods=["POST"])
@permission_required(PERMISSION_EDIT_RECURRING)
def confirm_recurring_pattern() -> ResponseReturnValue:
    """Handle the confirm recurring pattern route."""
    payload = request.get_json(silent=True) or {}
    try:
        pattern = recurring_pattern_payload(payload)
    except ValueError as exc:
        return jsonify({"ok": False, "message": str(exc)}), 400

    with db_core_transaction() as conn:
        upsert_recurring_pattern(
            conn,
            pattern["pattern_key"],
            pattern["merchant"],
            pattern["type"],
            merchant_id=pattern["merchant_id"],
            user_status="confirmed",
            active=1,
        )
    return jsonify({"ok": True, "userStatus": "confirmed", "active": 1})


@recurring_bp.route("/recurring/patterns/ignore", methods=["POST"])
@permission_required(PERMISSION_EDIT_RECURRING)
def ignore_recurring_pattern() -> ResponseReturnValue:
    """Ignore recurring pattern."""
    payload = request.get_json(silent=True) or {}
    try:
        pattern = recurring_pattern_payload(payload)
    except ValueError as exc:
        return jsonify({"ok": False, "message": str(exc)}), 400

    with db_core_transaction() as conn:
        upsert_recurring_pattern(
            conn,
            pattern["pattern_key"],
            pattern["merchant"],
            pattern["type"],
            merchant_id=pattern["merchant_id"],
            user_status="ignored",
            active=0,
        )
    return jsonify({"ok": True, "userStatus": "ignored", "active": 0})


@recurring_bp.route("/recurring/patterns/edit", methods=["POST"])
@permission_required(PERMISSION_EDIT_RECURRING)
def edit_recurring_pattern() -> ResponseReturnValue:
    """Edit recurring pattern."""
    payload = request.get_json(silent=True) or {}
    try:
        pattern = recurring_pattern_payload(payload)
    except ValueError as exc:
        return jsonify({"ok": False, "message": str(exc)}), 400

    expected_day = parse_expected_day(payload.get("expectedDate"))
    with db_core_transaction() as conn:
        upsert_recurring_pattern(
            conn,
            pattern["pattern_key"],
            pattern["merchant"],
            pattern["type"],
            merchant_id=pattern["merchant_id"],
            user_status="edited",
            frequency=payload.get("frequency"),
            expected_day=expected_day,
            typical_amount=payload.get("typicalAmount"),
            date_tolerance_days=payload.get("dateToleranceDays"),
            amount_tolerance=payload.get("amountTolerance"),
            active=payload.get("active", 1),
        )
    active = normalize_active(payload.get("active", 1))
    return jsonify({"ok": True, "userStatus": "edited", "active": active})
