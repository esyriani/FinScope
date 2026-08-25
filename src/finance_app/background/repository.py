"""Persist background job history while execution remains process-local.

The runner owns live futures, cancellation checks, and undo callables in memory.
This module records sanitized job snapshots and progress events in SQLAlchemy
Core tables so the Processing page, diagnostics, and startup repair can survive
process restarts.
"""

import json
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import delete, func, insert, select, update

from finance_app.core.constants import (
    ACTIVE_BACKGROUND_JOB_STATUSES,
    BACKGROUND_JOB_STATUS_FAILED,
    BACKGROUND_JOB_UNDO_STATUS_UNAVAILABLE,
    FINISHED_BACKGROUND_JOB_STATUSES,
)
from finance_app.database.engine import db_core_transaction
from finance_app.database.tables import background_job_events, background_jobs

MAX_JOB_EVENT_ROWS = 120
DEFAULT_JOB_RETENTION_DAYS = 90
INTERRUPTED_BACKGROUND_JOB_ERROR = (
    "The app restarted before this processing job finished. Retry the original action if needed."
)


def persist_job_snapshot(job: Mapping[str, Any]) -> None:
    """Persist a complete public-safe job snapshot in its own transaction."""
    with db_core_transaction() as conn:
        save_job_snapshot(conn, job)


def save_job_snapshot(conn: Any, job: Mapping[str, Any]) -> None:
    """Insert or update one background job row.

    Args:
        conn: Open SQLAlchemy Core connection. The caller owns transaction scope.
        job: Runner job dictionary or public job snapshot. Private execution
            values such as futures and undo callables are ignored.
    """
    values = job_row_values(job)
    existing = conn.execute(
        select(background_jobs.c.id).where(background_jobs.c.id == values["id"])
    ).scalar_one_or_none()
    if existing is None:
        conn.execute(insert(background_jobs).values(**values))
        return

    update_values = dict(values)
    update_values.pop("id", None)
    conn.execute(update(background_jobs).where(background_jobs.c.id == values["id"]).values(**update_values))


def persist_job_event(job_id: str, event: Mapping[str, Any]) -> None:
    """Persist one progress log event and prune older entries for the job."""
    with db_core_transaction() as conn:
        save_job_event(conn, job_id, event)
        prune_job_events(conn, job_id)


def save_job_event(conn: Any, job_id: str, event: Mapping[str, Any]) -> None:
    """Insert one sanitized progress event for a persisted job."""
    if not conn.execute(select(background_jobs.c.id).where(background_jobs.c.id == job_id)).scalar_one_or_none():
        return

    conn.execute(
        insert(background_job_events).values(
            job_id=job_id,
            created_at=event.get("timestamp") or utc_timestamp(),
            level=str(event.get("level") or "info"),
            message=str(event.get("message") or "Job event."),
            params=json_text(event.get("params") or {}),
        )
    )


def get_job(conn: Any, job_id: str) -> dict[str, Any] | None:
    """Return one persisted public job snapshot with its progress log."""
    row = conn.execute(select(background_jobs).where(background_jobs.c.id == job_id)).mappings().one_or_none()
    if row is None:
        return None

    return job_from_row(row, list_job_events(conn, [job_id]).get(job_id, []))


def list_jobs(conn: Any, limit: int | None = 50, offset: int = 0) -> list[dict[str, Any]]:
    """Return persisted public job snapshots in newest-first order."""
    statement = select(background_jobs).order_by(
        background_jobs.c.created_at.desc(),
        background_jobs.c.created_sequence.desc(),
        background_jobs.c.id.desc(),
    )
    if offset:
        statement = statement.offset(max(0, int(offset)))
    if limit is not None:
        statement = statement.limit(max(0, int(limit)))

    rows = conn.execute(statement).mappings().all()
    events_by_job = list_job_events(conn, [str(row["id"]) for row in rows])
    return [job_from_row(row, events_by_job.get(str(row["id"]), [])) for row in rows]


