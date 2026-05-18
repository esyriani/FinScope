"""Route tests for the background jobs feature."""

import pytest

from finance_app.background import runner
from finance_app.core.csrf import CSRF_FIELD_NAME, CSRF_SESSION_KEY


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
    yield
    with runner._lock:
        runner._jobs.clear()
        runner._job_sequence = 0


def set_csrf_token(client, token="test-csrf-token"):
    """Store a CSRF token in the test client's session."""
    with client.session_transaction() as session:
        session[CSRF_SESSION_KEY] = token
    return token


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
        "status": "completed",
        "created_at": response.get_json()["created_at"],
        "started_at": response.get_json()["started_at"],
        "finished_at": response.get_json()["finished_at"],
        "result": "json result",
        "error": None,
        "undo_status": "available",
        "undo_result": None,
        "undo_error": None,
        "undone_at": None,
        "can_undo": True,
    }


def test_job_status_json_returns_404_for_missing_job(client):
    """Verify that missing jobs return a JSON 404 response."""
    response = client.get("/jobs/missing.json")

    assert response.status_code == 404
    assert response.get_json() == {"error": "Job not found."}


def test_jobs_page_paginates_and_renders_public_job_data(client, db_conn):
    """Verify that the jobs page renders paginated newest-first job rows."""
    db_conn.execute(
        """
        UPDATE user_settings
        SET value = '2'
        WHERE key = 'default_table_page_size'
          AND user_id = (SELECT id FROM users WHERE username = 'owner')
        """
    )
    db_conn.commit()
    complete_job("Oldest job", result="old")
    complete_job("Middle job", result="middle")
    complete_job("Newest job", result="new")

    response = client.get("/jobs?page=2")
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Showing 3-3 of 3 jobs" in body
    assert "Oldest job" in body
    assert "old" in body
    assert "Newest job" not in body
    assert "Middle job" not in body


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
