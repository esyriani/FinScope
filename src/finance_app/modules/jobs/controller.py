"""Flask routes for the jobs feature."""

from flask import Blueprint, flash, jsonify, redirect, render_template, request, url_for

from finance_app.background.runner import (
    count_background_jobs,
    get_background_job,
    list_background_jobs,
    undo_background_job,
)
from finance_app.core.config import settings
from finance_app.database.engine import db_core_transaction
from finance_app.modules.settings.runtime import get_int_setting
from finance_app.core.query import parse_page


jobs_bp = Blueprint("jobs", __name__)


@jobs_bp.route("/jobs")
def jobs():
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
def job_status(job_id):
    """Return the current status for a background job."""
    job = get_background_job(job_id)
    if job is None:
        return jsonify({"error": "Job not found."}), 404
    return jsonify(job)


@jobs_bp.route("/jobs/<job_id>/undo", methods=["POST"])
def undo_job(job_id):
    """Request undo for a completed background job."""
    next_url = jobs_redirect_target()

    try:
        job = undo_background_job(job_id)
    except ValueError as exc:
        flash(str(exc))
        return redirect(next_url)
    except Exception as exc:
        flash(f"Could not undo job: {type(exc).__name__}: {exc}")
        return redirect(next_url)

    if job is None:
        flash("Job not found.")
    else:
        flash(job.get("undo_result") or "Job undone.")

    return redirect(next_url)


def jobs_redirect_target():
    """Return a safe redirect target for jobs actions."""
    target = request.form.get("next", "").strip()
    if target.startswith("/jobs"):
        return target

    return url_for("jobs.jobs")
