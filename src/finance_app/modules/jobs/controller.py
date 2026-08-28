"""Flask routes for the jobs feature."""

from flask import Blueprint, flash, jsonify, redirect, render_template, request, url_for
from flask.typing import ResponseReturnValue

from finance_app.background.runner import (
    AI_JOB_QUEUE,
    cancel_background_job,
    cancel_queued_background_jobs,
    get_background_job,
    undo_background_job,
)
from finance_app.core.i18n import gettext
from finance_app.modules.auth.permissions import PERMISSION_MANAGE_JOBS, permission_required
from finance_app.modules.categories.llm_token_confirmation import ai_token_estimate_confirmed
from finance_app.modules.categories.llm_token_presenter import localize_token_estimate_result
from finance_app.modules.categories.llm_tokens import AI_TOKEN_ESTIMATE_REQUIRED_MESSAGE
from finance_app.modules.jobs import service as jobs_service

jobs_bp = Blueprint("jobs", __name__)


@jobs_bp.route("/jobs")
@permission_required(PERMISSION_MANAGE_JOBS)
def jobs() -> str:
    """Render the background jobs page."""
    return render_template("jobs.html", **jobs_service.build_jobs_context(request.args))


@jobs_bp.route("/jobs/<job_id>.json")
@permission_required(PERMISSION_MANAGE_JOBS)
def job_status(job_id: str) -> ResponseReturnValue:
    """Return the current status for a background job."""
    job = get_background_job(job_id)
    if job is None:
        return jsonify({"error": gettext("Job not found.")}), 404
    return jsonify(jobs_service.job_status_payload(job))


@jobs_bp.route("/jobs/<job_id>/undo", methods=["POST"])
@permission_required(PERMISSION_MANAGE_JOBS)
def undo_job(job_id: str) -> ResponseReturnValue:
    """Request undo for a completed background job."""
    next_url = jobs_redirect_target()

    try:
        job = undo_background_job(job_id)
    except ValueError as exc:
        flash(gettext(str(exc)))
        return redirect(next_url)
    except Exception as exc:
        flash(
            gettext(
                "Could not undo job: {error_type}: {detail}",
                error_type=type(exc).__name__,
                detail=exc,
            )
        )
        return redirect(next_url)

    if job is None:
        flash(gettext("Job not found."))
    else:
        flash(gettext(job.get("undo_result") or "Job undone."))

    return redirect(next_url)


@jobs_bp.route("/jobs/<job_id>/cancel", methods=["POST"])
@permission_required(PERMISSION_MANAGE_JOBS)
def cancel_job(job_id: str) -> ResponseReturnValue:
    """Request cancellation for a queued or running background job."""
    next_url = jobs_redirect_target()

    try:
        job = cancel_background_job(job_id)
    except ValueError as exc:
        flash(gettext(str(exc)))
        return redirect(next_url)

    if job is None:
        flash(gettext("Job not found."))
    elif job["status"] == "cancelled":
        flash(gettext("Job cancelled."))
    else:
        flash(gettext("Cancellation requested. The job will stop after the current batch."))

    return redirect(next_url)


@jobs_bp.route("/jobs/ai/cancel-queued", methods=["POST"])
@permission_required(PERMISSION_MANAGE_JOBS)
def cancel_queued_ai_jobs() -> ResponseReturnValue:
    """Cancel all queued AI jobs that have not started yet."""
    next_url = jobs_redirect_target()
    cancelled_count = cancel_queued_background_jobs(queue=AI_JOB_QUEUE)
    flash(
        gettext(
            ("Cancelled {count} queued AI job." if cancelled_count == 1 else "Cancelled {count} queued AI jobs."),
            count=cancelled_count,
        )
    )
    return redirect(next_url)


@jobs_bp.route("/jobs/ai/categorize-unknowns", methods=["POST"])
@permission_required(PERMISSION_MANAGE_JOBS)
def categorize_all_unknowns() -> ResponseReturnValue:
    """Queue AI categorization for all active unknown transactions."""
    next_url = jobs_redirect_target()
    unknown_count = jobs_service.count_all_unknown_transactions()

    if not unknown_count:
        flash(gettext("No unknown transactions need AI categorization."))
        return redirect(next_url)

    if not ai_token_estimate_confirmed(request.form):
        flash(gettext(AI_TOKEN_ESTIMATE_REQUIRED_MESSAGE))
        return redirect(next_url)

    result = jobs_service.queue_all_unknown_categorization()
    if not result.get("ok"):
        flash(gettext("No unknown transactions need AI categorization."))
        return redirect(next_url)

    job_id = result["job_id"]
    unknown_count = result["unknown_count"]
    flash(
        gettext(
            (
                "AI categorization queued for {count} unknown transaction. Job: {job_id}"
                if unknown_count == 1
                else "AI categorization queued for {count} unknown transactions. Job: {job_id}"
            ),
            count=unknown_count,
            job_id=job_id[:8],
        )
    )
    return redirect(next_url)


@jobs_bp.route("/jobs/ai/categorize-unknowns/estimate", methods=["POST"])
@permission_required(PERMISSION_MANAGE_JOBS)
def estimate_categorize_all_unknowns() -> ResponseReturnValue:
    """Return a token estimate for all-unknown AI categorization."""
    result = jobs_service.estimate_all_unknown_categorization()
    status_code = 200 if result.get("ok") else 400
    return jsonify(localized_json_result(result)), status_code


def jobs_redirect_target() -> str:
    """Return a safe redirect target for jobs actions."""
    target = request.form.get("next", "").strip()
    if target.startswith("/jobs"):
        return target

    return url_for("jobs.jobs")


def localized_json_result(result: dict[str, object]) -> dict[str, object]:
    """Return a JSON result with its top-level message localized."""
    return localize_token_estimate_result(result, gettext)
