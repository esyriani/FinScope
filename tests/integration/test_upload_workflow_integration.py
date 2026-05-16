"""Integration tests for upload background workflow handoffs."""

import io
import json

from finance_app.core.csrf import CSRF_FIELD_NAME, CSRF_SESSION_KEY
from finance_app.modules.categories.taxonomy import get_transaction_tag_names
from finance_app.modules.upload import controller as upload_controller
from finance_app.modules.upload import workflow as upload_workflow


def set_csrf_token(client, token="test-csrf-token"):
    """Store a CSRF token in the test client's session."""
    with client.session_transaction() as session:
        session[CSRF_SESSION_KEY] = token
    return token


def first_statement_type_id(conn):
    """Return a valid active statement type id from the test database."""
    return conn.execute(
        """
        SELECT id
        FROM statement_types
        WHERE active = 1
        ORDER BY id
        LIMIT 1
        """
    ).fetchone()["id"]


def statement_type_id(conn, parser_type):
    """Return an active statement type id for a parser type."""
    return conn.execute(
        """
        SELECT id
        FROM statement_types
        WHERE active = 1
        AND parser_type = ?
        ORDER BY id
        LIMIT 1
        """,
        (parser_type,),
    ).fetchone()["id"]


def create_account_statement(conn, filename="statement.csv"):
    """Create an account and statement row for workflow tests."""
    account_id = conn.execute(
        """
        INSERT INTO accounts (name)
        VALUES ('Personal')
        """
    ).lastrowid
    statement_id = conn.execute(
        """
        INSERT INTO statements (account_id, statement_type_id, filename, checksum, raw_text)
        VALUES (?, ?, ?, ?, '')
        """,
        (
            account_id,
            first_statement_type_id(conn),
            filename,
            f"checksum-{filename}",
        ),
    ).lastrowid
    conn.commit()
    return account_id, statement_id


