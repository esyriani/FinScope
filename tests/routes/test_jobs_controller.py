"""Route tests for the background jobs feature."""

from sqlalchemy import text
import pytest

from finance_app.background import runner
from finance_app.core.csrf import CSRF_FIELD_NAME
from finance_app.core.filters import format_datetime
from tests.support.web import set_csrf_token


class CapturingExecutor:
    """Capture submitted jobs without running them asynchronously."""

    def __init__(self):
        """Prepare an empty submission log."""
        self.submissions = []

    def submit(self, func, *args, **kwargs):
        """Record the submitted job payload."""
        self.submissions.append((func, args, kwargs))
        return None


@pytest.fixture(autouse=True)
def isolated_background_runner(monkeypatch):
    """Reset global background runner state around each controller test."""
    with runner._lock:
        runner._jobs.clear()
        runner._job_sequence = 0
    monkeypatch.setattr(runner, "_executor", CapturingExecutor())
    monkeypatch.setattr(runner, "_ai_executor", CapturingExecutor())
    yield
    with runner._lock:
        runner._jobs.clear()
        runner._job_sequence = 0


def complete_job(label="Completed job", result="done", undo_handler=None):
    """Create a completed background job for route tests."""
    job_id = runner.submit_background_job(label, lambda: "placeholder", undo_handler=undo_handler)
    runner.run_job(job_id, lambda: result, (), {})
    return job_id


def test_job_status_json_returns_public_job(client):
    """Verify that the JSON status route returns the public job snapshot."""
    job_id = complete_job("JSON job", result="json result", undo_handler=lambda: "undone")

    response = client.get(f"/jobs/{job_id}.json")

    assert response.status_code == 200
    assert response.get_json() == {
        "id": job_id,
        "label": "JSON job",
        "queue": "main",
        "status": "completed",
        "created_at": response.get_json()["created_at"],
        "started_at": response.get_json()["started_at"],
        "finished_at": response.get_json()["finished_at"],
        "result": "json result",
        "error": None,
        "progress_current": 0,
        "progress_total": None,
        "progress_percent": None,
        "progress_message": "",
        "progress_params": {},
        "progress_log": [],
        "cancel_requested": False,
        "undo_status": "available",
        "undo_result": None,
        "undo_error": None,
        "undone_at": None,
        "can_undo": True,
        "can_cancel": False,
    }


def test_job_status_json_returns_404_for_missing_job(client):
    """Verify that missing jobs return a JSON 404 response."""
    response = client.get("/jobs/missing.json")

    assert response.status_code == 404
    assert response.get_json() == {"error": "Job not found."}


def test_job_status_json_formats_progress_log_timestamps(client):
    """Verify JSON job progress logs include locale-ready timestamp labels."""
    job_id = runner.submit_background_job(
        "AI JSON log job",
        lambda: "placeholder",
        queue=runner.AI_JOB_QUEUE,
    )
    runner.append_background_job_log(
        "Starting batch {start}-{end} of {total}.",
        params={"start": 1, "end": 20, "total": 20},
        job_id=job_id,
    )
    timestamp = runner.get_background_job(job_id)["progress_log"][0]["timestamp"]

    response = client.get(f"/jobs/{job_id}.json")
    payload = response.get_json()

    assert response.status_code == 200
    assert payload["progress_log"][0]["timestamp"] == timestamp
    assert payload["progress_log"][0]["timestamp_label"] == format_datetime(timestamp)
    assert "T" not in payload["progress_log"][0]["timestamp_label"]


def test_jobs_page_paginates_and_renders_public_job_data(client, core_conn):
    """Verify that the jobs page renders paginated newest-first job rows."""
    core_conn.execute(text("""
        UPDATE user_settings
        SET value = '2'
        WHERE key = 'default_table_page_size'
          AND user_id = (SELECT id FROM users WHERE username = 'owner')
        """))
    core_conn.commit()
    oldest_job_id = complete_job("Oldest job", result="old")
    complete_job("Middle job", result="middle")
    complete_job("Newest job", result="new")

    response = client.get("/jobs?page=2")
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Showing 3-3 of 3 jobs" in body
    assert "Oldest job" in body
    assert "old" in body
    oldest_created_at = runner.get_background_job(oldest_job_id)["created_at"]
    assert format_datetime(oldest_created_at) in body
    assert oldest_created_at not in body
    assert "Newest job" not in body
    assert "Middle job" not in body


def test_jobs_page_renders_expandable_progress_for_running_ai_job(client):
    """Verify running AI jobs expose an expandable progress row."""
    job_id = runner.submit_background_job(
        "AI progress job",
        lambda: "placeholder",
        queue=runner.AI_JOB_QUEUE,
    )
    runner.update_job(job_id, status="running", started_at=runner.utc_now())
    runner.update_background_job_progress(
        current=3,
        total=10,
        message="Processed {current} of {total}; {updated} categorized.",
        params={"current": 3, "total": 10, "updated": 2},
        job_id=job_id,
    )
    runner.append_background_job_log(
        "Starting batch {start}-{end} of {total}.",
        params={"start": 1, "end": 10, "total": 10},
        job_id=job_id,
    )

    response = client.get("/jobs")
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert f'id="job-progress-{job_id}"' in body
    assert "data-ai-job-progress" in body
    assert f'data-job-status-url="/jobs/{job_id}.json"' in body
    assert 'role="progressbar"' in body
    assert 'aria-valuenow="30"' in body
    assert "width: 30%" in body
    assert "30% complete" in body
    assert "Processed 3 of 10; 2 categorized." in body
    assert "Log" in body
    assert "Starting batch 1-10 of 10." in body


