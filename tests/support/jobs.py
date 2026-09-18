"""Background job helpers for tests.

Provides reusable recorders for route and workflow tests that patch
``submit_background_job``. The helpers capture submitted job metadata without
starting asynchronous work, and provide deterministic waits for smoke tests
that run the real process-local background runner.
"""

from concurrent.futures import TimeoutError as FutureTimeoutError
from dataclasses import dataclass, field

from finance_app.background import runner

DEFAULT_CAPTURED_JOB_ID = "test-background-job"
DEFAULT_REJECTED_JOB_ID = "rejected-background-job"
DEFAULT_REJECTION_DETAIL = "RuntimeError: executor stopped"


@dataclass(frozen=True)
class CapturedBackgroundJob:
    """Immutable metadata for one captured background job submission."""

    label: str
    func: object
    args: tuple = field(default_factory=tuple)
    undo_handler: object | None = None
    undo_args: tuple | None = None
    undo_kwargs: dict | None = None
    kwargs: dict = field(default_factory=dict)


class BackgroundJobRecorder:
    """Record patched background job submissions for assertions."""

    def __init__(self, job_id=DEFAULT_CAPTURED_JOB_ID):
        """Store the job id returned by patched submissions."""
        self.job_id = job_id
        self.jobs = []

    def install(self, monkeypatch, target):
        """Patch ``target.submit_background_job`` and return this recorder."""
        monkeypatch.setattr(target, "submit_background_job", self.submit)
        return self

    def submit(
        self,
        label,
        func,
        *args,
        undo_handler=None,
        undo_args=None,
        undo_kwargs=None,
        **kwargs,
    ):
        """Capture one job submission and return the configured test job id."""
        self.jobs.append(
            CapturedBackgroundJob(
                label=label,
                func=func,
                args=args,
                undo_handler=undo_handler,
                undo_args=undo_args,
                undo_kwargs=undo_kwargs,
                kwargs=dict(kwargs),
            )
        )
        return self.job_id

    def single(self):
        """Return the only captured job, failing when the count differs."""
        assert len(self.jobs) == 1
        return self.jobs[0]

    def __len__(self):
        """Return the number of captured jobs."""
        return len(self.jobs)

    def __iter__(self):
        """Iterate over captured jobs."""
        return iter(self.jobs)

    def __getitem__(self, index):
        """Return one captured job by index."""
        return self.jobs[index]

    def __bool__(self):
        """Return whether any jobs were captured."""
        return bool(self.jobs)


class RejectingBackgroundJobRecorder(BackgroundJobRecorder):
    """Record submitted job metadata, then raise the runner submission error."""

    def __init__(
        self,
        job_id=DEFAULT_REJECTED_JOB_ID,
        *,
        queue=runner.MAIN_JOB_QUEUE,
        detail=DEFAULT_REJECTION_DETAIL,
    ):
        """Store rejection metadata used by patched submissions."""
        super().__init__(job_id)
        self.queue = queue
        self.detail = detail

    def submit(
        self,
        label,
        func,
        *args,
        undo_handler=None,
        undo_args=None,
        undo_kwargs=None,
        **kwargs,
    ):
        """Capture one job submission and raise a typed queue rejection."""
        self.jobs.append(
            CapturedBackgroundJob(
                label=label,
                func=func,
                args=args,
                undo_handler=undo_handler,
                undo_args=undo_args,
                undo_kwargs=undo_kwargs,
                kwargs=dict(kwargs),
            )
        )
        raise runner.BackgroundJobSubmissionError(self.job_id, label, self.queue, self.detail)


def capture_background_jobs(monkeypatch, target, *, job_id=DEFAULT_CAPTURED_JOB_ID):
    """Patch a module or object to record background job submissions.

    Args:
        monkeypatch: pytest monkeypatch fixture used to restore the patched
            attribute after the test.
        target: Module or object exposing ``submit_background_job``.
        job_id: Deterministic id returned by each captured submission.

    Returns:
        A ``BackgroundJobRecorder`` containing captured submissions.
    """
    return BackgroundJobRecorder(job_id).install(monkeypatch, target)


def reject_background_jobs(
    monkeypatch,
    target,
    *,
    job_id=DEFAULT_REJECTED_JOB_ID,
    queue=runner.MAIN_JOB_QUEUE,
    detail=DEFAULT_REJECTION_DETAIL,
):
    """Patch a module or object so submitted jobs are rejected.

    Args:
        monkeypatch: pytest monkeypatch fixture used to restore the patched
            attribute after the test.
        target: Module or object exposing ``submit_background_job``.
        job_id: Deterministic id exposed by the raised runner error.
        queue: Queue name exposed by the raised runner error.
        detail: Failure detail exposed by the raised runner error.

    Returns:
        A ``RejectingBackgroundJobRecorder`` containing attempted submissions.
    """
    return RejectingBackgroundJobRecorder(job_id, queue=queue, detail=detail).install(monkeypatch, target)


def clear_background_jobs():
    """Clear process-local background job state for isolated runner tests."""
    with runner._lock:
        runner._jobs.clear()
        runner._job_sequence = 0
        runner._job_history_degraded = False
        runner._job_history_degraded_detail = ""


def wait_for_background_job(job_id, *, timeout=5, require_completed=True):
    """Wait for one real background runner job by joining its future."""
    record = background_job_record(job_id)
    if record is None:
        job = runner.get_background_job(job_id)
        if job is None:
            raise AssertionError(f"Background job did not start: {job_id}")
        if job["status"] not in runner.FINISHED_STATUSES:
            raise AssertionError(f"Background job did not finish: {job_id}")
        if require_completed:
            assert job["status"] == "completed", job
        return job

    future = record.get("_future")
    if future is not None:
        try:
            future.result(timeout=timeout)
        except FutureTimeoutError as exc:
            raise AssertionError(f"Background job did not finish: {job_id}") from exc

    job = runner.get_background_job(job_id)
    if job is None or job["status"] not in runner.FINISHED_STATUSES:
        raise AssertionError(f"Background job did not finish: {job_id}")
    if require_completed:
        assert job["status"] == "completed", job
    return job


def wait_for_background_job_label(label, *, timeout=5, require_completed=True):
    """Wait for a real background runner job identified by its display label."""
    record = next((job for job in runner.active_job_records() if job["label"] == label), None)
    if record is None:
        job = next((item for item in runner.list_background_jobs(limit=None) if item["label"] == label), None)
        if job is None:
            raise AssertionError(f"Background job did not start: {label}")
        return wait_for_background_job(job["id"], timeout=timeout, require_completed=require_completed)
    return wait_for_background_job(record["id"], timeout=timeout, require_completed=require_completed)


def wait_for_all_background_jobs(*, timeout=5):
    """Wait for all process-local background runner jobs to reach terminal states."""
    job_ids = [job["id"] for job in runner.active_job_records()]
    for job_id in job_ids:
        wait_for_background_job(job_id, timeout=timeout, require_completed=False)
    return runner.list_background_jobs(limit=None)


def background_job_record(job_id):
    """Return the process-local runner record for a job, including private test state."""
    return next((job for job in runner.active_job_records() if job["id"] == job_id), None)
