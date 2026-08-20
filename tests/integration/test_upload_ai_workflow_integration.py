"""Integration tests for upload AI workflow handoffs."""

import json

from sqlalchemy import text
from tests.support.database import set_owner_setting
from tests.support.upload import (
    assert_llm_progress_log_entries,
    assert_llm_progress_updates,
    build_llm_progress_categorizer,
    create_account_statement,
    insert_llm_progress_transactions,
    queue_statement_import_attempt,
)
from tests.support.web import set_csrf_token

from finance_app.core.csrf import CSRF_FIELD_NAME
from finance_app.modules.categories.repository import resolve_category_id
from finance_app.modules.categories.taxonomy import get_transaction_tag_names
from finance_app.modules.upload import ai_workflow as upload_ai_workflow
from finance_app.modules.upload import messages as upload_messages
from finance_app.modules.upload import workflow as upload_workflow


def run_statement_import_job(conn, statement_id, account_id, statement_type, extension, raw_text, **kwargs):
    """Queue and run one statement import job with a matching claim token."""
    import_token = queue_statement_import_attempt(conn, statement_id)
    return upload_workflow.import_statement_transactions_job(
        statement_id,
        account_id,
        statement_type,
        extension,
        raw_text,
        import_token,
        **kwargs,
    )


def test_import_statement_job_keeps_ai_candidates_for_manual_estimate_first_run(app, core_conn, monkeypatch):
    """Verify imports do not queue AI before a user reviews token estimates."""
    account_id, statement_id = create_account_statement(core_conn, "unknowns.csv")
    submitted_jobs = []

    def capture_job(label, func, *args, **kwargs):
        """Capture LLM follow-up job submission details."""
        submitted_jobs.append(
            {
                "label": label,
                "func": func,
                "args": args,
                "kwargs": kwargs,
            }
        )
        return "llm-job-id"

    monkeypatch.setattr(upload_ai_workflow, "submit_background_job", capture_job)

    message = run_statement_import_job(
        core_conn,
        statement_id,
        account_id,
        "credit_card",
        "csv",
        "Date,Description,Amount\n2026-01-02,UNKNOWN SHOP,12.34\n",
    )

    rows = core_conn.execute(
        text("""
        SELECT description, category, needs_review
        FROM transactions
        WHERE statement_id = :p0
        """),
        {"p0": statement_id},
    ).fetchall()
    assert "1 unknown transaction can be categorized with AI from Uploaded statements." in message
    assert [tuple(row) for row in rows] == [("UNKNOWN SHOP", "UNKNOWN", 1)]
    statement = core_conn.execute(
        text("""
        SELECT import_status, imported_count, skipped_count, ignored_count,
               llm_candidate_count, import_error
        FROM statements
        WHERE id = :p0
        """),
        {"p0": statement_id},
    ).fetchone()
    assert tuple(statement) == ("completed", 1, 0, 0, 1, None)
    assert submitted_jobs == []


def test_import_statement_job_auto_queues_ai_when_token_confirmation_disabled(app, core_conn, monkeypatch):
    """Verify imports queue statement AI automatically when token confirmation is disabled."""
    set_owner_setting(core_conn, "confirm_ai_token_usage_enabled", "0")
    account_id, statement_id = create_account_statement(core_conn, "auto-ai.csv")
    submitted_jobs = []

    def capture_job(label, func, *args, **kwargs):
        """Capture LLM follow-up job submission details."""
        submitted_jobs.append(
            {
                "label": label,
                "func": func,
                "args": args,
                "kwargs": kwargs,
            }
        )
        return "llm-auto-job-id"

    monkeypatch.setattr(upload_ai_workflow, "submit_background_job", capture_job)

    message = run_statement_import_job(
        core_conn,
        statement_id,
        account_id,
        "credit_card",
        "csv",
        "Date,Description,Amount\n2026-01-02,UNKNOWN SHOP,12.34\n",
    )

    assert "1 unknown transaction queued for AI categorization. AI job: llm-auto." in message
    assert submitted_jobs == [
        {
            "label": f"AI categorize statement {statement_id}",
            "func": upload_ai_workflow.categorize_statement_unknown_transactions_job,
            "args": (statement_id,),
            "kwargs": {"queue": "ai"},
        }
    ]


