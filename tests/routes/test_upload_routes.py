"""Route tests for statement upload validation and history views."""

import io

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError as SqlAlchemyIntegrityError
from tests.support.database import (
    default_statement_type_id,
    insert_account,
    insert_statement,
    insert_transaction,
    set_owner_setting,
    statement_type_id_for_parser,
)
from tests.support.html import assert_has_element, assert_markup, assert_visible_text
from tests.support.jobs import capture_background_jobs
from tests.support.web import set_csrf_token

from finance_app.background import runner
from finance_app.core.constants import (
    ACCOUNT_TYPE_CHECKING,
    ACCOUNT_TYPE_CREDIT_CARD,
    STATEMENT_TYPE_PARSER_CREDIT_CARD,
    STATEMENT_TYPE_PARSER_INTERAC_ETRANSFER,
)
from finance_app.core.csrf import CSRF_FIELD_NAME
from finance_app.database.tables import accounts as accounts_table
from finance_app.database.tables import statements as statements_table
from finance_app.modules.upload import service as upload_service


def statement_count(conn):
    """Return the number of persisted statement rows."""
    return conn.execute(select(func.count()).select_from(statements_table)).scalar_one()


def account_settings(conn, account_id):
    """Return account reporting settings for assertions."""
    return (
        conn.execute(
            select(accounts_table.c.account_type, accounts_table.c.paid_from_account_id).where(
                accounts_table.c.id == account_id
            )
        )
        .mappings()
        .one()
    )


