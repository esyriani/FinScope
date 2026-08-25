"""Process-local background job runner with persisted job history."""

from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Lock, local
from typing import Any
from uuid import uuid4

from sqlalchemy.exc import SQLAlchemyError

from finance_app.background import repository as job_repository
from finance_app.core.constants import (
    BACKGROUND_JOB_LOG_LEVEL_INFO,
    BACKGROUND_JOB_LOG_LEVEL_WARNING,
    BACKGROUND_JOB_LOG_LEVELS,
    BACKGROUND_JOB_QUEUE_AI,
    BACKGROUND_JOB_QUEUE_MAIN,
    BACKGROUND_JOB_QUEUES,
    BACKGROUND_JOB_STATUS_CANCELLED,
    BACKGROUND_JOB_STATUS_COMPLETED,
    BACKGROUND_JOB_STATUS_FAILED,
    BACKGROUND_JOB_STATUS_QUEUED,
    BACKGROUND_JOB_STATUS_RUNNING,
    BACKGROUND_JOB_UNDO_STATUS_AVAILABLE,
    BACKGROUND_JOB_UNDO_STATUS_UNAVAILABLE,
    BACKGROUND_JOB_UNDO_STATUS_UNDOING,
    BACKGROUND_JOB_UNDO_STATUS_UNDONE,
    FINISHED_BACKGROUND_JOB_STATUSES,
)
from finance_app.database.engine import db_core_transaction

MAIN_JOB_QUEUE = BACKGROUND_JOB_QUEUE_MAIN
AI_JOB_QUEUE = BACKGROUND_JOB_QUEUE_AI
JOB_QUEUES = frozenset(BACKGROUND_JOB_QUEUES)
MAX_BACKGROUND_WORKERS = 1
MAX_PROGRESS_LOG_ENTRIES = job_repository.MAX_JOB_EVENT_ROWS

_executor = ThreadPoolExecutor(max_workers=MAX_BACKGROUND_WORKERS)
_ai_executor = ThreadPoolExecutor(max_workers=MAX_BACKGROUND_WORKERS)
_jobs: dict[str, dict[str, Any]] = {}
_job_sequence = 0
_lock = Lock()
_job_context: Any = local()
_job_history_enabled = True


FINISHED_STATUSES = set(FINISHED_BACKGROUND_JOB_STATUSES)
CANCELLABLE_STATUSES = {BACKGROUND_JOB_STATUS_QUEUED, BACKGROUND_JOB_STATUS_RUNNING}
UNDOABLE_STATUSES = {BACKGROUND_JOB_STATUS_COMPLETED, BACKGROUND_JOB_STATUS_FAILED}


class JobCancelled(RuntimeError):
    """Signal that a running background job stopped after a cancel request."""


def submit_background_job(
    label: str,
    func: Callable[..., Any],
    *args: Any,
    undo_handler: Callable[..., Any] | None = None,
    undo_args: tuple[Any, ...] | None = None,
    undo_kwargs: Mapping[str, Any] | None = None,
    queue: object = MAIN_JOB_QUEUE,
    **kwargs: Any,
) -> str:
    """Queue a background job and return its public identifier."""
    global _job_sequence

    queue = normalize_job_queue(queue)
    job_id = uuid4().hex
    now = utc_now()
    job: dict[str, Any] = {
        "id": job_id,
        "label": label,
        "queue": queue,
        "status": BACKGROUND_JOB_STATUS_QUEUED,
        "created_at": now,
        "started_at": None,
        "finished_at": None,
        "result": None,
        "error": None,
        "progress_current": 0,
        "progress_total": None,
        "progress_percent": None,
        "progress_message": "",
        "progress_params": {},
        "progress_log": [],
        "cancel_requested": False,
        "undo_status": BACKGROUND_JOB_UNDO_STATUS_AVAILABLE if undo_handler else BACKGROUND_JOB_UNDO_STATUS_UNAVAILABLE,
        "undo_result": None,
        "undo_error": None,
        "undone_at": None,
        "_future": None,
        "_undo_handler": undo_handler,
        "_undo_args": tuple(undo_args or ()),
        "_undo_kwargs": dict(undo_kwargs or {}),
    }

    with _lock:
        _job_sequence += 1
        job["_created_sequence"] = _job_sequence
        _jobs[job_id] = job

    persist_job_snapshot(job)
    future = executor_for_queue(queue).submit(run_job, job_id, func, args, kwargs)
    update_job(job_id, _future=future)
    return job_id


