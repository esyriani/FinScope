"""Shared upload workflow test helpers.

Provides database setup and assertion helpers for upload route and background
workflow tests. The helpers assume the standard seeded statement type and owner
settings fixtures are present.
"""

from finance_app.modules.upload import ai_workflow as upload_ai_workflow
from finance_app.modules.upload.repository import new_statement_import_token, reset_statement_import_state
from tests.support.database import (
    default_statement_type_id,
    insert_account,
    insert_statement,
    insert_transaction,
    statement_type_id_for_parser,
)


def first_statement_type_id(conn):
    """Return a valid active statement type id from the test database."""
    return default_statement_type_id(conn)


def statement_type_id(conn, parser_type):
    """Return an active statement type id for a parser type.

    Args:
        conn: Active test database connection.
        parser_type: Parser type to locate.

    Returns:
        Matching statement type id.
    """
    return statement_type_id_for_parser(conn, parser_type)


def create_account_statement(conn, filename="statement.csv"):
    """Create an account and statement row for workflow tests.

    Args:
        conn: Active test database connection.
        filename: Statement filename and checksum suffix.

    Returns:
        A tuple of ``(account_id, statement_id)``.
    """
    account_id = insert_account(conn, "Personal")
    statement_id = insert_statement(
        conn,
        account_id=account_id,
        statement_type_id=first_statement_type_id(conn),
        filename=filename,
        checksum=f"checksum-{filename}",
    )
    return account_id, statement_id


def queue_statement_import_attempt(conn, statement_id):
    """Mark a statement queued and return its import attempt token."""
    import_token = new_statement_import_token()
    assert reset_statement_import_state(conn, statement_id, import_token) is True
    conn.commit()
    return import_token


def insert_llm_progress_transactions(conn, statement_id, account_id):
    """Insert unknown transactions that exercise success, unresolved, and request-error batches."""
    for description, tx_date, amount, fingerprint in (
        ("UNKNOWN GOOD", "2026-01-02", 12.34, "llm-progress-good"),
        ("UNKNOWN UNRESOLVED", "2026-01-03", 23.45, "llm-progress-unresolved"),
        ("UNKNOWN TIMEOUT", "2026-01-04", 34.56, "llm-progress-timeout"),
    ):
        insert_transaction(
            conn,
            statement_id=statement_id,
            account_id=account_id,
            tx_date=tx_date,
            description=description,
            amount=amount,
            category="UNKNOWN",
            needs_review=1,
            fingerprint=fingerprint,
        )


def build_llm_progress_categorizer(batches):
    """Return a deterministic categorizer for the AI batch progress test."""

    def categorize_for_test(transactions, conn=None):
        """Return one successful category and two unresolved AI outcomes."""
        del conn
        batches.append([tx["description"] for tx in transactions])
        if len(batches) == 1:
            upload_ai_workflow.llm_module.record_llm_request_status(
                "ok",
                requested_count=len(transactions),
                returned_count=len(transactions),
            )
            transactions[0].update(
                {
                    "category": "Food",
                    "needs_review": 0,
                    "category_source": "ai",
                    "category_confidence": 0.94,
                    "category_rule_id": None,
                    "categorized_at": "2026-05-09T00:00:00Z",
                    "reviewed_at": None,
                    "tags": [],
                }
            )
            transactions[1].update(
                {
                    "category": "UNKNOWN",
                    "needs_review": 1,
                    "category_source": "unknown",
                    "category_confidence": None,
                    "category_rule_id": None,
                    "categorized_at": None,
                    "reviewed_at": None,
                    "tags": [],
                    "category_metadata": {"failure_reason": "llm_no_results"},
                }
            )
            return transactions

        upload_ai_workflow.llm_module.record_llm_request_status(
            "request_error",
            error_type="TimeoutError",
            detail="request timed out",
            requested_count=len(transactions),
        )
        transactions[0].update(
            {
                "category": "UNKNOWN",
                "needs_review": 1,
                "category_source": "unknown",
                "category_confidence": None,
                "category_rule_id": None,
                "categorized_at": None,
                "reviewed_at": None,
                "tags": [],
                "category_metadata": {"failure_reason": "request_error"},
            }
        )
        return transactions

    return categorize_for_test


def assert_llm_progress_log_entries(log_entries):
    """Assert the AI progress job emitted the expected structured log entries."""
    messages = [entry["message"] for entry in log_entries]
    assert "Starting AI categorization for {total} unknown transactions." in messages
    assert "Starting batch {start}-{end} of {total}." in messages
    assert "AI request issue in batch {start}-{end}: {error_type}: {detail}" in messages
    assert "Batch {start}-{end} kept {unknown} transaction unknown for review." in messages
    assert "Finished batch {start}-{end}: {processed} processed; {updated} categorized total." in messages
    assert "AI categorization completed: {summary}" in messages

    request_issue = next(
        entry
        for entry in log_entries
        if entry["message"] == "AI request issue in batch {start}-{end}: {error_type}: {detail}"
    )
    assert request_issue["level"] == "warning"
    assert request_issue["params"]["start"] == 3
    assert request_issue["params"]["end"] == 3
    assert request_issue["params"]["error_type"] == "TimeoutError"

    unresolved = [
        entry
        for entry in log_entries
        if entry["message"] == "Batch {start}-{end} kept {unknown} transaction unknown for review."
    ]
    assert [entry["params"]["reasons"] for entry in unresolved] == [
        "llm_no_results: 1",
        "request_error: 1",
    ]


def assert_llm_progress_updates(progress_updates):
    """Assert the final progress update reports all rows and one AI categorization."""
    assert progress_updates[-1]["current"] == 3
    assert progress_updates[-1]["total"] == 3
    assert progress_updates[-1]["params"]["updated"] == 1
