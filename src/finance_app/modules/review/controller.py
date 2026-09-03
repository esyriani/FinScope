"""Flask routes for the review feature."""

from flask import Blueprint, flash, jsonify, redirect, render_template, request, url_for
from flask.typing import ResponseReturnValue

from finance_app.background.runner import get_background_job
from finance_app.core.i18n import gettext
from finance_app.modules.auth.permissions import PERMISSION_EDIT_TRANSACTIONS, permission_required
from finance_app.modules.review.service import (
    build_review_context,
    queue_review_group_application,
)

review_bp = Blueprint("review", __name__)


@review_bp.route("/review")
@permission_required(PERMISSION_EDIT_TRANSACTIONS)
def review() -> str:
    """Render the review page."""
    return render_template("review.html", **build_review_context(request.args))


@review_bp.route("/review/apply", methods=["POST"])
@permission_required(PERMISSION_EDIT_TRANSACTIONS)
def apply_review_group() -> ResponseReturnValue:
    """Apply review group."""
    next_url = review_redirect_target()
    try:
        result = queue_review_group_application(
            request.form.get("merchant_key", ""),
            request.form.get("transaction_id", ""),
            request.form.getlist("transaction_ids"),
            request.form.get("category", ""),
            request.form.getlist("tags"),
            request.form.get("create_rule"),
            request.form.get("keyword", ""),
            request.form.get("amount_min", ""),
            request.form.get("amount_max", ""),
        )
    except ValueError as exc:
        flash(gettext(str(exc)))
        return redirect(next_url)

    if not result.get("ok", True):
        message = gettext(str(result.get("message") or "Review could not be queued. Try again."))
        flash(message)
        if is_fetch_request():
            return jsonify({"ok": False, "message": message, "refresh_url": next_url}), int(result.get("status", 503))
        return redirect(next_url)

    job_id = result["job_id"]
    review_target = result["target"]
    flash(
        gettext(
            "Review {target} queued in the background. Track progress on the Processing page. Job: {job_id}",
            target=gettext(review_target),
            job_id=job_id[:8],
        )
    )
    if is_fetch_request():
        return jsonify(
            {
                "ok": True,
                "refresh_url": next_url,
                "job_status_url": url_for("review.review_job_status", job_id=job_id),
            }
        )

    return redirect(next_url)


@review_bp.route("/review/jobs/<job_id>.json")
@permission_required(PERMISSION_EDIT_TRANSACTIONS)
def review_job_status(job_id: str) -> ResponseReturnValue:
    """Return the minimal background job status needed by the review page."""
    job = get_background_job(job_id)
    if job is None:
        return jsonify({"ok": False, "message": gettext("Job not found.")}), 404

    return jsonify({"ok": True, "status": job["status"]})


def review_redirect_target() -> str:
    """Render the review redirect target page."""
    target = request.form.get("next", "").strip()
    if target.startswith("/review"):
        return target

    return url_for("review.review")


def is_fetch_request() -> bool:
    """Return whether the current request came from the shared AJAX form handler."""
    return request.headers.get("X-Requested-With") == "fetch"