def run_job(job_id: str, func: Callable[..., Any], args: tuple[Any, ...], kwargs: Mapping[str, Any]) -> None:
    """Execute a queued job and record its terminal state."""
    if is_job_cancel_requested(job_id):
        result = "Job cancelled before it started."
        append_background_job_log(result, level="warning", job_id=job_id)
        update_job(job_id, status=BACKGROUND_JOB_STATUS_CANCELLED, result=result, finished_at=utc_now())
        return

    update_job(job_id, status=BACKGROUND_JOB_STATUS_RUNNING, started_at=utc_now())

    try:
        _job_context.job_id = job_id
        result = func(*args, **kwargs)
    except JobCancelled as exc:
        result = str(exc) or "Job cancelled."
        append_background_job_log(
            "Job cancelled: {result}",
            params={"result": result},
            level="warning",
            job_id=job_id,
        )
        update_job(
            job_id,
            status=BACKGROUND_JOB_STATUS_CANCELLED,
            result=result,
            finished_at=utc_now(),
        )
        return
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        append_background_job_log(
            "Job failed: {error}",
            params={"error": error},
            level="error",
            job_id=job_id,
        )
        update_job(
            job_id,
            status=BACKGROUND_JOB_STATUS_FAILED,
            error=error,
            finished_at=utc_now(),
        )
        return
    finally:
        _job_context.job_id = None

    update_job(job_id, status=BACKGROUND_JOB_STATUS_COMPLETED, result=result, finished_at=utc_now())


def update_job(job_id: str, **changes: Any) -> None:
    """Apply state changes to a tracked job."""
    snapshot = None
    should_persist = any(not key.startswith("_") for key in changes)
    with _lock:
        job = _jobs.get(job_id)
        if job is not None:
            job.update(changes)
            if should_persist:
                snapshot = dict(job)
    if snapshot is not None:
        persist_job_snapshot(snapshot)


def update_background_job_progress(
    current: object | None = None,
    total: object | None = None,
    message: object | None = None,
    params: Mapping[str, Any] | None = None,
    job_id: str | None = None,
) -> dict[str, Any] | None:
    """Update progress details for a running background job.

    Args:
        current: Number of completed units, when known.
        total: Total expected units, when known.
        message: Translation key summarizing the current progress.
        params: Variables used with ``message`` by templates and client code.
        job_id: Optional explicit job id. Defaults to the current worker job.

    Returns:
        The public job snapshot, or ``None`` when no tracked job is active.
    """
    job_id = job_id or current_job_id()
    if not job_id:
        return None

    snapshot = None
    public = None
    with _lock:
        job = _jobs.get(job_id)
        if job is None:
            return None

        if current is not None:
            job["progress_current"] = max(0, int(str(current)))
        if total is not None:
            job["progress_total"] = max(0, int(str(total)))
        if message is not None:
            job["progress_message"] = str(message)
        if params is not None:
            job["progress_params"] = dict(params)

        job["progress_percent"] = progress_percent(
            job.get("progress_current"),
            job.get("progress_total"),
        )
        snapshot = dict(job)
        public = public_job(job)
    persist_job_snapshot(snapshot)
    return public


def append_background_job_log(
    message: object,
    params: Mapping[str, Any] | None = None,
    level: object = "info",
    job_id: str | None = None,
) -> dict[str, Any] | None:
    """Append a timestamped progress log entry to a tracked background job.

    Args:
        message: Translation key or concise display text for the log entry.
        params: Variables used with ``message`` by templates and client code.
        level: Entry severity, usually ``info``, ``warning``, or ``error``.
        job_id: Optional explicit job id. Defaults to the current worker job.

    Returns:
        The public job snapshot, or ``None`` when no tracked job is active.
    """
    job_id = job_id or current_job_id()
    if not job_id:
        return None

    event = None
    public = None
    with _lock:
        job = _jobs.get(job_id)
        if job is None:
            return None

        event = append_job_log_entry(job, message, params=params, level=level)
        public = public_job(job)
    persist_job_event(job_id, event)
    return public


def append_job_log_entry(
    job: dict[str, Any],
    message: object,
    params: Mapping[str, Any] | None = None,
    level: object = "info",
) -> dict[str, Any]:
    """Append a bounded log entry to a job dictionary already under lock."""
    entries = job.setdefault("progress_log", [])
    entry = {
        "timestamp": utc_now(),
        "level": normalize_log_level(level),
        "message": str(message),
        "params": dict(params or {}),
    }
    entries.append(entry)
    if len(entries) > MAX_PROGRESS_LOG_ENTRIES:
        del entries[: len(entries) - MAX_PROGRESS_LOG_ENTRIES]
    return entry


