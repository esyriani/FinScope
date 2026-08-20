"""Unit tests for upload service workflow seams."""

from finance_app.modules.upload import service as upload_service
from finance_app.modules.upload import workflow as upload_workflow


def test_submit_statement_import_job_uses_upload_workflow_boundary(monkeypatch):
    """Verify statement import jobs are submitted through the upload service boundary."""
    captured = {}

    def submit_for_test(label, func, *args, **kwargs):
        """Capture the queued background job for assertion."""
        captured["label"] = label
        captured["func"] = func
        captured["args"] = args
        captured["kwargs"] = kwargs
        return "job123"

    monkeypatch.setattr(upload_service, "submit_background_job", submit_for_test)

    result = upload_service.submit_statement_import_job(
        10,
        20,
        "credit_card",
        "standard",
        "csv",
        "raw",
        "statement.csv",
        "token123",
    )

    assert result == "job123"
    assert captured["label"] == "Import statement.csv"
    assert captured["func"] is upload_workflow.import_statement_transactions_job
    assert captured["args"] == (10, 20, "credit_card", "csv", "raw", "token123")
    assert captured["kwargs"]["import_mode"] == "standard"
    assert captured["kwargs"]["undo_handler"] is upload_workflow.undo_statement_upload_job
