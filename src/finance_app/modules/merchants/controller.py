"""Flask routes for merchant lookup UI.

Exposes authenticated JSON endpoints used by shared browser autocomplete
controls. Routes are read-only and do not mutate merchant data.
"""

from typing import Any

from flask import Blueprint, jsonify, request
from flask_login import login_required  # type: ignore[import-untyped]

from finance_app.modules.merchants.service import build_merchant_suggestions_payload

ResponseReturnValue = Any

merchants_bp = Blueprint("merchants", __name__)


@merchants_bp.get("/merchants/suggestions")
@login_required
def merchant_suggestions() -> ResponseReturnValue:
    """Return known merchants matching partial query text as JSON."""
    return jsonify(build_merchant_suggestions_payload(request.args))