def normalize_log_level(level: object) -> str:
    """Return a supported progress log severity."""
    text = str(level or BACKGROUND_JOB_LOG_LEVEL_INFO).strip().lower()
    return text if text in BACKGROUND_JOB_LOG_LEVELS else BACKGROUND_JOB_LOG_LEVEL_INFO


def progress_percent(current: object, total: object) -> int | None:
    """Return a bounded integer percent for known progress values."""
    if total in (None, ""):
        return None

    parsed_total = int(str(total))
    if parsed_total <= 0:
        return 100

    parsed_current = min(max(0, int(str(current or 0))), parsed_total)
    return int(round((parsed_current / parsed_total) * 100))


def cancel_background_job(job_id: str) -> dict[str, Any] | None:
    """Request cancellation for a queued or running job."""
    snapshot = None
    event = None
    public = None
    with _lock:
        job = _jobs.get(job_id)
        if job is None:
            return None

        if job["status"] not in CANCELLABLE_STATUSES:
            raise ValueError("Only queued or running jobs can be cancelled.")

        job["cancel_requested"] = True
        future = job.get("_future")
        cancelled_before_start = job["status"] == "queued" and (future is None or future.cancel())
        if cancelled_before_start:
            job["status"] = BACKGROUND_JOB_STATUS_CANCELLED
            job["result"] = "Job cancelled before it started."
            job["finished_at"] = utc_now()
            event = append_job_log_entry(
                job,
                "Job cancelled before it started.",
                level=BACKGROUND_JOB_LOG_LEVEL_WARNING,
            )
            snapshot = dict(job)
            public = public_job(job)
        else:
            event = append_job_log_entry(
                job,
                "Cancellation requested; waiting for the current batch to finish.",
                level=BACKGROUND_JOB_LOG_LEVEL_WARNING,
            )
            snapshot = dict(job)
            public = public_job(job)

    persist_job_snapshot(snapshot)
    persist_job_event(job_id, event)
    return public


def cancel_queued_background_jobs(queue: object | None = None) -> int:
    """Cancel queued jobs, optionally limited to one queue, and return a count."""
    queue = normalize_job_queue(queue) if queue is not None else None
    with _lock:
        job_ids = [
            job_id
            for job_id, job in _jobs.items()
            if job["status"] == BACKGROUND_JOB_STATUS_QUEUED and (queue is None or job.get("queue") == queue)
        ]

    cancelled_count = 0
    for job_id in job_ids:
        job = cancel_background_job(job_id)
        if job and job["status"] == "cancelled":
            cancelled_count += 1
    return cancelled_count


def get_background_job(job_id: str) -> dict[str, Any] | None:
    """Return a public snapshot of one background job."""
    with _lock:
        job = _jobs.get(job_id)
        if job is not None:
            return public_job(job)

    return persisted_job(job_id)


def list_background_jobs(limit: int | None = 50, offset: int = 0) -> list[dict[str, Any]]:
    """List public job snapshots in newest-first order."""
    persisted = persisted_jobs(limit=limit, offset=offset)
    if persisted is not None:
        return overlay_active_jobs(persisted)

    with _lock:
        jobs = list(_jobs.values())

    jobs.sort(
        key=lambda job: (job["created_at"], job.get("_created_sequence", 0)),
        reverse=True,
    )
    jobs = [public_job(job) for job in jobs]
    if limit is None:
        return jobs[offset:]

    return jobs[offset : offset + limit]


def count_background_jobs() -> int:
    """Count tracked background jobs."""
    persisted_count = count_persisted_jobs()
    if persisted_count is not None:
        return persisted_count

    with _lock:
        return len(_jobs)


def undo_background_job(job_id: str) -> dict[str, Any] | None:
    """Run the undo handler for a completed job when available."""
    undo_handler: Callable[..., Any] | None = None
    undo_args: tuple[Any, ...] = ()
    undo_kwargs: dict[str, Any] = {}
    snapshot: dict[str, Any] | None = None

    with _lock:
        job = _jobs.get(job_id)
        if job is not None:
            if job.get("_undo_handler") is None:
                raise ValueError("This job does not have anything to undo.")

            if job["status"] not in UNDOABLE_STATUSES:
                raise ValueError("Only finished jobs can be undone.")

            if job["undo_status"] == BACKGROUND_JOB_UNDO_STATUS_UNDONE:
                raise ValueError("This job has already been undone.")

            if job["undo_status"] == BACKGROUND_JOB_UNDO_STATUS_UNDOING:
                raise ValueError("This job is already being undone.")

            undo_handler = job["_undo_handler"]
            undo_args = job["_undo_args"]
            undo_kwargs = job["_undo_kwargs"]
            job["undo_status"] = BACKGROUND_JOB_UNDO_STATUS_UNDOING
            job["undo_error"] = None
            snapshot = dict(job)

    if job is None:
        if persisted_job(job_id) is None:
            return None
        raise ValueError("This job does not have anything to undo.")

    persist_job_snapshot(snapshot)

    try:
        assert undo_handler is not None
        result = undo_handler(*undo_args, **undo_kwargs)
    except Exception as exc:
        update_job(
            job_id,
            undo_status=BACKGROUND_JOB_UNDO_STATUS_AVAILABLE,
            undo_error=f"{type(exc).__name__}: {exc}",
        )
        raise

    update_job(
        job_id,
        undo_status=BACKGROUND_JOB_UNDO_STATUS_UNDONE,
        undo_result=result,
        undo_error=None,
        undone_at=utc_now(),
    )
    return get_background_job(job_id)


