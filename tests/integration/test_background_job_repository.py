"""Integration tests for persisted background job history."""

import json
import logging

import pytest
from sqlalchemy.exc import SQLAlchemyError

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


class RejectingExecutor:
    """Reject submitted jobs like a shut down executor would."""

    def submit(self, func, *args, **kwargs):
        """Raise instead of accepting work."""
        del func, args, kwargs
        raise RuntimeError("executor stopped")


def clear_runner_state() -> None:
    """Remove process-local jobs while preserving persisted history."""
    with runner._lock:
        runner._jobs.clear()
        runner._job_sequence = 0
        runner._job_history_degraded = False
        runner._job_history_degraded_detail = ""


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


def test_runner_persists_failed_job_when_executor_rejects(app, monkeypatch):
    """Verify persisted history records terminal failure when submit is rejected."""
    del app
    monkeypatch.setattr(runner, "_executor", RejectingExecutor())
    monkeypatch.setattr(runner, "_job_history_enabled", True)
    clear_runner_state()

    with pytest.raises(runner.BackgroundJobSubmissionError) as exc_info:
        runner.submit_background_job("Rejected persisted job", lambda: "placeholder")
    clear_runner_state()

    job = runner.get_background_job(exc_info.value.job_id)

    assert job is not None
    assert job["status"] == "failed"
    assert job["error"] == "Background job could not be queued: RuntimeError: executor stopped"
    assert job["can_cancel"] is False
    assert job["can_undo"] is False
    assert job["progress_log"] == [
        {
            "timestamp": job["progress_log"][0]["timestamp"],
            "level": "error",
            "message": "Background job could not be queued: {detail}",
            "params": {"detail": "RuntimeError: executor stopped"},
        }
    ]


def test_history_failure_is_logged_and_live_job_stays_listed(app, monkeypatch, caplog):
    """Verify failed history writes do not hide live process-local jobs."""
    del app
    executor = CapturingExecutor()
    monkeypatch.setattr(runner, "_executor", executor)
    monkeypatch.setattr(runner, "_job_history_enabled", True)
    clear_runner_state()
    original_persist_job_snapshot = job_repository.persist_job_snapshot

    def fail_job_snapshot(job):
        """Simulate a persistence failure after the executor accepts work."""
        del job
        raise SQLAlchemyError("x" * 2000)

    monkeypatch.setattr(job_repository, "persist_job_snapshot", fail_job_snapshot)
    with caplog.at_level(logging.WARNING, logger=runner.logger.name):
        job_id = runner.submit_background_job("Unpersisted live job", lambda: "placeholder")
    monkeypatch.setattr(job_repository, "persist_job_snapshot", original_persist_job_snapshot)

    jobs = runner.list_background_jobs(limit=None)
    status = runner.job_history_status()

    assert len(executor.submissions) == 1
    assert runner.count_background_jobs() == 1
    assert [job["id"] for job in jobs] == [job_id]
    assert jobs[0]["status"] == "queued"
    assert status["degraded"] is True
    assert "persist snapshot" in status["detail"]
    assert len(status["detail"]) <= runner.MAX_JOB_HISTORY_ERROR_DETAIL_LENGTH + 32
    assert "Background job history persist snapshot failed" in caplog.text
    assert "x" * 1000 not in caplog.text


def test_job_history_sanitizes_values_for_bounded_columns():
    """Verify job history rows and JSON params are bounded before SQL writes."""
    values = job_repository.job_row_values(
        {
            "id": "bounded-job",
            "label": "Import " + ("statement-" * 100),
            "queue": "not-a-queue",
            "status": "not-a-status",
            "created_at": "2026-08-25T00:00:00Z",
            "progress_params": {"detail": "x" * 10000, "metadata": {"nested": "value"}},
            "undo_status": "not-an-undo-status",
        }
    )
    params = json.loads(values["progress_params"])

    assert len(values["label"]) <= job_repository.JOB_LABEL_TEXT_LIMIT
    assert values["label"].endswith(job_repository.TEXT_TRUNCATION_SUFFIX)
    assert values["queue"] == "main"
    assert values["status"] == "queued"
    assert values["undo_status"] == "unavailable"
    assert len(values["progress_params"]) <= job_repository.JOB_PARAMS_TEXT_LIMIT
    assert params["_truncated"] is True
    assert len(params["detail"]) <= job_repository.JOB_JSON_VALUE_TEXT_LIMIT
    assert params["metadata"] == "{'nested': 'value'}"


def test_job_history_sanitizes_event_values_before_insert(app):
    """Verify persisted event level and params fit schema constraints."""
    del app
    with db_core_transaction() as conn:
        job_repository.save_job_snapshot(
            conn,
            {
                "id": "event-sanitized-job",
                "label": "Event sanitized job",
                "queue": "main",
                "status": "running",
                "created_at": "2026-08-25T00:00:00Z",
                "undo_status": "unavailable",
            },
        )
        job_repository.save_job_event(
            conn,
            "event-sanitized-job",
            {
                "timestamp": "2026-08-25T00:01:00Z",
                "level": "bad-level",
                "message": "",
                "params": {"detail": "x" * 10000},
            },
        )
        event = (
            conn.execute(background_job_events.select().where(background_job_events.c.job_id == "event-sanitized-job"))
            .mappings()
            .one()
        )

    params = json.loads(event["params"])
    assert event["level"] == "info"
    assert event["message"] == "Job event."
    assert len(event["params"]) <= job_repository.JOB_PARAMS_TEXT_LIMIT
    assert params["_truncated"] is True
    assert len(params["detail"]) <= job_repository.JOB_JSON_VALUE_TEXT_LIMIT


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