def test_upload_route_rejects_missing_file_without_statement_insert(owner_client, core_conn):
    """Verify that upload validation exits before creating a statement row."""
    statement_type_id = default_statement_type_id(core_conn)

    response = owner_client.post(
        "/upload",
        data={
            CSRF_FIELD_NAME: set_csrf_token(owner_client),
            "account_name": "Personal",
            "statement_type_id": str(statement_type_id),
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert_visible_text(response, "Please choose a statement file.")
    assert statement_count(core_conn) == 0


def test_upload_route_rejects_existing_account_role_mismatch(owner_client, core_conn):
    """Verify uploads cannot silently rewrite an existing account role."""
    account_id = insert_account(core_conn, "Travel card", account_type=ACCOUNT_TYPE_CREDIT_CARD)
    statement_type_id = default_statement_type_id(core_conn)

    response = owner_client.post(
        "/upload",
        data={
            CSRF_FIELD_NAME: set_csrf_token(owner_client),
            "account_name": " travel CARD ",
            "account_type": "checking",
            "statement_type_id": str(statement_type_id),
            "statement": (io.BytesIO(b"Date,Description,Amount\n2026-01-02,Cafe,4.56\n"), "travel.csv"),
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )

    account = account_settings(core_conn, account_id)

    assert response.status_code == 200
    assert_visible_text(
        response,
        'Account "travel CARD" already exists with different reporting settings. '
        "Use the existing settings or choose a different account name.",
    )
    assert account["account_type"] == "credit_card"
    assert account["paid_from_account_id"] is None
    assert statement_count(core_conn) == 0


def test_upload_route_rejects_existing_account_funding_mismatch(owner_client, core_conn):
    """Verify uploads cannot silently rewrite a credit-card funding account."""
    main_checking_id = insert_account(core_conn, "Main checking", account_type=ACCOUNT_TYPE_CHECKING)
    insert_account(core_conn, "Other checking", account_type=ACCOUNT_TYPE_CHECKING)
    card_id = insert_account(
        core_conn,
        "Travel card",
        account_type=ACCOUNT_TYPE_CREDIT_CARD,
        paid_from_account_id=main_checking_id,
    )
    statement_type_id = statement_type_id_for_parser(core_conn, STATEMENT_TYPE_PARSER_CREDIT_CARD)

    response = owner_client.post(
        "/upload",
        data={
            CSRF_FIELD_NAME: set_csrf_token(owner_client),
            "account_name": "Travel card",
            "account_type": "credit_card",
            "paid_from_account_name": "Other checking",
            "statement_type_id": str(statement_type_id),
            "statement": (io.BytesIO(b"Date,Description,Amount\n2026-01-02,Cafe,4.56\n"), "travel.csv"),
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )

    card = account_settings(core_conn, card_id)

    assert response.status_code == 200
    assert_visible_text(
        response,
        'Account "Travel card" already exists with different reporting settings. '
        "Use the existing settings or choose a different account name.",
    )
    assert card["account_type"] == "credit_card"
    assert card["paid_from_account_id"] == main_checking_id
    assert statement_count(core_conn) == 0


def test_upload_route_renders_statement_detail_modal(owner_client, core_conn):
    """Verify uploaded statement rows open processed details by double-click target."""
    paid_from_account_id = insert_account(core_conn, "Main checking", account_type=ACCOUNT_TYPE_CHECKING)
    account_id = insert_account(
        core_conn,
        "RBC Visa",
        account_type=ACCOUNT_TYPE_CREDIT_CARD,
        paid_from_account_id=paid_from_account_id,
    )
    statement_id = insert_statement(
        core_conn,
        account_id=account_id,
        statement_type_id=statement_type_id_for_parser(core_conn, STATEMENT_TYPE_PARSER_CREDIT_CARD),
        filename="visa.csv",
        checksum="statement-detail-route",
        extension="csv",
        raw_text="Date,Description,Amount\n2026-01-02,Corner store,12.34",
        import_status="completed",
        import_started_at="2026-05-11T10:00:00Z",
        import_finished_at="2026-05-11T10:00:02Z",
        imported_count=2,
        skipped_count=1,
        ignored_count=3,
        llm_candidate_count=4,
        uploaded_at="2026-05-11T09:59:59Z",
    )
    for description, tx_date, amount, fingerprint in (
        ("Corner store", "2026-01-02", 12.34, "statement-detail-1"),
        ("Cafe", "2026-01-03", 4.56, "statement-detail-2"),
    ):
        insert_transaction(
            core_conn,
            statement_id=statement_id,
            account_id=account_id,
            tx_date=tx_date,
            description=description,
            amount=amount,
            category="Food",
            fingerprint=fingerprint,
        )

    response = owner_client.get("/upload")

    assert response.status_code == 200
    assert_markup(
        response,
        f'data-row-edit-target="#statement-details-{statement_id}"',
        f'id="statement-details-{statement_id}"',
        "data-row-action",
    )
    assert_visible_text(
        response,
        "Statement details",
        "Processing summary",
        "Current statement transactions",
        "Main checking",
        "Date,Description,Amount",
    )
    assert_has_element(
        response,
        "span",
        attrs={"data-export-part": True, "data-export-header": "Import status"},
        text="completed",
    )
    assert_has_element(
        response,
        "span",
        attrs={
            "data-export-part": True,
            "data-export-header": "Added transactions",
            "data-export-value": "2",
            "data-export-text": "2",
        },
    )
    assert_has_element(
        response,
        "span",
        attrs={
            "data-export-part": True,
            "data-export-header": "Skipped rows",
            "data-export-value": "1",
            "data-export-text": "1",
        },
    )
    assert_has_element(
        response,
        "span",
        attrs={
            "data-export-part": True,
            "data-export-header": "Ignored rows",
            "data-export-value": "3",
            "data-export-text": "3",
        },
    )
    assert_has_element(
        response,
        "span",
        attrs={
            "data-export-part": True,
            "data-export-header": "AI candidates",
            "data-export-value": "4",
            "data-export-text": "4",
        },
    )
    assert_has_element(
        response,
        "span",
        attrs={
            "data-export-part": True,
            "data-export-header": "Unknown transactions",
            "data-export-value": "0",
            "data-export-text": "0",
        },
    )


def test_upload_preview_table_is_not_exportable(owner_client):
    """Verify transient upload previews do not get CSV or Excel export controls."""
    response = owner_client.get("/upload")

    assert response.status_code == 200
    assert_has_element(
        response,
        "table",
        attrs={"data-upload-preview-table": True, "data-no-export": True},
    )


def test_upload_route_renders_interac_import_guidance(owner_client, core_conn):
    """Verify Interac uploads explain ordering and skipped or ignored rows."""
    account_id = insert_account(core_conn, "TD Interac Sent", account_type=ACCOUNT_TYPE_CHECKING)
    insert_statement(
        core_conn,
        account_id=account_id,
        statement_type_id=statement_type_id_for_parser(core_conn, STATEMENT_TYPE_PARSER_INTERAC_ETRANSFER),
        filename="interac-sent.csv",
        checksum="interac-guidance-route",
        extension="csv",
        raw_text="Date Sent,Recipient,Amount,Method,Status",
        import_status="completed",
        imported_count=29,
        skipped_count=1,
        ignored_count=76,
        uploaded_at="2026-05-14T17:41:24Z",
    )

    response = owner_client.get("/upload")

    assert response.status_code == 200
    assert_visible_text(
        response,
        "Import matching checking statements first",
        "Interac history only enriches existing checking rows.",
        "skipped rows are ambiguous matches",
        "no matching checking transaction yet",
    )


def test_estimate_categorize_statement_unknowns_returns_json(owner_client, core_conn, monkeypatch):
    """Verify the statement AI estimate route returns JSON."""
    statement_id = insert_statement(
        core_conn,
        statement_type_id=default_statement_type_id(core_conn),
        filename="statement.csv",
        checksum="statement-estimate-route",
        extension="csv",
        raw_text="Date,Description,Amount",
        import_status="completed",
        uploaded_at="2026-05-11T09:59:59Z",
    )

    monkeypatch.setattr(upload_service.upload_workflow, "count_statement_unknown_transactions", lambda conn, sid: 2)
    monkeypatch.setattr(
        upload_service,
        "estimate_statement_llm_categorization",
        lambda sid: {
            "ok": True,
            "message": "AI usage estimate ready.",
            "estimate": {"request_count": 2, "input_tokens": 222},
        },
    )

    response = owner_client.post(
        f"/upload/{statement_id}/categorize-unknowns/estimate",
        data={CSRF_FIELD_NAME: set_csrf_token(owner_client)},
    )

    assert response.status_code == 200
    assert response.get_json()["estimate"]["request_count"] == 2
    assert response.get_json()["message"] == "AI usage estimate ready."


def test_categorize_statement_unknowns_requires_token_estimate_confirmation(owner_client, core_conn, monkeypatch):
    """Verify statement AI categorization does not queue without confirmation."""
    statement_id = insert_statement(
        core_conn,
        statement_type_id=default_statement_type_id(core_conn),
        filename="statement-ai-unconfirmed.csv",
        checksum="statement-ai-unconfirmed",
        extension="csv",
        raw_text="Date,Description,Amount",
        import_status="completed",
        uploaded_at="2026-05-11T09:59:59Z",
    )
    insert_transaction(
        core_conn,
        statement_id=statement_id,
        tx_date="2026-01-02",
        description="UNKNOWN SHOP",
        amount=12.34,
        category="UNKNOWN",
        needs_review=1,
        fingerprint="statement-ai-unconfirmed-tx",
    )
    submitted = []

    def queue_for_test(queued_statement_id):
        """Capture accidental statement AI queue requests."""
        submitted.append(queued_statement_id)
        return "statementaijob123"

    monkeypatch.setattr(upload_service.upload_workflow, "queue_statement_llm_categorization", queue_for_test)

    response = owner_client.post(
        f"/upload/{statement_id}/categorize-unknowns",
        data={CSRF_FIELD_NAME: set_csrf_token(owner_client), "next": "/upload"},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert submitted == []
    assert_visible_text(response, "Review the estimated AI usage before continuing.")


def test_categorize_statement_unknowns_runs_without_confirmation_when_setting_disabled(
    owner_client,
    core_conn,
    monkeypatch,
):
    """Verify statement AI can queue without modal confirmation when the setting is off."""
    statement_id = insert_statement(
        core_conn,
        statement_type_id=default_statement_type_id(core_conn),
        filename="statement-ai-confirm-disabled.csv",
        checksum="statement-ai-confirm-disabled",
        extension="csv",
        raw_text="Date,Description,Amount",
        import_status="completed",
        uploaded_at="2026-05-11T09:59:59Z",
    )
    insert_transaction(
        core_conn,
        statement_id=statement_id,
        tx_date="2026-01-02",
        description="UNKNOWN SHOP",
        amount=12.34,
        category="UNKNOWN",
        needs_review=1,
        fingerprint="statement-ai-confirm-disabled-tx",
    )
    set_owner_setting(core_conn, "confirm_ai_token_usage_enabled", "0")
    submitted = []

    def queue_for_test(queued_statement_id):
        """Capture the statement AI queue request."""
        submitted.append(queued_statement_id)
        return "statementaijob123"

    monkeypatch.setattr(upload_service.upload_workflow, "queue_statement_llm_categorization", queue_for_test)

    response = owner_client.post(
        f"/upload/{statement_id}/categorize-unknowns",
        data={CSRF_FIELD_NAME: set_csrf_token(owner_client), "next": "/upload"},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert submitted == [statement_id]
    assert_visible_text(response, "AI categorization queued for 1 unknown transaction.")


def test_categorize_statement_unknowns_handles_queue_rejection(owner_client, core_conn, monkeypatch):
    """Verify statement AI queue rejection returns a normal route message."""
    statement_id = insert_statement(
        core_conn,
        statement_type_id=default_statement_type_id(core_conn),
        filename="statement-ai-rejected.csv",
        checksum="statement-ai-rejected",
        extension="csv",
        raw_text="Date,Description,Amount",
        import_status="completed",
        uploaded_at="2026-05-11T09:59:59Z",
    )
    insert_transaction(
        core_conn,
        statement_id=statement_id,
        tx_date="2026-01-02",
        description="UNKNOWN SHOP",
        amount=12.34,
        category="UNKNOWN",
        needs_review=1,
        fingerprint="statement-ai-rejected-tx",
    )

    def reject_queue(queued_statement_id):
        """Reject the statement AI queue request."""
        assert queued_statement_id == statement_id
        raise runner.BackgroundJobSubmissionError(
            "rejected-statement-ai",
            "AI categorize statement",
            runner.AI_JOB_QUEUE,
            "RuntimeError: executor stopped",
        )

    monkeypatch.setattr(upload_service.upload_workflow, "queue_statement_llm_categorization", reject_queue)

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
    assert_visible_text(response, "AI categorization could not be queued. Try again.")


def test_upload_route_rejects_duplicate_statement_checksum(owner_client, core_conn, monkeypatch):
    """Verify that duplicate uploads are rejected before queueing background work."""
    statement_type_id = default_statement_type_id(core_conn)
    insert_statement(
        core_conn,
        statement_type_id=statement_type_id,
        filename="already.csv",
        checksum="known-checksum",
        raw_text="Date,Description,Amount",
        uploaded_at="2026-05-11T12:00:00Z",
    )
    monkeypatch.setattr("finance_app.modules.upload.service.file_checksum", lambda uploaded_file: "known-checksum")

    response = owner_client.post(
        "/upload",
        data={
            CSRF_FIELD_NAME: set_csrf_token(owner_client),
            "account_name": "Personal",
            "statement_type_id": str(statement_type_id),
            "statement": (io.BytesIO(b"Date,Description,Amount\n"), "duplicate.csv"),
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert_visible_text(response, "This statement was already uploaded as already.csv on 2026-05-11T12:00:00Z")
    assert statement_count(core_conn) == 1


def test_upload_route_handles_racing_duplicate_statement_checksum(owner_client, core_conn, monkeypatch):
    """Verify checksum insert conflicts return the controlled duplicate-upload outcome."""
    statement_type_id = default_statement_type_id(core_conn)
    monkeypatch.setattr("finance_app.modules.upload.service.file_checksum", lambda uploaded_file: "race-checksum")
    lookups = {"count": 0}
    submitted_jobs = capture_background_jobs(monkeypatch, upload_service)

    def racing_statement_lookup(conn, checksum):
        """Miss the pre-check, then find the row inserted by another request."""
        del conn
        assert checksum == "race-checksum"
        lookups["count"] += 1
        if lookups["count"] == 1:
            return None
        return {
            "filename": "race.csv",
            "uploaded_at": "2026-05-11T12:00:00Z",
            "import_status": "queued",
        }

    def duplicate_statement_insert(*args, **kwargs):
        """Simulate the database checksum constraint losing a concurrent insert race."""
        del args, kwargs
        raise SqlAlchemyIntegrityError("duplicate checksum", {}, Exception("unique checksum"))

    monkeypatch.setattr(upload_service, "statement_by_checksum", racing_statement_lookup)
    monkeypatch.setattr(upload_service, "create_uploaded_statement", duplicate_statement_insert)

    response = owner_client.post(
        "/upload",
        data={
            CSRF_FIELD_NAME: set_csrf_token(owner_client),
            "account_name": "Race duplicate account",
            "account_type": "credit_card",
            "paid_from_account_name": "Race duplicate checking",
            "statement_type_id": str(statement_type_id),
            "statement": (io.BytesIO(b"Date,Description,Amount\n"), "duplicate.csv"),
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    account_count = core_conn.execute(
        select(func.count())
        .select_from(accounts_table)
        .where(accounts_table.c.name.in_(["Race duplicate account", "Race duplicate checking"]))
    ).scalar_one()

    assert response.status_code == 200
    assert lookups["count"] == 2
    assert len(submitted_jobs) == 0
    assert account_count == 0
    assert_visible_text(response, "This statement was already uploaded as race.csv on 2026-05-11T12:00:00Z")