def count_jobs(conn: Any) -> int:
    """Return the number of persisted background jobs."""
    return int(conn.execute(select(func.count()).select_from(background_jobs)).scalar_one())


def mark_interrupted_jobs_failed(conn: Any, finished_at: str | None = None) -> int:
    """Mark queued/running persisted jobs failed after process restart."""
    finished_at = finished_at or utc_timestamp()
    interrupted_ids = [
        str(job_id)
        for job_id in conn.execute(
            select(background_jobs.c.id).where(background_jobs.c.status.in_(ACTIVE_BACKGROUND_JOB_STATUSES))
        ).scalars()
    ]
    if not interrupted_ids:
        return 0

    conn.execute(
        update(background_jobs)
        .where(background_jobs.c.id.in_(interrupted_ids))
        .values(
            status=BACKGROUND_JOB_STATUS_FAILED,
            error=INTERRUPTED_BACKGROUND_JOB_ERROR,
            finished_at=finished_at,
            undo_status=BACKGROUND_JOB_UNDO_STATUS_UNAVAILABLE,
        )
    )
    for job_id in interrupted_ids:
        save_job_event(
            conn,
            job_id,
            {
                "timestamp": finished_at,
                "level": "error",
                "message": "Job failed: {error}",
                "params": {"error": INTERRUPTED_BACKGROUND_JOB_ERROR},
            },
        )
        prune_job_events(conn, job_id)
    return len(interrupted_ids)


def cleanup_old_jobs(
    conn: Any,
    *,
    retention_days: int = DEFAULT_JOB_RETENTION_DAYS,
    now: str | None = None,
) -> int:
    """Delete terminal jobs older than the retention window.

    Args:
        conn: Open SQLAlchemy Core connection. The caller owns transaction scope.
        retention_days: Number of days to retain completed, failed, and
            cancelled jobs. Non-positive values disable cleanup.
        now: Optional UTC ISO timestamp for deterministic tests.

    Returns:
        Number of deleted job rows. Related events are removed by cascade.
    """
    retention_days = int(retention_days)
    if retention_days <= 0:
        return 0

    cutoff = cutoff_timestamp(retention_days, now=now)
    result = conn.execute(
        delete(background_jobs).where(
            background_jobs.c.status.in_(FINISHED_BACKGROUND_JOB_STATUSES),
            background_jobs.c.finished_at.is_not(None),
            background_jobs.c.finished_at < cutoff,
        )
    )
    return max(0, result.rowcount or 0)


def prune_job_events(conn: Any, job_id: str, *, max_entries: int = MAX_JOB_EVENT_ROWS) -> int:
    """Keep only the newest progress log rows for one job."""
    total = int(
        conn.execute(
            select(func.count()).select_from(background_job_events).where(background_job_events.c.job_id == job_id)
        ).scalar_one()
    )
    overflow = total - max(0, int(max_entries))
    if overflow <= 0:
        return 0

    stale_ids = [
        int(event_id)
        for event_id in conn.execute(
            select(background_job_events.c.id)
            .where(background_job_events.c.job_id == job_id)
            .order_by(background_job_events.c.id)
            .limit(overflow)
        ).scalars()
    ]
    if not stale_ids:
        return 0

    result = conn.execute(delete(background_job_events).where(background_job_events.c.id.in_(stale_ids)))
    return max(0, result.rowcount or 0)


def list_job_events(conn: Any, job_ids: Sequence[str]) -> dict[str, list[dict[str, Any]]]:
    """Return progress events keyed by job id."""
    if not job_ids:
        return {}

    rows = (
        conn.execute(
            select(background_job_events)
            .where(background_job_events.c.job_id.in_(job_ids))
            .order_by(background_job_events.c.job_id, background_job_events.c.id)
        )
        .mappings()
        .all()
    )
    events_by_job: dict[str, list[dict[str, Any]]] = {job_id: [] for job_id in job_ids}
    for row in rows:
        events_by_job.setdefault(str(row["job_id"]), []).append(event_from_row(row))

    for job_id, events in events_by_job.items():
        events_by_job[job_id] = events[-MAX_JOB_EVENT_ROWS:]
    return events_by_job