def test_import_statement_job_reports_no_ai_candidates_when_none_remain(app, core_conn, monkeypatch):
    """Verify imports without unknown rows do not mention manual AI reruns."""
    account_id, statement_id = create_account_statement(core_conn, "manual-ai.csv")
    submitted_jobs = []

    monkeypatch.setattr(
        upload_ai_workflow,
        "submit_background_job",
        lambda *args, **kwargs: submitted_jobs.append((args, kwargs)),
    )

    message = run_statement_import_job(
        core_conn,
        statement_id,
        account_id,
        "credit_card",
        "csv",
        "Date,Description,Amount\n",
    )

    assert submitted_jobs == []
    assert "can be categorized with AI" not in message


def test_categorize_statement_unknown_transactions_job_updates_rows_and_tags(app, core_conn):
    """Verify that LLM categorization results are persisted to uploaded transactions."""
    _, statement_id = create_account_statement(core_conn, "llm.csv")
    unknown_id = core_conn.execute(
        text("""
        INSERT INTO transactions (
            statement_id,
            tx_date,
            description,
            amount,
            category,
            category_id,
            needs_review,
            fingerprint
        )
        VALUES (:p0, '2026-01-02', 'UNKNOWN SHOP', 12.34, 'UNKNOWN', :category_id, 1, 'llm-unknown')
        """),
        {"p0": statement_id, "category_id": resolve_category_id(core_conn, "UNKNOWN")},
    ).lastrowid
    known_id = core_conn.execute(
        text("""
        INSERT INTO transactions (
            statement_id,
            tx_date,
            description,
            amount,
            category,
            category_id,
            needs_review,
            fingerprint
        )
        VALUES (:p0, '2026-01-03', 'KNOWN SHOP', 50.00, 'Utilities', :category_id, 0, 'llm-known')
        """),
        {"p0": statement_id, "category_id": resolve_category_id(core_conn, "Utilities")},
    ).lastrowid
    ignored_id = core_conn.execute(
        text("""
        INSERT INTO transactions (
            statement_id,
            tx_date,
            description,
            amount,
            category,
            category_id,
            needs_review,
            ignored,
            fingerprint
        )
        VALUES (:p0, '2026-01-04', 'IGNORED SHOP', 25.00, 'UNKNOWN', :category_id, 1, 1, 'llm-ignored')
        """),
        {"p0": statement_id, "category_id": resolve_category_id(core_conn, "UNKNOWN")},
    ).lastrowid
    core_conn.commit()

    def categorize_for_test(transactions, conn=None, use_llm=True):
        """Return deterministic LLM categorization output for selected unknown rows."""
        assert use_llm is True
        assert [tx["id"] for tx in transactions] == [unknown_id]
        transactions[0].update(
            {
                "category": "Groceries",
                "needs_review": 0,
                "category_source": "ai",
                "category_confidence": 0.93,
                "category_rule_id": None,
                "categorized_at": "2026-05-09T00:00:00Z",
                "reviewed_at": None,
                "tags": ["Tax"],
            }
        )
        return transactions

    message = upload_ai_workflow.categorize_statement_unknown_transactions_job(
        statement_id,
        transaction_categorizer=categorize_for_test,
    )

    updated = core_conn.execute(
        text("""
        SELECT category, needs_review, category_source, category_confidence,
               category_rule_id, categorized_at, reviewed_at
        FROM transactions
        WHERE id = :p0
        """),
        {"p0": unknown_id},
    ).fetchone()
    known = core_conn.execute(text("SELECT category FROM transactions WHERE id = :p0"), {"p0": known_id}).fetchone()
    ignored = core_conn.execute(text("SELECT category FROM transactions WHERE id = :p0"), {"p0": ignored_id}).fetchone()
    assert message == "1 automatically categorized: 1 AI."
    assert tuple(updated) == (
        "Groceries",
        0,
        "ai",
        0.93,
        None,
        "2026-05-09T00:00:00Z",
        None,
    )
    assert get_transaction_tag_names(core_conn, unknown_id) == ["Tax"]
    assert known._mapping["category"] == "Utilities"
    assert ignored._mapping["category"] == "UNKNOWN"


