"""Background job capture helpers for tests.

Provides reusable recorders for route and workflow tests that patch
``submit_background_job``. The helpers capture submitted job metadata without
starting asynchronous work.
"""

from dataclasses import dataclass, field

DEFAULT_CAPTURED_JOB_ID = "test-background-job"


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
