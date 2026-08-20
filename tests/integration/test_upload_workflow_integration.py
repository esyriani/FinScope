"""Integration tests for upload background workflow handoffs."""

import io

from sqlalchemy import text
from tests.support.html import assert_visible_text
from tests.support.upload import create_account_statement, first_statement_type_id, statement_type_id
from tests.support.web import set_csrf_token

from finance_app.core.csrf import CSRF_FIELD_NAME
from finance_app.modules.upload import service as upload_service
from finance_app.modules.upload import workflow as upload_workflow


def test_upload_route_submits_background_import_job(owner_client, core_conn, monkeypatch):
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

    monkeypatch.setattr(upload_service, "submit_background_job", capture_job)
    raw_csv = b"Date,Description,Amount\n2026-01-02,UNKNOWN SHOP,12.34\n"

    response = owner_client.post(
        "/upload",
        data={
            CSRF_FIELD_NAME: set_csrf_token(owner_client),
            "account_name": "Personal",
            "statement_type_id": str(first_statement_type_id(core_conn)),
            "statement": (io.BytesIO(raw_csv), "statement.csv"),
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )

    statement = core_conn.execute(text("""
        SELECT
            statements.id,
            statements.raw_text,
            statements.extension,
            statements.date_order,
            statements.import_status,
            statements.import_token,
            accounts.id AS account_id,
            accounts.name AS account_name,
            statement_types.parser_type
        FROM statements
        JOIN accounts ON accounts.id = statements.account_id
        JOIN statement_types ON statement_types.id = statements.statement_type_id
        WHERE statements.filename = 'statement.csv'
    """)).fetchone()
    assert response.status_code == 200
    assert_visible_text(response, "Statement queued for background import and categorization.")
    assert len(submitted_jobs) == 1
    assert statement is not None
    assert statement._mapping["raw_text"] == raw_csv.decode("utf-8")
    assert statement._mapping["extension"] == "csv"
    assert statement._mapping["date_order"] == "auto"
    assert statement._mapping["import_status"] == "queued"
    assert statement._mapping["account_name"] == "Personal"
    assert statement._mapping["parser_type"] == "bank_account"

    submitted = submitted_jobs[0]
    assert submitted["label"] == "Import statement.csv"
    assert submitted["func"] is upload_workflow.import_statement_transactions_job
    assert submitted["args"] == (
        statement._mapping["id"],
        statement._mapping["account_id"],
        "bank_account",
        "csv",
        raw_csv.decode("utf-8"),
        statement._mapping["import_token"],
    )
    assert submitted["undo_handler"] is upload_workflow.undo_statement_upload_job
    assert submitted["undo_args"][0] == statement._mapping["id"]
    assert submitted["undo_args"][1] is submitted["kwargs"]["undo_state"]
    assert submitted["kwargs"]["interac_direction"] == "auto"
    assert submitted["kwargs"]["date_order"] == "auto"


def test_upload_preview_detects_month_first_slash_dates(owner_client, core_conn):
    """Verify the preview parses unambiguous month-first CSV dates correctly."""
    raw_csv = b"05/18/2026,DISNEY PLUS,9.19,,4463.99\n" b"05/12/2026,AMZN Mktp CA*PF2WC4HM3,134.56,,3922.64\n"

    response = owner_client.post(
        "/upload/preview",
        data={
            CSRF_FIELD_NAME: set_csrf_token(owner_client),
            "account_name": "Personal",
            "statement_type_id": str(first_statement_type_id(core_conn)),
            "statement": (io.BytesIO(raw_csv), "preview-month-first.csv"),
        },
        content_type="multipart/form-data",
    )

    data = response.get_json()
    assert response.status_code == 200
    assert data["ok"] is True
    assert data["preview"]["date_format"]["effective_order"] == "month_first"
    assert data["preview"]["date_format"]["requires_choice"] is False
    assert data["preview"]["preview_rows"][0]["raw_date"] == "05/12/2026"
    assert data["preview"]["preview_rows"][0]["parsed_date"] == "2026-05-12"
    assert data["preview"]["date_range"] == {
        "earliest": "2026-05-12",
        "latest": "2026-05-18",
    }


def test_upload_preview_requires_choice_for_ambiguous_slash_dates(owner_client, core_conn):
    """Verify ambiguous slash-only statements ask for an explicit date order."""
    raw_csv = b"05/12/2026,AMZN Mktp CA*PF2WC4HM3,134.56,,3922.64\n"

    response = owner_client.post(
        "/upload/preview",
        data={
            CSRF_FIELD_NAME: set_csrf_token(owner_client),
            "account_name": "Personal",
            "statement_type_id": str(first_statement_type_id(core_conn)),
            "statement": (io.BytesIO(raw_csv), "preview-ambiguous.csv"),
        },
        content_type="multipart/form-data",
    )

    preview = response.get_json()["preview"]
    assert response.status_code == 200
    assert preview["date_format"]["effective_order"] == ""
    assert preview["date_format"]["requires_choice"] is True
    assert preview["preview_rows"][0]["month_first_date"] == "2026-05-12"
    assert preview["preview_rows"][0]["day_first_date"] == "2026-12-05"
    assert preview["date_range"] == {"earliest": "", "latest": ""}
    assert preview["date_ranges"]["month_first"] == {
        "earliest": "2026-05-12",
        "latest": "2026-05-12",
    }
    assert preview["date_ranges"]["day_first"] == {
        "earliest": "2026-12-05",
        "latest": "2026-12-05",
    }


def test_upload_preview_prioritizes_ambiguous_date_samples(owner_client, core_conn):
    """Verify preview samples show ambiguous date rows when available."""
    clear_rows = "\n".join(f"12/{day}/2025,CLEAR SAMPLE {day},1.00,," for day in range(13, 25))
    raw_csv = f"{clear_rows}\n05/12/2026,AMBIGUOUS SAMPLE,2.00,,\n".encode()

    response = owner_client.post(
        "/upload/preview",
        data={
            CSRF_FIELD_NAME: set_csrf_token(owner_client),
            "account_name": "Personal",
            "statement_type_id": str(first_statement_type_id(core_conn)),
            "statement": (io.BytesIO(raw_csv), "preview-ambiguous-sample.csv"),
        },
        content_type="multipart/form-data",
    )

    preview = response.get_json()["preview"]
    assert response.status_code == 200
    assert preview["date_format"]["effective_order"] == "month_first"
    assert preview["date_format"]["source"] == "detected"
    assert preview["date_format"]["requires_choice"] is False
    assert preview["preview_rows"][0]["raw_date"] == "05/12/2026"
    assert preview["preview_rows"][0]["month_first_date"] == "2026-05-12"
    assert preview["preview_rows"][0]["day_first_date"] == "2026-12-05"


def test_upload_route_requires_date_order_for_ambiguous_slash_dates(owner_client, core_conn, monkeypatch):
    """Verify final uploads cannot bypass date-order confirmation."""
    monkeypatch.setattr(
        upload_service,
        "submit_background_job",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("Ambiguous upload should not queue")),
    )

    response = owner_client.post(
        "/upload",
        data={
            CSRF_FIELD_NAME: set_csrf_token(owner_client),
            "account_name": "Personal",
            "statement_type_id": str(first_statement_type_id(core_conn)),
            "statement": (
                io.BytesIO(b"05/12/2026,AMZN Mktp CA*PF2WC4HM3,134.56,,3922.64\n"),
                "ambiguous.csv",
            ),
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )

    statement_count = core_conn.execute(text("""
        SELECT COUNT(*) AS count
        FROM statements
        WHERE filename = 'ambiguous.csv'
        """)).fetchone()._mapping["count"]
    assert response.status_code == 200
    assert_visible_text(response, "Choose a statement date format before uploading.")
    assert statement_count == 0


def test_upload_route_stores_date_order_override(owner_client, core_conn, monkeypatch):
    """Verify confirmed date-order choices are persisted and passed to import jobs."""
    submitted_jobs = []

    def capture_job(label, func, *args, undo_handler=None, undo_args=None, undo_kwargs=None, **kwargs):
        """Capture the submitted background job payload."""
        submitted_jobs.append({"label": label, "func": func, "args": args, "kwargs": kwargs})
        return "dateorderjob123"

    monkeypatch.setattr(upload_service, "submit_background_job", capture_job)

    response = owner_client.post(
        "/upload",
        data={
            CSRF_FIELD_NAME: set_csrf_token(owner_client),
            "account_name": "Personal",
            "statement_type_id": str(first_statement_type_id(core_conn)),
            "date_order": "month_first",
            "statement": (
                io.BytesIO(b"05/12/2026,AMZN Mktp CA*PF2WC4HM3,134.56,,3922.64\n"),
                "date-order.csv",
            ),
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )

    statement = core_conn.execute(text("""
        SELECT date_order
        FROM statements
        WHERE filename = 'date-order.csv'
        """)).fetchone()
    assert response.status_code == 200
    assert statement._mapping["date_order"] == "month_first"
    assert submitted_jobs[0]["kwargs"]["date_order"] == "month_first"


def test_upload_route_rejects_pdf_files(owner_client, core_conn, monkeypatch):
    """Verify statement uploads reject PDF files before creating a statement."""
    monkeypatch.setattr(
        upload_service,
        "submit_background_job",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("PDF should not queue")),
    )

    response = owner_client.post(
        "/upload",
        data={
            CSRF_FIELD_NAME: set_csrf_token(owner_client),
            "account_name": "Personal",
            "statement_type_id": str(first_statement_type_id(core_conn)),
            "statement": (io.BytesIO(b"%PDF-1.4"), "statement.pdf"),
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )

    statement_count = core_conn.execute(text("""
        SELECT COUNT(*) AS count
        FROM statements
        WHERE filename = 'statement.pdf'
        """)).fetchone()._mapping["count"]

    assert response.status_code == 200
    assert_visible_text(response, "Only CSV files are supported.")
    assert statement_count == 0


def test_upload_route_stores_interac_direction_override(owner_client, core_conn, monkeypatch):
    """Verify Interac direction override is persisted and passed to the import job."""
    submitted_jobs = []

    def capture_job(label, func, *args, undo_handler=None, undo_args=None, undo_kwargs=None, **kwargs):
        """Capture the submitted background job payload."""
        submitted_jobs.append({"args": args, "kwargs": kwargs})
        return "interacjob123"

    monkeypatch.setattr(upload_service, "submit_background_job", capture_job)
    raw_csv = b"Date,Name,Amount,Status\n2026-05-08,Alex Buyer,$125.00,Autodeposited\n"

    response = owner_client.post(
        "/upload",
        data={
            CSRF_FIELD_NAME: set_csrf_token(owner_client),
            "account_name": "Personal",
            "statement_type_id": str(statement_type_id(core_conn, "interac_etransfer")),
            "interac_direction": "received",
            "statement": (io.BytesIO(raw_csv), "interac.csv"),
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )

    statement = core_conn.execute(text("""
        SELECT interac_direction
        FROM statements
        WHERE filename = 'interac.csv'
        """)).fetchone()
    assert response.status_code == 200
    assert statement._mapping["interac_direction"] == "received"
    assert submitted_jobs[0]["args"][2] == "interac_etransfer"
    assert submitted_jobs[0]["kwargs"]["interac_direction"] == "received"


def test_retry_statement_import_route_queues_existing_statement(owner_client, core_conn, monkeypatch):
    """Verify retry queues import work from stored statement text."""
    account_id, statement_id = create_account_statement(core_conn, "retry.csv")
    core_conn.execute(
        text("""
        UPDATE statements
        SET raw_text = :p0,
            extension = 'csv',
            import_status = 'failed',
            import_error = 'Parser failed'
        WHERE id = :p1
        """),
        {"p0": "Date,Description,Amount\n2026-01-02,RETRY SHOP,12.34\n", "p1": statement_id},
    )
    core_conn.commit()
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

    monkeypatch.setattr(upload_service, "submit_background_job", capture_job)

    response = owner_client.post(
        f"/upload/{statement_id}/retry",
        data={CSRF_FIELD_NAME: set_csrf_token(owner_client)},
        follow_redirects=True,
    )

    statement = core_conn.execute(
        text("""
        SELECT import_status, import_error, imported_count, import_token
        FROM statements
        WHERE id = :p0
        """),
        {"p0": statement_id},
    ).fetchone()
    assert response.status_code == 200
    assert_visible_text(response, "Retry queued.")
    assert tuple(statement)[:3] == ("queued", None, 0)
    assert len(submitted_jobs) == 1
    assert submitted_jobs[0]["label"] == "Retry import retry.csv"
    assert submitted_jobs[0]["func"] is upload_workflow.import_statement_transactions_job
    assert submitted_jobs[0]["args"] == (
        statement_id,
        account_id,
        "bank_account",
        "csv",
        "Date,Description,Amount\n2026-01-02,RETRY SHOP,12.34\n",
        statement._mapping["import_token"],
    )
    assert submitted_jobs[0]["undo_handler"] is upload_workflow.undo_statement_upload_job
    assert submitted_jobs[0]["undo_args"][0] == statement_id
    assert submitted_jobs[0]["undo_args"][1] is submitted_jobs[0]["kwargs"]["undo_state"]
    assert submitted_jobs[0]["kwargs"]["interac_direction"] == "auto"
    assert submitted_jobs[0]["kwargs"]["date_order"] == "auto"


def test_reprocess_statement_import_route_removes_statement_transactions(owner_client, core_conn, monkeypatch):
    """Verify reprocess clears statement transactions before queueing import work."""
    account_id, statement_id = create_account_statement(core_conn, "reprocess.csv")
    core_conn.execute(
        text("""
        UPDATE statements
        SET raw_text = :p0,
            extension = 'csv',
            import_status = 'completed',
            imported_count = 1
        WHERE id = :p1
        """),
        {"p0": "Date,Description,Amount\n2026-01-02,REPROCESS SHOP,12.34\n", "p1": statement_id},
    )
    core_conn.execute(
        text("""
        INSERT INTO transactions (
            statement_id,
            account_id,
            tx_date,
            description,
            amount,
            category,
            fingerprint
        )
        VALUES (:p0, :p1, '2026-01-01', 'REPLACED SHOP', 5.00, 'UNKNOWN', 'reprocess-existing')
        """),
        {"p0": statement_id, "p1": account_id},
    )
    core_conn.commit()
    submitted_jobs = []
    monkeypatch.setattr(
        upload_service,
        "submit_background_job",
        lambda label, func, *args, undo_handler=None, undo_args=None, **kwargs: (
            submitted_jobs.append((label, func, args, undo_handler, undo_args, kwargs)) or "reprocess-job-id"
        ),
    )

    response = owner_client.post(
        f"/upload/{statement_id}/reprocess",
        data={CSRF_FIELD_NAME: set_csrf_token(owner_client)},
        follow_redirects=True,
    )

    transaction_count = (
        core_conn.execute(
            text("""
        SELECT COUNT(*) AS count
        FROM transactions
        WHERE statement_id = :p0
        """),
            {"p0": statement_id},
        )
        .fetchone()
        ._mapping["count"]
    )
    statement = core_conn.execute(
        text("""
        SELECT import_status, imported_count, import_token
        FROM statements
        WHERE id = :p0
        """),
        {"p0": statement_id},
    ).fetchone()
    assert response.status_code == 200
    assert_visible_text(response, "Reprocess queued.")
    assert transaction_count == 0
    assert tuple(statement)[:2] == ("queued", 0)
    assert submitted_jobs[0][0] == "Reprocess reprocess.csv"
    assert submitted_jobs[0][2] == (
        statement_id,
        account_id,
        "bank_account",
        "csv",
        "Date,Description,Amount\n2026-01-02,REPROCESS SHOP,12.34\n",
        statement._mapping["import_token"],
    )
    assert submitted_jobs[0][5]["interac_direction"] == "auto"
    assert submitted_jobs[0][5]["date_order"] == "auto"


def test_undo_statement_upload_job_removes_statement_transactions_and_tags(app, core_conn):
    """Verify upload undo removes the statement and all imported transactions."""
    account_id, statement_id = create_account_statement(core_conn, "undo.csv")
    tx_id = core_conn.execute(
        text("""
        INSERT INTO transactions (
            statement_id,
            account_id,
            tx_date,
            description,
            amount,
            category,
            fingerprint
        )
        VALUES (:p0, :p1, '2026-01-02', 'UNDO SHOP', 12.34, 'Food', 'undo-upload-tx')
        """),
        {"p0": statement_id, "p1": account_id},
    ).lastrowid
    tag_id = core_conn.execute(text("""
        SELECT id
        FROM tags
        WHERE name = 'Tax'
        """)).fetchone()._mapping["id"]
    core_conn.execute(
        text("""
        INSERT INTO transaction_tags (transaction_id, tag_id)
        VALUES (:p0, :p1)
        """),
        {"p0": tx_id, "p1": tag_id},
    )
    core_conn.commit()

    message = upload_workflow.undo_statement_upload_job(statement_id)

    statement_count = (
        core_conn.execute(text("SELECT COUNT(*) AS count FROM statements WHERE id = :p0"), {"p0": statement_id})
        .fetchone()
        ._mapping["count"]
    )
    transaction_count = (
        core_conn.execute(
            text("SELECT COUNT(*) AS count FROM transactions WHERE statement_id = :p0"), {"p0": statement_id}
        )
        .fetchone()
        ._mapping["count"]
    )
    tag_count = (
        core_conn.execute(
            text("SELECT COUNT(*) AS count FROM transaction_tags WHERE transaction_id = :p0"), {"p0": tx_id}
        )
        .fetchone()
        ._mapping["count"]
    )
    assert message == "Removed statement undo.csv and 1 transaction."
    assert statement_count == 0
    assert transaction_count == 0
    assert tag_count == 0


def test_undo_statement_upload_job_handles_already_removed_statement(app, core_conn):
    """Verify upload undo returns a stable message when the statement is gone."""
    _, statement_id = create_account_statement(core_conn, "already-removed.csv")
    core_conn.execute(text("DELETE FROM statements WHERE id = :p0"), {"p0": statement_id})
    core_conn.commit()

    assert upload_workflow.undo_statement_upload_job(statement_id) == "Statement was already removed."
