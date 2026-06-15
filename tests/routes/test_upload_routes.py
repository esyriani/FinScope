"""Route tests for statement upload validation and history views."""

import io

from sqlalchemy import text
from tests.support.database import set_owner_setting
from tests.support.html import assert_has_element, assert_markup, assert_visible_text
from tests.support.web import set_csrf_token

from finance_app.core.csrf import CSRF_FIELD_NAME


def test_upload_route_rejects_missing_file_without_statement_insert(client, core_conn):
    """Verify that upload validation exits before creating a statement row."""
    statement_type_id = core_conn.execute(text("""
        SELECT id
        FROM statement_types
        WHERE active = 1
        ORDER BY id
        LIMIT 1
        """)).fetchone()._mapping["id"]

    response = client.post(
        "/upload",
        data={
            CSRF_FIELD_NAME: set_csrf_token(client),
            "account_name": "Personal",
            "statement_type_id": str(statement_type_id),
        },
        follow_redirects=True,
    )

    statement_count = core_conn.execute(text("SELECT COUNT(*) AS count FROM statements")).fetchone()._mapping["count"]
    assert response.status_code == 200
    assert_visible_text(response, "Please choose a statement file.")
    assert statement_count == 0


def test_upload_route_renders_statement_detail_modal(client, core_conn):
    """Verify uploaded statement rows open processed details by double-click target."""
    paid_from_account_id = core_conn.execute(text("""
        INSERT INTO accounts (name, account_type)
        VALUES ('Main checking', 'checking')
        """)).lastrowid
    account_id = core_conn.execute(
        text("""
        INSERT INTO accounts (name, account_type, paid_from_account_id)
        VALUES ('RBC Visa', 'credit_card', :p0)
        """),
        {"p0": paid_from_account_id},
    ).lastrowid
    statement_type_id = core_conn.execute(text("""
        SELECT id
        FROM statement_types
        WHERE parser_type = 'credit_card'
        LIMIT 1
        """)).fetchone()._mapping["id"]
    statement_id = core_conn.execute(
        text("""
        INSERT INTO statements (
            account_id,
            statement_type_id,
            filename,
            checksum,
            extension,
            raw_text,
            import_status,
            import_started_at,
            import_finished_at,
            imported_count,
            skipped_count,
            ignored_count,
            llm_candidate_count,
            uploaded_at
        )
        VALUES (
            :p0, :p1, 'visa.csv', 'statement-detail-route',
            'csv', 'Date,Description,Amount\n2026-01-02,Corner store,12.34',
            'completed', '2026-05-11T10:00:00Z', '2026-05-11T10:00:02Z',
            2, 1, 3, 4, '2026-05-11T09:59:59Z'
        )
        """),
        {"p0": account_id, "p1": statement_type_id},
    ).lastrowid
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
        VALUES (:p0, :p1, :p2, :p3, :p4, 'Food', :p5)
        """),
        [
            {
                "p0": statement_id,
                "p1": account_id,
                "p2": "2026-01-02",
                "p3": "Corner store",
                "p4": 12.34,
                "p5": "statement-detail-1",
            },
            {
                "p0": statement_id,
                "p1": account_id,
                "p2": "2026-01-03",
                "p3": "Cafe",
                "p4": 4.56,
                "p5": "statement-detail-2",
            },
        ],
    )
    core_conn.commit()

    response = client.get("/upload")

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


def test_upload_route_renders_interac_import_guidance(client, core_conn):
    """Verify Interac uploads explain ordering and skipped or ignored rows."""
    account_id = core_conn.execute(text("""
        INSERT INTO accounts (name, account_type)
        VALUES ('TD Interac Sent', 'checking')
        """)).lastrowid
    statement_type_id = core_conn.execute(text("""
        SELECT id
        FROM statement_types
        WHERE parser_type = 'interac_etransfer'
        LIMIT 1
        """)).fetchone()._mapping["id"]
    core_conn.execute(
        text("""
        INSERT INTO statements (
            account_id,
            statement_type_id,
            filename,
            checksum,
            extension,
            raw_text,
            import_status,
            imported_count,
            skipped_count,
            ignored_count,
            uploaded_at
        )
        VALUES (
            :p0, :p1, 'interac-sent.csv', 'interac-guidance-route',
            'csv', 'Date Sent,Recipient,Amount,Method,Status',
            'completed', 29, 1, 76, '2026-05-14T17:41:24Z'
        )
        """),
        {"p0": account_id, "p1": statement_type_id},
    )
    core_conn.commit()

    response = client.get("/upload")

    assert response.status_code == 200
    assert_visible_text(
        response,
        "Import matching checking statements first",
        "Interac history only enriches existing checking rows.",
        "skipped rows are ambiguous matches",
        "no matching checking transaction yet",
    )


def test_estimate_categorize_statement_unknowns_returns_json(client, core_conn, monkeypatch):
    """Verify the statement AI estimate route returns JSON."""
    statement_type_id = core_conn.execute(text("""
        SELECT id
        FROM statement_types
        WHERE active = 1
        ORDER BY id
        LIMIT 1
        """)).fetchone()._mapping["id"]
    statement_id = core_conn.execute(
        text("""
        INSERT INTO statements (
            statement_type_id,
            filename,
            checksum,
            extension,
            raw_text,
            import_status,
            uploaded_at
        )
        VALUES (
            :p0,
            'statement.csv',
            'statement-estimate-route',
            'csv',
            'Date,Description,Amount',
            'completed',
            '2026-05-11T09:59:59Z'
        )
        """),
        {"p0": statement_type_id},
    ).lastrowid
    core_conn.commit()

    from finance_app.modules.upload import controller as upload_controller

    monkeypatch.setattr(upload_controller.upload_workflow, "count_statement_unknown_transactions", lambda conn, sid: 2)
    monkeypatch.setattr(
        upload_controller,
        "estimate_statement_llm_categorization",
        lambda sid: {
            "ok": True,
            "message": "AI token estimate ready.",
            "estimate": {"request_count": 2, "input_tokens": 222},
        },
    )

    response = client.post(
        f"/upload/{statement_id}/categorize-unknowns/estimate",
        data={CSRF_FIELD_NAME: set_csrf_token(client)},
    )

    assert response.status_code == 200
    assert response.get_json()["estimate"]["request_count"] == 2
    assert response.get_json()["message"] == "AI token estimate ready."


def test_categorize_statement_unknowns_requires_token_estimate_confirmation(client, core_conn, monkeypatch):
    """Verify statement AI categorization does not queue without confirmation."""
    statement_type_id = core_conn.execute(text("""
        SELECT id
        FROM statement_types
        WHERE active = 1
        ORDER BY id
        LIMIT 1
        """)).fetchone()._mapping["id"]
    statement_id = core_conn.execute(
        text("""
        INSERT INTO statements (
            statement_type_id,
            filename,
            checksum,
            extension,
            raw_text,
            import_status,
            uploaded_at
        )
        VALUES (
            :p0,
            'statement-ai-unconfirmed.csv',
            'statement-ai-unconfirmed',
            'csv',
            'Date,Description,Amount',
            'completed',
            '2026-05-11T09:59:59Z'
        )
        """),
        {"p0": statement_type_id},
    ).lastrowid
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
        VALUES (:p0, '2026-01-02', 'UNKNOWN SHOP', 12.34, 'UNKNOWN', 1, 'statement-ai-unconfirmed-tx')
        """),
        {"p0": statement_id},
    )
    core_conn.commit()
    submitted = []

    from finance_app.modules.upload import controller as upload_controller

    def queue_for_test(queued_statement_id):
        """Capture accidental statement AI queue requests."""
        submitted.append(queued_statement_id)
        return "statementaijob123"

    monkeypatch.setattr(upload_controller.upload_workflow, "queue_statement_llm_categorization", queue_for_test)

    response = client.post(
        f"/upload/{statement_id}/categorize-unknowns",
        data={CSRF_FIELD_NAME: set_csrf_token(client), "next": "/upload"},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert submitted == []
    assert_visible_text(response, "Review the token estimate before running AI.")


def test_categorize_statement_unknowns_runs_without_confirmation_when_setting_disabled(
    client,
    core_conn,
    monkeypatch,
):
    """Verify statement AI can queue without modal confirmation when the setting is off."""
    statement_type_id = core_conn.execute(text("""
        SELECT id
        FROM statement_types
        WHERE active = 1
        ORDER BY id
        LIMIT 1
        """)).fetchone()._mapping["id"]
    statement_id = core_conn.execute(
        text("""
        INSERT INTO statements (
            statement_type_id,
            filename,
            checksum,
            extension,
            raw_text,
            import_status,
            uploaded_at
        )
        VALUES (
            :p0,
            'statement-ai-confirm-disabled.csv',
            'statement-ai-confirm-disabled',
            'csv',
            'Date,Description,Amount',
            'completed',
            '2026-05-11T09:59:59Z'
        )
        """),
        {"p0": statement_type_id},
    ).lastrowid
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
        VALUES (:p0, '2026-01-02', 'UNKNOWN SHOP', 12.34, 'UNKNOWN', 1, 'statement-ai-confirm-disabled-tx')
        """),
        {"p0": statement_id},
    )
    core_conn.commit()
    set_owner_setting(core_conn, "confirm_ai_token_usage_enabled", "0")
    submitted = []

    from finance_app.modules.upload import controller as upload_controller

    def queue_for_test(queued_statement_id):
        """Capture the statement AI queue request."""
        submitted.append(queued_statement_id)
        return "statementaijob123"

    monkeypatch.setattr(upload_controller.upload_workflow, "queue_statement_llm_categorization", queue_for_test)

    response = client.post(
        f"/upload/{statement_id}/categorize-unknowns",
        data={CSRF_FIELD_NAME: set_csrf_token(client), "next": "/upload"},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert submitted == [statement_id]
    assert_visible_text(response, "AI categorization queued for 1 unknown transaction.")


def test_upload_route_rejects_duplicate_statement_checksum(client, core_conn, monkeypatch):
    """Verify that duplicate uploads are rejected before queueing background work."""
    statement_type_id = core_conn.execute(text("""
        SELECT id
        FROM statement_types
        WHERE active = 1
        ORDER BY id
        LIMIT 1
        """)).fetchone()._mapping["id"]
    core_conn.execute(
        text("""
        INSERT INTO statements (
            statement_type_id,
            filename,
            checksum,
            raw_text,
            uploaded_at
        )
        VALUES (:p0, 'already.csv', :p1, 'Date,Description,Amount', '2026-05-11T12:00:00Z')
        """),
        {"p0": statement_type_id, "p1": "known-checksum"},
    )
    core_conn.commit()
    monkeypatch.setattr("finance_app.modules.upload.controller.file_checksum", lambda uploaded_file: "known-checksum")

    response = client.post(
        "/upload",
        data={
            CSRF_FIELD_NAME: set_csrf_token(client),
            "account_name": "Personal",
            "statement_type_id": str(statement_type_id),
            "statement": (io.BytesIO(b"Date,Description,Amount\n"), "duplicate.csv"),
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )

    statement_count = core_conn.execute(text("SELECT COUNT(*) AS count FROM statements")).fetchone()._mapping["count"]
    assert response.status_code == 200
    assert_visible_text(response, "This statement was already uploaded as already.csv on 2026-05-11T12:00:00Z")
    assert statement_count == 1