def job_row_values(job: Mapping[str, Any]) -> dict[str, Any]:
    """Return a sanitized database row for a job dictionary."""
    return {
        "id": str(job["id"]),
        "label": str(job["label"]),
        "queue": str(job.get("queue") or "main"),
        "status": str(job.get("status") or "queued"),
        "created_at": job.get("created_at") or utc_timestamp(),
        "started_at": job.get("started_at"),
        "finished_at": job.get("finished_at"),
        "result": text_value(job.get("result")),
        "error": text_value(job.get("error")),
        "progress_current": non_negative_int(job.get("progress_current"), default=0),
        "progress_total": non_negative_int(job.get("progress_total")),
        "progress_percent": bounded_percent(job.get("progress_percent")),
        "progress_message": text_value(job.get("progress_message")),
        "progress_params": json_text(job.get("progress_params") or {}),
        "cancel_requested": int(bool(job.get("cancel_requested"))),
        "undo_status": str(job.get("undo_status") or "unavailable"),
        "undo_result": text_value(job.get("undo_result")),
        "undo_error": text_value(job.get("undo_error")),
        "undone_at": job.get("undone_at"),
        "created_sequence": non_negative_int(job.get("_created_sequence", job.get("created_sequence")), default=0),
    }


def job_from_row(row: Mapping[str, Any], events: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Return the public runner-shaped job dictionary from a persisted row."""
    job = dict(row)
    job.pop("created_sequence", None)
    job["progress_message"] = job.get("progress_message") or ""
    job["progress_params"] = json_object(job.get("progress_params"))
    job["cancel_requested"] = bool(job.get("cancel_requested"))
    job["progress_log"] = [
        {
            **dict(event),
            "params": dict(event.get("params") or {}),
        }
        for event in events
    ]
    job["can_undo"] = False
    job["can_cancel"] = False
    return job


def event_from_row(row: Mapping[str, Any]) -> dict[str, Any]:
    """Return a public progress event from a persisted event row."""
    return {
        "timestamp": row["created_at"],
        "level": row["level"],
        "message": row["message"],
        "params": json_object(row.get("params")),
    }


def json_text(value: object) -> str:
    """Return an ASCII JSON object string for persisted params."""
    if not isinstance(value, Mapping):
        value = {}
    return json.dumps(dict(value), ensure_ascii=True, sort_keys=True, default=str)


def json_object(value: object) -> dict[str, Any]:
    """Return a JSON object from persisted text, ignoring malformed values."""
    if not value:
        return {}
    try:
        loaded = json.loads(str(value))
    except (TypeError, ValueError):
        return {}
    return dict(loaded) if isinstance(loaded, Mapping) else {}


def text_value(value: object) -> str | None:
    """Return a text value suitable for persisted result/error columns."""
    if value is None:
        return None
    return str(value)


def non_negative_int(value: object, default: int | None = None) -> int | None:
    """Return a non-negative integer or a caller-provided fallback."""
    if value in (None, ""):
        return default
    return max(0, int(str(value)))


def bounded_percent(value: object) -> int | None:
    """Return a nullable progress percent bounded to 0 through 100."""
    if value in (None, ""):
        return None
    return min(100, max(0, int(str(value))))


def cutoff_timestamp(retention_days: int, *, now: str | None = None) -> str:
    """Return the UTC timestamp before which terminal jobs can be pruned."""
    return format_utc_datetime(parse_utc_timestamp(now) - timedelta(days=retention_days))


def parse_utc_timestamp(value: str | None) -> datetime:
    """Return an aware UTC datetime for retention calculations."""
    if value:
        text = value[:-1] + "+00:00" if value.endswith("Z") else value
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    return datetime.now(timezone.utc).replace(microsecond=0)


def utc_timestamp() -> str:
    """Return a UTC timestamp string for persisted job metadata."""
    return format_utc_datetime(datetime.now(timezone.utc))


def format_utc_datetime(value: datetime) -> str:
    """Return the canonical UTC timestamp used by FinScope."""
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