def test_categorize_statement_unknown_transactions_job_persists_unknown_llm_metadata(app, core_conn):
    """Verify metadata-only LLM unknown outcomes are persisted for existing rows."""
    del app
    _, statement_id = create_account_statement(core_conn, "llm-unknown-metadata.csv")
    unknown_id = core_conn.execute(
        text("""
        INSERT INTO transactions (
            statement_id,
            tx_date,
            description,
            amount,
            category,
            needs_review,
            fingerprint
        )
        VALUES (:p0, '2026-01-02', 'UNKNOWN SHOP', 12.34, 'UNKNOWN', 1, 'llm-unknown-metadata')
        """),
        {"p0": statement_id},
    ).lastrowid
    core_conn.commit()

    def categorize_for_test(transactions, conn=None, use_llm=True):
        """Return an explicit unknown LLM outcome with audit metadata."""
        del conn
        assert use_llm is True
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
                "category_metadata": json.dumps(
                    {
                        "decision_source": "llm",
                        "failure_reason": "llm_no_results",
                        "review_required": True,
                    }
                ),
            }
        )
        return transactions

    message = upload_ai_workflow.categorize_statement_unknown_transactions_job(
        statement_id,
        transaction_categorizer=categorize_for_test,
    )

    row = core_conn.execute(
        text("""
        SELECT category, needs_review, category_source, category_metadata
        FROM transactions
        WHERE id = :p0
        """),
        {"p0": unknown_id},
    ).fetchone()
    metadata = json.loads(row._mapping["category_metadata"])
    assert message == "0 automatically categorized."
    assert row._mapping["category"] == "UNKNOWN"
    assert row._mapping["needs_review"] == 1
    assert row._mapping["category_source"] == "unknown"
    assert metadata["failure_reason"] == "llm_no_results"


def test_categorize_unknown_transactions_job_logs_real_batch_progress(app, core_conn):
    """Verify AI jobs publish batch, issue, unresolved, and completion log entries."""
    del app
    account_id, statement_id = create_account_statement(core_conn, "llm-progress.csv")
    insert_llm_progress_transactions(core_conn, statement_id, account_id)
    progress_updates = []
    log_entries = []
    batches = []

    def capture_progress(**kwargs):
        """Capture progress updates without requiring a running background thread."""
        progress_updates.append(kwargs)

    def capture_log(message, params=None, level="info"):
        """Capture workflow log entries emitted by the AI categorization loop."""
        log_entries.append({"message": message, "params": dict(params or {}), "level": level})

    message = upload_ai_workflow.categorize_statement_unknown_transactions_job(
        statement_id,
        batch_size=2,
        transaction_categorizer=build_llm_progress_categorizer(batches),
        progress_updater=capture_progress,
        log_appender=capture_log,
    )

    assert batches == [
        ["UNKNOWN GOOD", "UNKNOWN UNRESOLVED"],
        ["UNKNOWN TIMEOUT"],
    ]
    assert message == "1 automatically categorized: 1 AI."
    assert_llm_progress_log_entries(log_entries)
    assert_llm_progress_updates(progress_updates)


def test_categorize_statement_unknown_transactions_job_reports_no_work(app, core_conn):
    """Verify that the LLM job exits cleanly when there are no unknown rows."""
    _, statement_id = create_account_statement(core_conn, "none.csv")
    calls = []

    message = upload_ai_workflow.categorize_statement_unknown_transactions_job(
        statement_id,
        transaction_categorizer=lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    assert message == "No unknown transactions needed AI categorization."
    assert calls == []


def test_categorize_statement_unknowns_route_queues_statement_ai(owner_client, core_conn, monkeypatch):
    """Verify Uploaded statements can queue AI reruns for remaining unknown rows."""
    _, statement_id = create_account_statement(core_conn, "manual-statement-ai.csv")
    core_conn.execute(
        text("""
        INSERT INTO transactions (
            statement_id,
            tx_date,
            description,
            amount,
            category,
            needs_review,
            fingerprint
        )
        VALUES (:p0, '2026-01-02', 'UNKNOWN SHOP', 12.34, 'UNKNOWN', 1, 'manual-statement-ai')
        """),
        {"p0": statement_id},
    )
    core_conn.commit()
    submitted = []

    def queue_for_test(queued_statement_id):
        """Capture the statement-level AI queue request."""
        submitted.append(queued_statement_id)
        return "statementaijob123"

    monkeypatch.setattr(upload_workflow, "queue_statement_llm_categorization", queue_for_test)

    response = owner_client.post(
        f"/upload/{statement_id}/categorize-unknowns",
        data={
            CSRF_FIELD_NAME: set_csrf_token(owner_client),
            "next": "/upload",
            "ai_token_estimate_confirmed": "1",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert submitted == [statement_id]
    assert "AI categorization queued for 1 unknown transaction." in response.get_data(as_text=True)


def test_automatic_categorization_message_reports_source_breakdown():
    """Verify background job summaries distinguish similarity and AI sources."""
    message = upload_messages.automatic_categorization_message(
        76,
        {
            "history": 50,
            "ai": 26,
        },
    )

    assert message == "76 automatically categorized: 50 similarity, 26 AI."
