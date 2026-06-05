"""Unit tests for upload controller workflow seams."""

from finance_app.modules.upload import controller as upload_controller
from finance_app.modules.upload import workflow as upload_workflow


def test_controller_import_transactions_passes_dependencies_without_replacing_globals(monkeypatch):
    """Verify upload controller import wrapper does not mutate workflow globals."""
    original_categorizer = upload_workflow.categorize_transactions
    original_tag_setter = upload_workflow.set_transaction_tags
    captured = {}

    def import_for_test(*args, **kwargs):
        """Capture workflow import dependencies for assertion."""
        captured["args"] = args
        captured["kwargs"] = kwargs
        assert upload_workflow.categorize_transactions is original_categorizer
        assert upload_workflow.set_transaction_tags is original_tag_setter
        return 1, 2, 3

    monkeypatch.setattr(upload_workflow, "import_transactions", import_for_test)

    result = upload_controller.import_transactions(
        object(),
        10,
        20,
        "credit_card",
        "csv",
        "raw",
    )

    assert result == (1, 2, 3)
    assert captured["kwargs"]["categorizer"] is upload_controller.categorize_transactions
    assert captured["kwargs"]["tag_setter"] is upload_controller.set_transaction_tags
    assert upload_workflow.categorize_transactions is original_categorizer
    assert upload_workflow.set_transaction_tags is original_tag_setter