def test_jobs_page_keeps_ai_progress_log_available_after_completion(client):
    """Verify completed AI jobs still expose their collapsible progress log."""
    job_id = runner.submit_background_job(
        "Completed AI log job",
        lambda: "placeholder",
        queue=runner.AI_JOB_QUEUE,
    )
    runner.append_background_job_log(
        "AI categorization completed: {summary}",
        params={"summary": "1 automatically categorized."},
        job_id=job_id,
    )
    runner.update_job(
        job_id,
        status="completed",
        started_at=runner.utc_now(),
        finished_at=runner.utc_now(),
        result="1 automatically categorized.",
    )

    response = client.get("/jobs")
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert f'id="job-progress-{job_id}"' in body
    assert '<tr class="collapse " id=' in body
    assert "AI categorization completed: 1 automatically categorized." in body


def test_jobs_page_renders_ajax_auto_refresh_controls(client):
    """Verify the jobs page exposes AJAX refresh controls with a countdown."""
    complete_job("Auto refresh job", result="done")

    response = client.get("/jobs?page=1")
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "data-jobs-auto-refresh" in body
    assert 'data-jobs-auto-refresh-interval="10"' in body
    assert "data-jobs-refresh-button" in body
    assert 'data-jobs-refresh-interval="10"' in body
    assert "data-jobs-refresh-target='[data-ajax-refresh-target=\"jobs-actions\"]'" in body
    assert "Refresh (10)" in body


def test_undo_job_post_runs_undo_and_flashes_result(client):
    """Verify that undo POST calls the runner and shows the undo result."""
    undo_calls = []
    job_id = complete_job(
        "Undo route job",
        result="created data",
        undo_handler=lambda: undo_calls.append("called") or "Undo route complete.",
    )

    response = client.post(
        f"/jobs/{job_id}/undo",
        data={
            CSRF_FIELD_NAME: set_csrf_token(client),
            "next": "/jobs?page=1",
        },
        follow_redirects=True,
    )
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert undo_calls == ["called"]
    assert "Undo route complete." in body
    assert runner.get_background_job(job_id)["undo_status"] == "undone"


def test_undo_job_post_handles_runner_error_cases(client):
    """Verify that undo POST flashes user-facing errors for failure paths."""
    no_undo_job_id = complete_job("No undo route job", result="done")
    failing_job_id = complete_job(
        "Failing undo route job",
        result="done",
        undo_handler=lambda: (_ for _ in ()).throw(RuntimeError("cannot undo")),
    )
    token = set_csrf_token(client)

    missing_response = client.post(
        "/jobs/missing/undo",
        data={CSRF_FIELD_NAME: token},
        follow_redirects=True,
    )
    unavailable_response = client.post(
        f"/jobs/{no_undo_job_id}/undo",
        data={CSRF_FIELD_NAME: token},
        follow_redirects=True,
    )
    failing_response = client.post(
        f"/jobs/{failing_job_id}/undo",
        data={CSRF_FIELD_NAME: token},
        follow_redirects=True,
    )

    assert "Job not found." in missing_response.get_data(as_text=True)
    assert "This job does not have anything to undo." in unavailable_response.get_data(as_text=True)
    assert "Could not undo job: RuntimeError: cannot undo" in failing_response.get_data(as_text=True)


def test_cancel_job_post_marks_queued_job_cancelled(client):
    """Verify cancel POST cancels a queued job and flashes the result."""
    job_id = runner.submit_background_job("Cancel route job", lambda: "placeholder")

    response = client.post(
        f"/jobs/{job_id}/cancel",
        data={
            CSRF_FIELD_NAME: set_csrf_token(client),
            "next": "/jobs",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert "Job cancelled." in response.get_data(as_text=True)
    assert runner.get_background_job(job_id)["status"] == "cancelled"


def test_categorize_all_unknowns_queues_ai_job(client, core_conn, monkeypatch):
    """Verify the Jobs page can queue AI categorization for all unknown rows."""
    core_conn.execute(text("""
        INSERT INTO transactions (tx_date, description, amount, category, fingerprint)
        VALUES ('2026-01-02', 'UNKNOWN SHOP', 12.34, 'UNKNOWN', 'jobs-ai-unknown')
        """))
    core_conn.commit()
    submitted = []

    def queue_for_test():
        """Capture the all-unknown AI queue request."""
        submitted.append("queued")
        return "aijob12345"

    from finance_app.modules.jobs import controller as jobs_controller

    monkeypatch.setattr(jobs_controller.upload_workflow, "queue_all_unknown_llm_categorization", queue_for_test)

    response = client.post(
        "/jobs/ai/categorize-unknowns",
        data={CSRF_FIELD_NAME: set_csrf_token(client), "next": "/jobs"},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert submitted == ["queued"]
    assert "AI categorization queued for 1 unknown transaction." in response.get_data(as_text=True)


def test_cancel_queued_ai_jobs_route_clears_only_ai_queue(client):
    """Verify queued AI jobs can be cleared without cancelling main-queue jobs."""
    main_job = runner.submit_background_job("Main job", lambda: "main")
    ai_job = runner.submit_background_job("AI job", lambda: "ai", queue=runner.AI_JOB_QUEUE)

    response = client.post(
        "/jobs/ai/cancel-queued",
        data={CSRF_FIELD_NAME: set_csrf_token(client), "next": "/jobs"},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert "Cancelled 1 queued AI job." in response.get_data(as_text=True)
    assert runner.get_background_job(main_job)["status"] == "queued"
    assert runner.get_background_job(ai_job)["status"] == "cancelled"
