"""Integration tests for persisted background job history."""

from finance_app.background import repository as job_repository
from finance_app.background import runner
from finance_app.database.engine import db_core_transaction
from finance_app.database.tables import background_job_events, background_jobs


class CapturingExecutor:
    """Capture submitted jobs without executing them on another thread."""

    def __init__(self):
        """Prepare an empty submission log."""
        self.submissions = []

    def submit(self, func, *args, **kwargs):
        """Record a submitted callable and its payload."""
        self.submissions.append((func, args, kwargs))
        return None


def clear_runner_state() -> None:
    """Remove process-local jobs while preserving persisted history."""
    with runner._lock:
        runner._jobs.clear()
        runner._job_sequence = 0


def test_runner_persists_completed_job_history(app, monkeypatch):
    """Verify completed jobs remain visible after in-memory state is gone."""
    del app
    executor = CapturingExecutor()
    monkeypatch.setattr(runner, "_executor", executor)
    monkeypatch.setattr(runner, "_job_history_enabled", True)
    clear_runner_state()

    job_id = runner.submit_background_job("Persisted job", lambda: "placeholder")
    runner.append_background_job_log("Starting batch {start}-{end}.", params={"start": 1, "end": 2}, job_id=job_id)
    runner.run_job(job_id, lambda: "persisted result", (), {})
    clear_runner_state()

    job = runner.get_background_job(job_id)
    jobs = runner.list_background_jobs(limit=None)

    assert len(executor.submissions) == 1
    assert job is not None
    assert job["status"] == "completed"
    assert job["result"] == "persisted result"
    assert job["can_cancel"] is False
    assert job["can_undo"] is False
    assert job["progress_log"] == [
        {
            "timestamp": job["progress_log"][0]["timestamp"],
            "level": "info",
            "message": "Starting batch {start}-{end}.",
            "params": {"start": 1, "end": 2},
        }
    ]
    assert [item["id"] for item in jobs] == [job_id]


def test_persisted_job_events_are_bounded(app, monkeypatch):
    """Verify persisted progress logs retain only the newest entries."""
    del app
    monkeypatch.setattr(runner, "_executor", CapturingExecutor())
    monkeypatch.setattr(runner, "_job_history_enabled", True)
    clear_runner_state()

    job_id = runner.submit_background_job("Bounded log job", lambda: "placeholder")
    for index in range(job_repository.MAX_JOB_EVENT_ROWS + 5):
        runner.append_background_job_log(
            "Event {index}",
            params={"index": index},
            job_id=job_id,
        )
    clear_runner_state()

    job = runner.get_background_job(job_id)

    assert job is not None
    assert len(job["progress_log"]) == job_repository.MAX_JOB_EVENT_ROWS
    assert job["progress_log"][0]["params"] == {"index": 5}
    assert job["progress_log"][-1]["params"] == {"index": job_repository.MAX_JOB_EVENT_ROWS + 4}


def test_cleanup_old_jobs_removes_terminal_history_and_events(app):
    """Verify retention cleanup removes only old finished jobs."""
    del app
    with db_core_transaction() as conn:
        job_repository.save_job_snapshot(
            conn,
            {
                "id": "old-failed-job",
                "label": "Old failed job",
                "queue": "main",
                "status": "failed",
                "created_at": "2026-01-01T00:00:00Z",
                "finished_at": "2026-01-01T00:05:00Z",
                "undo_status": "unavailable",
            },
        )
        job_repository.save_job_snapshot(
            conn,
            {
                "id": "recent-completed-job",
                "label": "Recent completed job",
                "queue": "main",
                "status": "completed",
                "created_at": "2026-08-20T00:00:00Z",
                "finished_at": "2026-08-20T00:05:00Z",
                "undo_status": "unavailable",
            },
        )
        job_repository.save_job_snapshot(
            conn,
            {
                "id": "old-running-job",
                "label": "Old running job",
                "queue": "main",
                "status": "running",
                "created_at": "2026-01-01T00:00:00Z",
                "started_at": "2026-01-01T00:01:00Z",
                "undo_status": "unavailable",
            },
        )
        job_repository.save_job_event(
            conn,
            "old-failed-job",
            {
                "timestamp": "2026-01-01T00:05:00Z",
                "level": "error",
                "message": "Job failed: {error}",
                "params": {"error": "boom"},
            },
        )

        deleted = job_repository.cleanup_old_jobs(
            conn,
            retention_days=30,
            now="2026-08-25T00:00:00Z",
        )
        remaining_jobs = {row["id"] for row in conn.execute(background_jobs.select()).mappings()}
        remaining_events = [row["job_id"] for row in conn.execute(background_job_events.select()).mappings()]

    assert deleted == 1
    assert remaining_jobs == {"recent-completed-job", "old-running-job"}
    assert remaining_events == []
