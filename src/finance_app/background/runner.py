"""In-memory background job runner and undo orchestration."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Lock, local
from uuid import uuid4

MAIN_JOB_QUEUE = "main"
AI_JOB_QUEUE = "ai"
JOB_QUEUES = frozenset({MAIN_JOB_QUEUE, AI_JOB_QUEUE})
MAX_BACKGROUND_WORKERS = 1
MAX_PROGRESS_LOG_ENTRIES = 120

_executor = ThreadPoolExecutor(max_workers=MAX_BACKGROUND_WORKERS)
_ai_executor = ThreadPoolExecutor(max_workers=MAX_BACKGROUND_WORKERS)
_jobs = {}
_job_sequence = 0
_lock = Lock()
_job_context = local()


FINISHED_STATUSES = {"completed", "failed", "cancelled"}
CANCELLABLE_STATUSES = {"queued", "running"}
UNDOABLE_STATUSES = {"completed", "failed"}


class JobCancelled(RuntimeError):
    """Signal that a running background job stopped after a cancel request."""


def submit_background_job(
    label,
    func,
    *args,
    undo_handler=None,
    undo_args=None,
    undo_kwargs=None,
    queue=MAIN_JOB_QUEUE,
    **kwargs,
):
    """Queue a background job and return its public identifier."""
    global _job_sequence

    queue = normalize_job_queue(queue)
    job_id = uuid4().hex
    now = utc_now()
    job = {
        "id": job_id,
        "label": label,
        "queue": queue,
        "status": "queued",
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
        "undo_status": "available" if undo_handler else "unavailable",
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

    future = executor_for_queue(queue).submit(run_job, job_id, func, args, kwargs)
    update_job(job_id, _future=future)
    return job_id


def run_job(job_id, func, args, kwargs):
    """Execute a queued job and record its terminal state."""
    if is_job_cancel_requested(job_id):
        result = "Job cancelled before it started."
        append_background_job_log(result, level="warning", job_id=job_id)
        update_job(job_id, status="cancelled", result=result, finished_at=utc_now())
        return

    update_job(job_id, status="running", started_at=utc_now())

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
            status="cancelled",
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
            status="failed",
            error=error,
            finished_at=utc_now(),
        )
        return
    finally:
        _job_context.job_id = None

    update_job(job_id, status="completed", result=result, finished_at=utc_now())


def update_job(job_id, **changes):
    """Apply state changes to a tracked job."""
    with _lock:
        job = _jobs.get(job_id)
        if job is not None:
            job.update(changes)


def update_background_job_progress(
    current=None,
    total=None,
    message=None,
    params=None,
    job_id=None,
):
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

    with _lock:
        job = _jobs.get(job_id)
        if job is None:
            return None

        if current is not None:
            job["progress_current"] = max(0, int(current))
        if total is not None:
            job["progress_total"] = max(0, int(total))
        if message is not None:
            job["progress_message"] = str(message)
        if params is not None:
            job["progress_params"] = dict(params)

        job["progress_percent"] = progress_percent(
            job.get("progress_current"),
            job.get("progress_total"),
        )
        return public_job(job)


def append_background_job_log(message, params=None, level="info", job_id=None):
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

    with _lock:
        job = _jobs.get(job_id)
        if job is None:
            return None

        append_job_log_entry(job, message, params=params, level=level)
        return public_job(job)


def append_job_log_entry(job, message, params=None, level="info"):
    """Append a bounded log entry to a job dictionary already under lock."""
    entries = job.setdefault("progress_log", [])
    entries.append(
        {
            "timestamp": utc_now(),
            "level": normalize_log_level(level),
            "message": str(message),
            "params": dict(params or {}),
        }
    )
    if len(entries) > MAX_PROGRESS_LOG_ENTRIES:
        del entries[: len(entries) - MAX_PROGRESS_LOG_ENTRIES]


def normalize_log_level(level):
    """Return a supported progress log severity."""
    text = str(level or "info").strip().lower()
    return text if text in {"info", "warning", "error"} else "info"


def progress_percent(current, total):
    """Return a bounded integer percent for known progress values."""
    if total in (None, ""):
        return None

    total = int(total)
    if total <= 0:
        return 100

    current = min(max(0, int(current or 0)), total)
    return int(round((current / total) * 100))


def cancel_background_job(job_id):
    """Request cancellation for a queued or running job."""
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
            job["status"] = "cancelled"
            job["result"] = "Job cancelled before it started."
            job["finished_at"] = utc_now()
            append_job_log_entry(job, "Job cancelled before it started.", level="warning")
            return public_job(job)

        append_job_log_entry(
            job,
            "Cancellation requested; waiting for the current batch to finish.",
            level="warning",
        )
        return public_job(job)


def cancel_queued_background_jobs(queue=None):
    """Cancel queued jobs, optionally limited to one queue, and return a count."""
    queue = normalize_job_queue(queue) if queue is not None else None
    with _lock:
        job_ids = [
            job_id
            for job_id, job in _jobs.items()
            if job["status"] == "queued" and (queue is None or job.get("queue") == queue)
        ]

    cancelled_count = 0
    for job_id in job_ids:
        job = cancel_background_job(job_id)
        if job and job["status"] == "cancelled":
            cancelled_count += 1
    return cancelled_count


def get_background_job(job_id):
    """Return a public snapshot of one background job."""
    with _lock:
        job = _jobs.get(job_id)
        return public_job(job) if job else None


def list_background_jobs(limit=50, offset=0):
    """List public job snapshots in newest-first order."""
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


def count_background_jobs():
    """Count tracked background jobs."""
    with _lock:
        return len(_jobs)


def undo_background_job(job_id):
    """Run the undo handler for a completed job when available."""
    with _lock:
        job = _jobs.get(job_id)
        if job is None:
            return None

        if job.get("_undo_handler") is None:
            raise ValueError("This job does not have anything to undo.")

        if job["status"] not in UNDOABLE_STATUSES:
            raise ValueError("Only finished jobs can be undone.")

        if job["undo_status"] == "undone":
            raise ValueError("This job has already been undone.")

        if job["undo_status"] == "undoing":
            raise ValueError("This job is already being undone.")

        undo_handler = job["_undo_handler"]
        undo_args = job["_undo_args"]
        undo_kwargs = job["_undo_kwargs"]
        job["undo_status"] = "undoing"
        job["undo_error"] = None

    try:
        result = undo_handler(*undo_args, **undo_kwargs)
    except Exception as exc:
        update_job(
            job_id,
            undo_status="available",
            undo_error=f"{type(exc).__name__}: {exc}",
        )
        raise

    update_job(
        job_id,
        undo_status="undone",
        undo_result=result,
        undo_error=None,
        undone_at=utc_now(),
    )
    return get_background_job(job_id)


def public_job(job):
    """Build the job representation exposed to controllers."""
    public = {key: value for key, value in dict(job).items() if not key.startswith("_")}
    public["can_undo"] = (
        job.get("_undo_handler") is not None
        and job["status"] in UNDOABLE_STATUSES
        and job["undo_status"] == "available"
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


def current_job_id():
    """Return the identifier of the job executing on this worker thread."""
    return getattr(_job_context, "job_id", None)


def is_job_cancel_requested(job_id=None):
    """Return whether the current or named job has a pending cancel request."""
    job_id = job_id or current_job_id()
    if not job_id:
        return False

    with _lock:
        job = _jobs.get(job_id)
        return bool(job and job.get("cancel_requested"))


def raise_if_cancel_requested(message="Job cancelled."):
    """Raise ``JobCancelled`` when the current job should stop cooperatively."""
    if is_job_cancel_requested():
        raise JobCancelled(message)


def executor_for_queue(queue):
    """Return the executor assigned to a normalized queue name."""
    return _ai_executor if queue == AI_JOB_QUEUE else _executor


def normalize_job_queue(queue):
    """Return a supported queue name for new background work."""
    text = str(queue or MAIN_JOB_QUEUE).strip().lower()
    if text not in JOB_QUEUES:
        raise ValueError(f"Unsupported background job queue: {queue}")
    return text


def utc_now():
    """Return the current UTC timestamp for job metadata."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
