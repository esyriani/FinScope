"""In-memory background job runner and undo orchestration."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from threading import Lock
from uuid import uuid4


MAX_BACKGROUND_WORKERS = 1

_executor = ThreadPoolExecutor(max_workers=MAX_BACKGROUND_WORKERS)
_jobs = {}
_job_sequence = 0
_lock = Lock()


FINISHED_STATUSES = {"completed", "failed"}


def submit_background_job(label, func, *args, undo_handler=None, undo_args=None, undo_kwargs=None, **kwargs):
    """Queue a background job and return its public identifier."""
    global _job_sequence

    job_id = uuid4().hex
    now = utc_now()
    job = {
        "id": job_id,
        "label": label,
        "status": "queued",
        "created_at": now,
        "started_at": None,
        "finished_at": None,
        "result": None,
        "error": None,
        "undo_status": "available" if undo_handler else "unavailable",
        "undo_result": None,
        "undo_error": None,
        "undone_at": None,
        "_undo_handler": undo_handler,
        "_undo_args": tuple(undo_args or ()),
        "_undo_kwargs": dict(undo_kwargs or {}),
    }

    with _lock:
        _job_sequence += 1
        job["_created_sequence"] = _job_sequence
        _jobs[job_id] = job

    _executor.submit(run_job, job_id, func, args, kwargs)
    return job_id


def run_job(job_id, func, args, kwargs):
    """Execute a queued job and record its terminal state."""
    update_job(job_id, status="running", started_at=utc_now())

    try:
        result = func(*args, **kwargs)
    except Exception as exc:
        update_job(
            job_id,
            status="failed",
            error=f"{type(exc).__name__}: {exc}",
            finished_at=utc_now(),
        )
        return

    update_job(job_id, status="completed", result=result, finished_at=utc_now())


def update_job(job_id, **changes):
    """Apply state changes to a tracked job."""
    with _lock:
        job = _jobs.get(job_id)
        if job is not None:
            job.update(changes)


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

    return jobs[offset:offset + limit]


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

        if job["status"] not in FINISHED_STATUSES:
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
    public = {
        key: value
        for key, value in dict(job).items()
        if not key.startswith("_")
    }
    public["can_undo"] = (
        job.get("_undo_handler") is not None
        and job["status"] in FINISHED_STATUSES
        and job["undo_status"] == "available"
    )
    return public


def utc_now():
    """Return the current UTC timestamp for job metadata."""
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
