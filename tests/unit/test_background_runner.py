"""Tests for the in-memory background job runner."""

import pytest

from finance_app.background import runner


class CapturingExecutor:
    """Capture submitted jobs without executing them on another thread."""

    def __init__(self):
        """Prepare an empty submission log."""
        self.submissions = []

    def submit(self, func, *args, **kwargs):
        """Record a submitted callable and its payload."""
        self.submissions.append((func, args, kwargs))
        return None


@pytest.fixture(autouse=True)
def isolated_background_runner(monkeypatch):
    """Reset global background runner state for each test."""
    executor = CapturingExecutor()
    with runner._lock:
        runner._jobs.clear()
        runner._job_sequence = 0
    monkeypatch.setattr(runner, "_executor", executor)
    yield executor
    with runner._lock:
        runner._jobs.clear()
        runner._job_sequence = 0


def test_submit_background_job_records_queued_job_and_submission(isolated_background_runner):
    """Verify that submitted jobs start queued and are sent to the executor."""
    job_id = runner.submit_background_job("Sample job", lambda: "done")

    job = runner.get_background_job(job_id)
    assert job["id"] == job_id
    assert job["label"] == "Sample job"
    assert job["status"] == "queued"
    assert job["started_at"] is None
    assert job["finished_at"] is None
    assert job["can_undo"] is False
    assert len(isolated_background_runner.submissions) == 1

    submitted_func, submitted_args, submitted_kwargs = isolated_background_runner.submissions[0]
    assert submitted_func is runner.run_job
    assert submitted_args[0] == job_id
    assert submitted_kwargs == {}


def test_run_job_transitions_through_running_to_completed():
    """Verify that successful jobs expose running and completed states."""
    seen_statuses = []
    job_id = runner.submit_background_job("Stateful job", lambda: "placeholder")

    def work():
        """Observe the public state while the job is executing."""
        seen_statuses.append(runner.get_background_job(job_id)["status"])
        return "job result"

    runner.run_job(job_id, work, (), {})

    job = runner.get_background_job(job_id)
    assert seen_statuses == ["running"]
    assert job["status"] == "completed"
    assert job["result"] == "job result"
    assert job["error"] is None
    assert job["started_at"] is not None
    assert job["finished_at"] is not None


def test_run_job_records_failed_state_and_error_text():
    """Verify that failed jobs keep a useful public error message."""
    job_id = runner.submit_background_job("Failing job", lambda: "placeholder")

    def fail():
        """Raise a representative job failure."""
        raise RuntimeError("boom")

    runner.run_job(job_id, fail, (), {})

    job = runner.get_background_job(job_id)
    assert job["status"] == "failed"
    assert job["result"] is None
    assert job["error"] == "RuntimeError: boom"
    assert job["finished_at"] is not None


def test_get_list_and_count_background_jobs_use_public_newest_first_order():
    """Verify job lookup, count, limits, and offset ordering."""
    first = runner.submit_background_job("First", lambda: None)
    second = runner.submit_background_job("Second", lambda: None)
    third = runner.submit_background_job("Third", lambda: None)

    assert runner.count_background_jobs() == 3
    assert runner.get_background_job(second)["label"] == "Second"
    assert runner.get_background_job("missing") is None
    assert [job["id"] for job in runner.list_background_jobs(limit=None)] == [
        third,
        second,
        first,
    ]
    assert [job["label"] for job in runner.list_background_jobs(limit=2, offset=1)] == [
        "Second",
        "First",
    ]


def test_undo_background_job_updates_state_and_blocks_second_undo():
    """Verify that completed jobs with undo handlers can be undone once."""
    undo_calls = []
    job_id = runner.submit_background_job(
        "Undoable job",
        lambda: "placeholder",
        undo_handler=lambda value: undo_calls.append(value) or "undo complete",
        undo_args=("payload",),
    )
    runner.run_job(job_id, lambda: "job complete", (), {})

    undone = runner.undo_background_job(job_id)

    assert undo_calls == ["payload"]
    assert undone["undo_status"] == "undone"
    assert undone["undo_result"] == "undo complete"
    assert undone["undo_error"] is None
    assert undone["undone_at"] is not None
    assert undone["can_undo"] is False
    with pytest.raises(ValueError, match="already been undone"):
        runner.undo_background_job(job_id)


def test_undo_background_job_error_cases_restore_public_state():
    """Verify missing, unavailable, unfinished, and failing undo paths."""
    no_undo_job_id = runner.submit_background_job("No undo", lambda: "placeholder")
    runner.run_job(no_undo_job_id, lambda: "done", (), {})

    queued_job_id = runner.submit_background_job(
        "Queued undo",
        lambda: "placeholder",
        undo_handler=lambda: "undo",
    )

    failing_undo_job_id = runner.submit_background_job(
        "Failing undo",
        lambda: "placeholder",
        undo_handler=lambda: (_ for _ in ()).throw(RuntimeError("undo failed")),
    )
    runner.run_job(failing_undo_job_id, lambda: "done", (), {})

    assert runner.undo_background_job("missing") is None
    with pytest.raises(ValueError, match="does not have anything to undo"):
        runner.undo_background_job(no_undo_job_id)
    with pytest.raises(ValueError, match="Only finished jobs can be undone"):
        runner.undo_background_job(queued_job_id)
    with pytest.raises(RuntimeError, match="undo failed"):
        runner.undo_background_job(failing_undo_job_id)

    job = runner.get_background_job(failing_undo_job_id)
    assert job["undo_status"] == "available"
    assert job["undo_error"] == "RuntimeError: undo failed"
    assert job["can_undo"] is True


def test_undo_background_job_rejects_running_jobs_without_changing_undo_state():
    """Verify undo cannot race a job that is already running."""
    job_id = runner.submit_background_job(
        "Running undo",
        lambda: "placeholder",
        undo_handler=lambda: "undo",
    )
    runner.update_job(job_id, status="running", started_at=runner.utc_now())

    with pytest.raises(ValueError, match="Only finished jobs can be undone"):
        runner.undo_background_job(job_id)

    job = runner.get_background_job(job_id)
    assert job["status"] == "running"
    assert job["undo_status"] == "available"
    assert job["can_undo"] is False
