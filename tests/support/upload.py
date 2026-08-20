"""Shared upload workflow test helpers.

Provides database setup and assertion helpers for upload route and background
workflow tests. The helpers assume the standard seeded statement type and owner
settings fixtures are present.
"""

from sqlalchemy import text

from finance_app.modules.upload import ai_workflow as upload_ai_workflow
from finance_app.modules.upload.repository import new_statement_import_token, reset_statement_import_state


def first_statement_type_id(conn):
    """Return a valid active statement type id from the test database."""
    return conn.execute(text("""
        SELECT id
        FROM statement_types
        WHERE active = 1
        ORDER BY id
        LIMIT 1
        """)).fetchone()._mapping["id"]


def statement_type_id(conn, parser_type):
    """Return an active statement type id for a parser type.

    Args:
        conn: Active test database connection.
        parser_type: Parser type to locate.

    Returns:
        Matching statement type id.
    """
    return (
        conn.execute(
            text("""
        SELECT id
        FROM statement_types
        WHERE active = 1
        AND parser_type = :p0
        ORDER BY id
        LIMIT 1
        """),
            {"p0": parser_type},
        )
        .fetchone()
        ._mapping["id"]
    )


def create_account_statement(conn, filename="statement.csv"):
    """Create an account and statement row for workflow tests.

    Args:
        conn: Active test database connection.
        filename: Statement filename and checksum suffix.

    Returns:
        A tuple of ``(account_id, statement_id)``.
    """
    account_id = conn.execute(text("""
        INSERT INTO accounts (name)
        VALUES ('Personal')
        """)).lastrowid
    statement_id = conn.execute(
        text("""
        INSERT INTO statements (account_id, statement_type_id, filename, checksum, raw_text)
        VALUES (:p0, :p1, :p2, :p3, '')
        """),
        {"p0": account_id, "p1": first_statement_type_id(conn), "p2": filename, "p3": f"checksum-{filename}"},
    ).lastrowid
    conn.commit()
    return account_id, statement_id


def queue_statement_import_attempt(conn, statement_id):
    """Mark a statement queued and return its import attempt token."""
    import_token = new_statement_import_token()
    assert reset_statement_import_state(conn, statement_id, import_token) is True
    conn.commit()
    return import_token


def insert_llm_progress_transactions(conn, statement_id, account_id):
    """Insert unknown transactions that exercise success, unresolved, and request-error batches."""
    conn.execute(
        text("""
        INSERT INTO transactions (
            statement_id,
            account_id,
            tx_date,
            description,
            amount,
            category,
            needs_review,
            fingerprint
        )
        VALUES (:p0, :p1, :p2, :p3, :p4, 'UNKNOWN', 1, :p5)
        """),
        [
            {
                "p0": statement_id,
                "p1": account_id,
                "p2": "2026-01-02",
                "p3": "UNKNOWN GOOD",
                "p4": 12.34,
                "p5": "llm-progress-good",
            },
            {
                "p0": statement_id,
                "p1": account_id,
                "p2": "2026-01-03",
                "p3": "UNKNOWN UNRESOLVED",
                "p4": 23.45,
                "p5": "llm-progress-unresolved",
            },
            {
                "p0": statement_id,
                "p1": account_id,
                "p2": "2026-01-04",
                "p3": "UNKNOWN TIMEOUT",
                "p4": 34.56,
                "p5": "llm-progress-timeout",
            },
        ],
    )
    conn.commit()


def build_llm_progress_categorizer(batches):
    """Return a deterministic categorizer for the AI batch progress test."""

    def categorize_for_test(transactions, conn=None, use_llm=True):
        """Return one successful category and two unresolved AI outcomes."""
        del conn
        assert use_llm is True
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
