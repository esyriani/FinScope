"""Application orchestration for the jobs feature.

This module keeps background-job page assembly and AI queue decisions outside
Flask routes. Controllers remain responsible for redirects, flashes, and HTTP
status codes while this service owns settings reads and upload workflow calls.
"""

from collections.abc import Mapping
from typing import Any

from finance_app.background.runner import count_background_jobs, list_background_jobs
from finance_app.core.config import settings
from finance_app.core.filters import format_datetime
from finance_app.core.query import parse_page
from finance_app.database.engine import db_core_transaction
from finance_app.modules.settings.runtime import confirm_ai_token_usage_enabled, get_int_setting
from finance_app.modules.upload import ai_workflow as upload_ai_workflow
from finance_app.modules.upload import service as upload_service


def build_jobs_context(args: Any) -> dict[str, Any]:
    """Build the background jobs page context."""
    page = parse_page(args.get("page"))
    with db_core_transaction() as conn:
        page_size = get_int_setting(conn, "default_table_page_size", settings.default_table_page_size)
        confirm_ai_token_usage = confirm_ai_token_usage_enabled(conn)

    total_count = count_background_jobs()
    total_pages = max(1, (total_count + page_size - 1) // page_size)
    page = min(page, total_pages)
    offset = (page - 1) * page_size

    return {
        "jobs": list_background_jobs(limit=page_size, offset=offset),
        "page": page,
        "page_size": page_size,
        "total_count": total_count,
        "total_pages": total_pages,
        "page_start": offset + 1 if total_count else 0,
        "page_end": min(offset + page_size, total_count),
        "confirm_ai_token_usage_enabled": confirm_ai_token_usage,
    }


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


def count_all_unknown_transactions() -> int:
    """Return the current number of unknown transactions eligible for AI."""
    with db_core_transaction() as conn:
        return upload_ai_workflow.count_unknown_transactions(conn)


def queue_all_unknown_categorization() -> dict[str, Any]:
    """Queue AI categorization for all unknown transactions when work exists."""
    unknown_count = count_all_unknown_transactions()
    if not unknown_count:
        return {"ok": False, "unknown_count": 0, "job_id": None}

    job_id = upload_ai_workflow.queue_all_unknown_llm_categorization()
    return {"ok": True, "unknown_count": unknown_count, "job_id": job_id}


def estimate_all_unknown_categorization() -> dict[str, Any]:
    """Return an AI token estimate for all-unknown categorization."""
    unknown_count = count_all_unknown_transactions()
    if not unknown_count:
        return {"ok": False, "message": "No unknown transactions need AI categorization."}

    return upload_service.estimate_all_unknown_llm_categorization()
