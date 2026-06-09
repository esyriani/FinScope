"""Flask routes for the jobs feature."""

from collections.abc import Mapping
from typing import Any

from flask import Blueprint, flash, jsonify, redirect, render_template, request, url_for
from flask.typing import ResponseReturnValue

from finance_app.background.runner import (
    AI_JOB_QUEUE,
    cancel_background_job,
    cancel_queued_background_jobs,
    count_background_jobs,
    get_background_job,
    list_background_jobs,
    undo_background_job,
)
from finance_app.core.config import settings
from finance_app.core.filters import format_datetime
from finance_app.core.i18n import gettext
from finance_app.core.query import parse_page
from finance_app.database.engine import db_core_transaction
from finance_app.modules.auth.permissions import PERMISSION_MANAGE_JOBS, permission_required
from finance_app.modules.settings.runtime import get_int_setting
from finance_app.modules.upload import workflow as upload_workflow

jobs_bp = Blueprint("jobs", __name__)


@jobs_bp.route("/jobs")
@permission_required(PERMISSION_MANAGE_JOBS)
def jobs() -> str:
    """Render the background jobs page."""
    page = parse_page(request.args.get("page"))
    with db_core_transaction() as conn:
        page_size = get_int_setting(conn, "default_table_page_size", settings.default_table_page_size)

    total_count = count_background_jobs()
    total_pages = max(1, (total_count + page_size - 1) // page_size)
    page = min(page, total_pages)
    offset = (page - 1) * page_size

    return render_template(
        "jobs.html",
        jobs=list_background_jobs(limit=page_size, offset=offset),
        page=page,
        page_size=page_size,
        total_count=total_count,
        total_pages=total_pages,
        page_start=offset + 1 if total_count else 0,
        page_end=min(offset + page_size, total_count),
    )


@jobs_bp.route("/jobs/<job_id>.json")
@permission_required(PERMISSION_MANAGE_JOBS)
def job_status(job_id: str) -> ResponseReturnValue:
    """Return the current status for a background job."""
    job = get_background_job(job_id)
    if job is None:
        return jsonify({"error": gettext("Job not found.")}), 404
    return jsonify(job_status_payload(job))


def job_status_payload(job: Mapping[str, Any]) -> dict[str, Any]:
    """Return a JSON-ready job snapshot with formatted log timestamps."""
    payload = dict(job)
    payload["progress_log"] = [
        {
            **entry,
            "timestamp_label": format_datetime(entry.get("timestamp")),
        }
        for entry in job.get("progress_log") or []
    ]
    return payload


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
    with db_core_transaction() as conn:
        unknown_count = upload_workflow.count_unknown_transactions(conn)

    if not unknown_count:
        flash(gettext("No unknown transactions need AI categorization."))
        return redirect(next_url)

    job_id = upload_workflow.queue_all_unknown_llm_categorization()
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


def jobs_redirect_target() -> str:
    """Return a safe redirect target for jobs actions."""
    target = request.form.get("next", "").strip()
    if target.startswith("/jobs"):
        return target

    return url_for("jobs.jobs")