def public_job(job: Mapping[str, Any]) -> dict[str, Any]:
    """Build the job representation exposed to controllers."""
    public = {key: value for key, value in dict(job).items() if not key.startswith("_")}
    public["can_undo"] = (
        job.get("_undo_handler") is not None
        and job["status"] in UNDOABLE_STATUSES
        and job["undo_status"] == BACKGROUND_JOB_UNDO_STATUS_AVAILABLE
    )
    public["can_cancel"] = job["status"] in CANCELLABLE_STATUSES and not job.get("cancel_requested")
    public["progress_params"] = dict(public.get("progress_params") or {})
    public["progress_log"] = [
        {
            **entry,
            "params": dict(entry.get("params") or {}),
        }
        for entry in public.get("progress_log") or []
    ]
    return public


def current_job_id() -> str | None:
    """Return the identifier of the job executing on this worker thread."""
    return getattr(_job_context, "job_id", None)


def is_job_cancel_requested(job_id: str | None = None) -> bool:
    """Return whether the current or named job has a pending cancel request."""
    job_id = job_id or current_job_id()
    if not job_id:
        return False

    with _lock:
        job = _jobs.get(job_id)
        return bool(job and job.get("cancel_requested"))


def raise_if_cancel_requested(message: str = "Job cancelled.") -> None:
    """Raise ``JobCancelled`` when the current job should stop cooperatively."""
    if is_job_cancel_requested():
        raise JobCancelled(message)


def executor_for_queue(queue: object) -> ThreadPoolExecutor:
    """Return the executor assigned to a normalized queue name."""
    return _ai_executor if queue == AI_JOB_QUEUE else _executor


def normalize_job_queue(queue: object) -> str:
    """Return a supported queue name for new background work."""
    text = str(queue or MAIN_JOB_QUEUE).strip().lower()
    if text not in JOB_QUEUES:
        raise ValueError(f"Unsupported background job queue: {queue}")
    return text


def utc_now() -> str:
    """Return the current UTC timestamp for job metadata."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def persist_job_snapshot(job: Mapping[str, Any] | None) -> None:
    """Persist one job snapshot without letting history failures break work."""
    if not _job_history_enabled or job is None:
        return
    try:
        job_repository.persist_job_snapshot(job)
    except SQLAlchemyError:
        return


def persist_job_event(job_id: str, event: Mapping[str, Any] | None) -> None:
    """Persist one job progress event without interrupting the active worker."""
    if not _job_history_enabled or event is None:
        return
    try:
        job_repository.persist_job_event(job_id, event)
    except SQLAlchemyError:
        return


def persisted_job(job_id: str) -> dict[str, Any] | None:
    """Load a persisted job snapshot when history storage is available."""
    if not _job_history_enabled:
        return None
    try:
        with db_core_transaction() as conn:
            return job_repository.get_job(conn, job_id)
    except SQLAlchemyError:
        return None


def persisted_jobs(limit: int | None, offset: int) -> list[dict[str, Any]] | None:
    """Load persisted job snapshots for list views."""
    if not _job_history_enabled:
        return None
    try:
        with db_core_transaction() as conn:
            return job_repository.list_jobs(conn, limit=limit, offset=offset)
    except SQLAlchemyError:
        return None


def count_persisted_jobs() -> int | None:
    """Count persisted jobs when history storage is available."""
    if not _job_history_enabled:
        return None
    try:
        with db_core_transaction() as conn:
            return job_repository.count_jobs(conn)
    except SQLAlchemyError:
        return None


def overlay_active_jobs(jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Replace persisted snapshots with richer in-memory state for active jobs."""
    with _lock:
        active_jobs = {job_id: public_job(job) for job_id, job in _jobs.items()}

    return [active_jobs.get(str(job["id"]), job) for job in jobs]