def test_upload_route_submits_background_import_job(client, db_conn, monkeypatch):
    """Verify that a valid upload stores the statement and queues the import job."""
    submitted_jobs = []

    def capture_job(label, func, *args, undo_handler=None, undo_args=None, undo_kwargs=None, **kwargs):
        """Capture the submitted background job payload."""
        submitted_jobs.append(
            {
                "label": label,
                "func": func,
                "args": args,
                "undo_handler": undo_handler,
                "undo_args": undo_args,
                "undo_kwargs": undo_kwargs,
                "kwargs": kwargs,
            }
        )
        return "abc12345job"

    monkeypatch.setattr(upload_controller, "submit_background_job", capture_job)
    raw_csv = b"Date,Description,Amount\n2026-01-02,UNKNOWN SHOP,12.34\n"

    response = client.post(
        "/upload",
        data={
            CSRF_FIELD_NAME: set_csrf_token(client),
            "account_name": "Personal",
            "statement_type_id": str(first_statement_type_id(db_conn)),
            "statement": (io.BytesIO(raw_csv), "statement.csv"),
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )

    statement = db_conn.execute(
        """
        SELECT
            statements.id,
            statements.raw_text,
            statements.extension,
            statements.import_status,
            accounts.id AS account_id,
            accounts.name AS account_name,
            statement_types.parser_type
        FROM statements
        JOIN accounts ON accounts.id = statements.account_id
        JOIN statement_types ON statement_types.id = statements.statement_type_id
        WHERE statements.filename = 'statement.csv'
        """
    ).fetchone()
    assert response.status_code == 200
    assert b"Statement queued for background import and categorization." in response.data
    assert len(submitted_jobs) == 1
    assert statement is not None
    assert statement["raw_text"] == raw_csv.decode("utf-8")
    assert statement["extension"] == "csv"
    assert statement["import_status"] == "queued"
    assert statement["account_name"] == "Personal"
    assert statement["parser_type"] == "bank_account"

    submitted = submitted_jobs[0]
    assert submitted["label"] == "Import statement.csv"
    assert submitted["func"] is upload_controller.import_statement_transactions_job
    assert submitted["args"] == (
        statement["id"],
        statement["account_id"],
        "bank_account",
        "csv",
        raw_csv.decode("utf-8"),
    )
    assert submitted["undo_handler"] is upload_controller.undo_statement_upload_job
    assert submitted["undo_args"][0] == statement["id"]
    assert submitted["undo_args"][1] is submitted["kwargs"]["undo_state"]
    assert submitted["kwargs"]["interac_direction"] == "auto"


def test_upload_route_stores_interac_direction_override(client, db_conn, monkeypatch):
    """Verify Interac direction override is persisted and passed to the import job."""
    submitted_jobs = []

    def capture_job(label, func, *args, undo_handler=None, undo_args=None, undo_kwargs=None, **kwargs):
        """Capture the submitted background job payload."""
        submitted_jobs.append({"args": args, "kwargs": kwargs})
        return "interacjob123"

    monkeypatch.setattr(upload_controller, "submit_background_job", capture_job)
    raw_csv = b"Date,Name,Amount,Status\n2026-05-08,Alex Buyer,$125.00,Autodeposited\n"

    response = client.post(
        "/upload",
        data={
            CSRF_FIELD_NAME: set_csrf_token(client),
            "account_name": "Personal",
            "statement_type_id": str(statement_type_id(db_conn, "interac_etransfer")),
            "interac_direction": "received",
            "statement": (io.BytesIO(raw_csv), "interac.csv"),
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )

    statement = db_conn.execute(
        """
        SELECT interac_direction
        FROM statements
        WHERE filename = 'interac.csv'
        """
    ).fetchone()
    assert response.status_code == 200
    assert statement["interac_direction"] == "received"
    assert submitted_jobs[0]["args"][2] == "interac_etransfer"
    assert submitted_jobs[0]["kwargs"]["interac_direction"] == "received"


def test_import_statement_job_queues_llm_categorization_for_unknowns(app, db_conn, monkeypatch):
    """Verify that statement import queues a follow-up LLM job for unknown rows."""
    account_id, statement_id = create_account_statement(db_conn, "unknowns.csv")
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

    monkeypatch.setattr(upload_workflow, "submit_background_job", capture_job)

    message = upload_workflow.import_statement_transactions_job(
        statement_id,
        account_id,
        "credit_card",
        "csv",
        "Date,Description,Amount\n2026-01-02,UNKNOWN SHOP,12.34\n",
    )

    rows = db_conn.execute(
        """
        SELECT description, category, needs_review
        FROM transactions
        WHERE statement_id = ?
        """,
        (statement_id,),
    ).fetchall()
    assert "Queued LLM categorization for 1 unknown transaction." in message
    assert [tuple(row) for row in rows] == [("UNKNOWN SHOP", "UNKNOWN", 1)]
    statement = db_conn.execute(
        """
        SELECT import_status, imported_count, skipped_count, ignored_count,
               llm_candidate_count, import_error
        FROM statements
        WHERE id = ?
        """,
        (statement_id,),
    ).fetchone()
    assert tuple(statement) == ("completed", 1, 0, 0, 1, None)
    assert submitted_jobs == [
        {
            "label": f"LLM categorize statement {statement_id}",
            "func": upload_workflow.categorize_statement_unknown_transactions_job,
            "args": (statement_id,),
            "kwargs": {},
        }
    ]


def test_categorize_statement_unknown_transactions_job_updates_rows_and_tags(app, db_conn, monkeypatch):
    """Verify that LLM categorization results are persisted to uploaded transactions."""
    _, statement_id = create_account_statement(db_conn, "llm.csv")
    unknown_id = db_conn.execute(
        """
        INSERT INTO transactions (
            statement_id,
            tx_date,
            description,
            amount,
            category,
            needs_review,
            fingerprint
        )
        VALUES (?, '2026-01-02', 'UNKNOWN SHOP', 12.34, 'UNKNOWN', 1, 'llm-unknown')
        """,
        (statement_id,),
    ).lastrowid
    known_id = db_conn.execute(
        """
        INSERT INTO transactions (
            statement_id,
            tx_date,
            description,
            amount,
            category,
            needs_review,
            fingerprint
        )
        VALUES (?, '2026-01-03', 'KNOWN SHOP', 50.00, 'Utilities', 0, 'llm-known')
        """,
        (statement_id,),
    ).lastrowid
    ignored_id = db_conn.execute(
        """
        INSERT INTO transactions (
            statement_id,
            tx_date,
            description,
            amount,
            category,
            needs_review,
            ignored,
            fingerprint
        )
        VALUES (?, '2026-01-04', 'IGNORED SHOP', 25.00, 'UNKNOWN', 1, 1, 'llm-ignored')
        """,
        (statement_id,),
    ).lastrowid
    db_conn.commit()

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

    monkeypatch.setattr(upload_workflow, "categorize_transactions", categorize_for_test)

    message = upload_workflow.categorize_statement_unknown_transactions_job(statement_id)

    updated = db_conn.execute(
        """
        SELECT category, needs_review, category_source, category_confidence,
               category_rule_id, categorized_at, reviewed_at
        FROM transactions
        WHERE id = ?
        """,
        (unknown_id,),
    ).fetchone()
    known = db_conn.execute(
        "SELECT category FROM transactions WHERE id = ?",
        (known_id,),
    ).fetchone()
    ignored = db_conn.execute(
        "SELECT category FROM transactions WHERE id = ?",
        (ignored_id,),
    ).fetchone()
    assert message == "LLM categorized 1 transaction."
    assert tuple(updated) == (
        "Groceries",
        0,
        "ai",
        0.93,
        None,
        "2026-05-09T00:00:00Z",
        None,
    )
    assert get_transaction_tag_names(db_conn, unknown_id) == ["Tax"]
    assert known["category"] == "Utilities"
    assert ignored["category"] == "UNKNOWN"


def test_categorize_statement_unknown_transactions_job_persists_unknown_llm_metadata(app, db_conn, monkeypatch):
    """Verify metadata-only LLM unknown outcomes are persisted for existing rows."""
    del app
    _, statement_id = create_account_statement(db_conn, "llm-unknown-metadata.csv")
    unknown_id = db_conn.execute(
        """
        INSERT INTO transactions (
            statement_id,
            tx_date,
            description,
            amount,
            category,
            needs_review,
            fingerprint
        )
        VALUES (?, '2026-01-02', 'UNKNOWN SHOP', 12.34, 'UNKNOWN', 1, 'llm-unknown-metadata')
        """,
        (statement_id,),
    ).lastrowid
    db_conn.commit()

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

    monkeypatch.setattr(upload_workflow, "categorize_transactions", categorize_for_test)

    message = upload_workflow.categorize_statement_unknown_transactions_job(statement_id)

    row = db_conn.execute(
        """
        SELECT category, needs_review, category_source, category_metadata
        FROM transactions
        WHERE id = ?
        """,
        (unknown_id,),
    ).fetchone()
    metadata = json.loads(row["category_metadata"])
    assert message == "LLM categorized 0 transactions."
    assert row["category"] == "UNKNOWN"
    assert row["needs_review"] == 1
    assert row["category_source"] == "unknown"
    assert metadata["failure_reason"] == "llm_no_results"


def test_categorize_statement_unknown_transactions_job_reports_no_work(app, db_conn, monkeypatch):
    """Verify that the LLM job exits cleanly when there are no unknown rows."""
    _, statement_id = create_account_statement(db_conn, "none.csv")
    calls = []
    monkeypatch.setattr(
        upload_workflow,
        "categorize_transactions",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    message = upload_workflow.categorize_statement_unknown_transactions_job(statement_id)

    assert message == "No unknown transactions needed LLM categorization."
    assert calls == []


def test_retry_statement_import_route_queues_existing_statement(client, db_conn, monkeypatch):
    """Verify retry queues import work from stored statement text."""
    account_id, statement_id = create_account_statement(db_conn, "retry.csv")
    db_conn.execute(
        """
        UPDATE statements
        SET raw_text = ?,
            extension = 'csv',
            import_status = 'failed',
            import_error = 'Parser failed'
        WHERE id = ?
        """,
        (
            "Date,Description,Amount\n2026-01-02,RETRY SHOP,12.34\n",
            statement_id,
        ),
    )
    db_conn.commit()
    submitted_jobs = []

    def capture_job(label, func, *args, undo_handler=None, undo_args=None, **kwargs):
        """Capture retry job payload."""
        submitted_jobs.append(
            {
                "label": label,
                "func": func,
                "args": args,
                "undo_handler": undo_handler,
                "undo_args": undo_args,
                "kwargs": kwargs,
            }
        )
        return "retry-job-id"

    monkeypatch.setattr(upload_controller, "submit_background_job", capture_job)

    response = client.post(
        f"/upload/{statement_id}/retry",
        data={CSRF_FIELD_NAME: set_csrf_token(client)},
        follow_redirects=True,
    )

    statement = db_conn.execute(
        """
        SELECT import_status, import_error, imported_count
        FROM statements
        WHERE id = ?
        """,
        (statement_id,),
    ).fetchone()
    assert response.status_code == 200
    assert b"Retry queued." in response.data
    assert tuple(statement) == ("queued", None, 0)
    assert len(submitted_jobs) == 1
    assert submitted_jobs[0]["label"] == "Retry import retry.csv"
    assert submitted_jobs[0]["func"] is upload_controller.import_statement_transactions_job
    assert submitted_jobs[0]["args"] == (
        statement_id,
        account_id,
        "bank_account",
        "csv",
        "Date,Description,Amount\n2026-01-02,RETRY SHOP,12.34\n",
    )
    assert submitted_jobs[0]["undo_handler"] is upload_controller.undo_statement_upload_job
    assert submitted_jobs[0]["undo_args"][0] == statement_id
    assert submitted_jobs[0]["undo_args"][1] is submitted_jobs[0]["kwargs"]["undo_state"]
    assert submitted_jobs[0]["kwargs"]["interac_direction"] == "auto"


def test_reprocess_statement_import_route_removes_statement_transactions(client, db_conn, monkeypatch):
    """Verify reprocess clears statement transactions before queueing import work."""
    account_id, statement_id = create_account_statement(db_conn, "reprocess.csv")
    db_conn.execute(
        """
        UPDATE statements
        SET raw_text = ?,
            extension = 'csv',
            import_status = 'completed',
            imported_count = 1
        WHERE id = ?
        """,
        (
            "Date,Description,Amount\n2026-01-02,REPROCESS SHOP,12.34\n",
            statement_id,
        ),
    )
    db_conn.execute(
        """
        INSERT INTO transactions (
            statement_id,
            account_id,
            tx_date,
            description,
            amount,
            category,
            fingerprint
        )
        VALUES (?, ?, '2026-01-01', 'OLD SHOP', 5.00, 'UNKNOWN', 'reprocess-old')
        """,
        (statement_id, account_id),
    )
    db_conn.commit()
    submitted_jobs = []
    monkeypatch.setattr(
        upload_controller,
        "submit_background_job",
        lambda label, func, *args, undo_handler=None, undo_args=None, **kwargs: (
            submitted_jobs.append((label, func, args, undo_handler, undo_args, kwargs))
            or "reprocess-job-id"
        ),
    )

    response = client.post(
        f"/upload/{statement_id}/reprocess",
        data={CSRF_FIELD_NAME: set_csrf_token(client)},
        follow_redirects=True,
    )

    transaction_count = db_conn.execute(
        """
        SELECT COUNT(*) AS count
        FROM transactions
        WHERE statement_id = ?
        """,
        (statement_id,),
    ).fetchone()["count"]
    statement = db_conn.execute(
        """
        SELECT import_status, imported_count
        FROM statements
        WHERE id = ?
        """,
        (statement_id,),
    ).fetchone()
    assert response.status_code == 200
    assert b"Reprocess queued." in response.data
    assert transaction_count == 0
    assert tuple(statement) == ("queued", 0)
    assert submitted_jobs[0][0] == "Reprocess reprocess.csv"
    assert submitted_jobs[0][2] == (
        statement_id,
        account_id,
        "bank_account",
        "csv",
        "Date,Description,Amount\n2026-01-02,REPROCESS SHOP,12.34\n",
    )
    assert submitted_jobs[0][5]["interac_direction"] == "auto"


def test_undo_statement_upload_job_removes_statement_transactions_and_tags(app, db_conn):
    """Verify upload undo removes the statement and all imported transactions."""
    account_id, statement_id = create_account_statement(db_conn, "undo.csv")
    tx_id = db_conn.execute(
        """
        INSERT INTO transactions (
            statement_id,
            account_id,
            tx_date,
            description,
            amount,
            category,
            fingerprint
        )
        VALUES (?, ?, '2026-01-02', 'UNDO SHOP', 12.34, 'Food', 'undo-upload-tx')
        """,
        (statement_id, account_id),
    ).lastrowid
    tag_id = db_conn.execute(
        """
        SELECT id
        FROM tags
        WHERE name = 'Tax'
        """
    ).fetchone()["id"]
    db_conn.execute(
        """
        INSERT INTO transaction_tags (transaction_id, tag_id)
        VALUES (?, ?)
        """,
        (tx_id, tag_id),
    )
    db_conn.commit()

    message = upload_workflow.undo_statement_upload_job(statement_id)

    statement_count = db_conn.execute(
        "SELECT COUNT(*) AS count FROM statements WHERE id = ?",
        (statement_id,),
    ).fetchone()["count"]
    transaction_count = db_conn.execute(
        "SELECT COUNT(*) AS count FROM transactions WHERE statement_id = ?",
        (statement_id,),
    ).fetchone()["count"]
    tag_count = db_conn.execute(
        "SELECT COUNT(*) AS count FROM transaction_tags WHERE transaction_id = ?",
        (tx_id,),
    ).fetchone()["count"]
    assert message == "Removed statement undo.csv and 1 transaction."
    assert statement_count == 0
    assert transaction_count == 0
    assert tag_count == 0


def test_undo_statement_upload_job_handles_already_removed_statement(app, db_conn):
    """Verify upload undo returns a stable message when the statement is gone."""
    _, statement_id = create_account_statement(db_conn, "already-removed.csv")
    db_conn.execute("DELETE FROM statements WHERE id = ?", (statement_id,))
    db_conn.commit()

    assert upload_workflow.undo_statement_upload_job(statement_id) == "Statement was already removed."
