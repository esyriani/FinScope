"""HTTP contracts for queuing user-owned transaction backup imports."""

import io

import pytest
from sqlalchemy import func, select
from tests.support.database import insert_transaction, set_owner_setting
from tests.support.html import assert_has_element, assert_link, assert_visible_text, parse_html
from tests.support.jobs import capture_background_jobs, reject_background_jobs
from tests.support.transaction_backups import backup_csv, backup_row
from tests.support.web import csrf_enabled_client

from finance_app.database.tables import accounts, background_jobs, categories, merchants, tags, transactions
from finance_app.modules.transactions import backup_payloads, backup_workflow
from finance_app.modules.transactions.controller import TRANSACTION_BACKUP_IMPORT_REFERENCE
from finance_app.modules.transactions.exporter import export_transactions_backup_csv


def upload_backup(client, csv_text):
    """Upload a backup through its preview form and return the opaque reference."""
    response = client.post(
        "/transactions/backup/preview",
        data={
            "backup_file": (io.BytesIO(csv_text.encode("utf-8")), "backup.csv"),
            "next": "/transactions?period=all&search=Keep",
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert response.status_code == 200
    return parse_html(response).find_all("input", attrs={"name": "import_reference"})[0].attrs["value"]


@pytest.mark.parametrize(
    ("client_fixture", "action"),
    [
        ("owner_client", "skip_duplicates"),
        ("owner_client", "import_all"),
        ("editor_client", "skip_duplicates"),
        ("stale_session_client", "import_all"),
    ],
)
def test_confirm_backup_queues_selected_choice_once(request, core_conn, monkeypatch, client_fixture, action):
    """Confirm either policy once while preserving filters and keeping CSV out of the session."""
    client = csrf_enabled_client(request.getfixturevalue(client_fixture))
    insert_transaction(core_conn, description="Keep private backup detail", fingerprint="route-queue-backup")
    csv_text = backup_csv(
        [
            backup_row(description="Keep private backup detail", fingerprint="route-queue-backup"),
            backup_row(description="Keep new private backup detail", fingerprint="route-queue-new"),
        ]
    )
    reference = upload_backup(client, csv_text)
    recorder = capture_background_jobs(monkeypatch, backup_workflow)
    form = {"import_reference": reference, "duplicate_action": action, "next": "/transactions?period=all&search=Keep"}

    response = client.post("/transactions/backup/import", data=form)
    replay = client.post("/transactions/backup/import", data=form, follow_redirects=True)

    assert response.status_code == 302
    assert response.headers["Location"] == form["next"]
    job = recorder.single()
    assert job.label == "Import transaction backup"
    assert job.func == backup_workflow.import_transactions_backup_job
    assert job.args == (csv_text, action)
    assert_visible_text(replay, "Transaction backup import queued.", "Transaction backup import preview expired.")
    with client.session_transaction() as session:
        assert "Keep private backup detail" not in str(dict(session))


def test_backup_claim_is_bound_to_uploading_user(owner_client, editor_client, core_conn, monkeypatch):
    """Another editor cannot consume an upload reference belonging to the owner."""
    owner = csrf_enabled_client(owner_client)
    editor = csrf_enabled_client(editor_client)
    insert_transaction(core_conn, fingerprint="route-backup-owned")
    reference = upload_backup(owner, export_transactions_backup_csv(core_conn))
    recorder = capture_background_jobs(monkeypatch, backup_workflow)
    form = {"import_reference": reference, "duplicate_action": "import_all"}

    rejected = editor.post("/transactions/backup/import", data=form, follow_redirects=True)
    assert_visible_text(rejected, "Transaction backup import preview expired.")
    assert not recorder
    assert owner.post("/transactions/backup/import", data=form).status_code == 302
    recorder.single()


def test_backup_queue_rejection_allows_same_preview_to_be_retried(csrf_client, core_conn, monkeypatch):
    """An executor rejection releases the upload claim without losing the reviewed choice."""
    insert_transaction(core_conn, fingerprint="route-backup-rejected")
    reference = upload_backup(
        csrf_client,
        backup_csv(
            [
                backup_row(fingerprint="route-backup-rejected"),
                backup_row(description="Keep new row", fingerprint="route-backup-retry-new"),
            ]
        ),
    )
    reject_background_jobs(monkeypatch, backup_workflow)
    form = {"import_reference": reference, "duplicate_action": "skip_duplicates"}

    response = csrf_client.post("/transactions/backup/import", data=form, follow_redirects=True)
    assert_visible_text(response, "Transaction backup import could not be queued. Try again.")
    assert_has_element(
        response, "div", attrs={"id": "transaction-backup-import-preview-modal", "data-auto-show-modal": True}
    )
    recorder = capture_background_jobs(monkeypatch, backup_workflow)
    csrf_client.post("/transactions/backup/import", data=form)
    assert recorder.single().args[1] == "skip_duplicates"


@pytest.mark.parametrize(
    ("client_fixture", "status", "location"),
    [
        ("viewer_client", 403, None),
        ("anonymous_client", 302, "/login"),
        ("must_change_password_client", 302, "/password"),
    ],
)
def test_backup_confirmation_enforces_auth_states(request, monkeypatch, client_fixture, status, location):
    """Only authenticated editors and owners may queue backup restoration."""
    client = csrf_enabled_client(request.getfixturevalue(client_fixture))
    recorder = capture_background_jobs(monkeypatch, backup_workflow)
    response = client.post(
        "/transactions/backup/import", data={"import_reference": "unknown", "duplicate_action": "import_all"}
    )
    assert response.status_code == status
    if location:
        assert location in response.headers["Location"]
    assert not recorder


def test_backup_confirmation_requires_csrf(owner_client, monkeypatch):
    """A confirmation lacking CSRF cannot reach the queue workflow."""
    recorder = capture_background_jobs(monkeypatch, backup_workflow)
    response = owner_client.post("/transactions/backup/import", data={"duplicate_action": "import_all"})
    assert response.status_code == 403
    assert not recorder


def test_backup_preview_redirect_keeps_filters_and_displays_escaped_imported_values(
    csrf_client, core_conn, monkeypatch
):
    """A GET preview retains the starting list and escapes untrusted backup text."""
    description = "<script id=backup-injection>Keep</script>"
    insert_transaction(core_conn, description=description, fingerprint="backup-escaped-preview")
    csv_text = export_transactions_backup_csv(core_conn)
    next_url = "/transactions?period=all&search=Keep&sort=amount&direction=asc"
    response = csrf_client.post(
        "/transactions/backup/preview",
        data={"next": next_url, "backup_file": (io.BytesIO(csv_text.encode()), "backup.csv")},
    )
    assert response.status_code == 302
    assert response.headers["Location"] == next_url
    with csrf_client.session_transaction() as session:
        assert TRANSACTION_BACKUP_IMPORT_REFERENCE in session
        assert description not in str(dict(session))
    preview = csrf_client.get(next_url)
    assert_has_element(preview, "input", attrs={"name": "search", "value": "Keep"})
    assert_has_element(preview, "input", attrs={"name": "next", "value": next_url})
    document = parse_html(preview)
    assert not document.has_element("script", attrs={"id": "backup-injection"})
    assert_visible_text(preview, description)
    reference = document.find_all("input", attrs={"name": "import_reference"})[0].attrs["value"]
    recorder = capture_background_jobs(monkeypatch, backup_workflow)
    confirmation = csrf_client.post(
        "/transactions/backup/import",
        data={"next": next_url, "import_reference": reference, "duplicate_action": "import_all"},
        follow_redirects=True,
    )
    assert_link(confirmation, "/jobs", text="View processing")
    recorder.single()


@pytest.mark.parametrize(("language", "match_type"), [("en", "fingerprint"), ("fr", "details")])
def test_backup_skipping_all_duplicates_does_not_queue_or_change_any_data(
    csrf_client, core_conn, data_factory, monkeypatch, language, match_type
):
    """All-skipped confirmations consume the preview without changing references or job history."""
    set_owner_setting(core_conn, "ui_language", language)
    account_id = data_factory.account(name="Keep existing checking", account_type="checking")
    data_factory.transaction(
        account_id=account_id,
        description="Keep existing transaction",
        tx_date="2026-05-03",
        amount="12.34",
        category="Food",
        fingerprint="all-duplicate-existing",
    )
    tables = (accounts, background_jobs, categories, merchants, tags, transactions)
    before_counts = {
        table.name: core_conn.execute(select(func.count()).select_from(table)).scalar_one() for table in tables
    }
    reference = upload_backup(
        csrf_client,
        backup_csv(
            [
                backup_row(
                    account_name="Keep existing checking",
                    account_type="credit_card",
                    paid_from_account_name="New funding",
                    description="Keep existing transaction",
                    category="New backup category",
                    merchant_name="NEW BACKUP MERCHANT",
                    tags='["New backup tag"]',
                    fingerprint=(
                        "all-duplicate-existing" if match_type == "fingerprint" else "different-backup-fingerprint"
                    ),
                )
            ]
        ),
    )
    recorder = capture_background_jobs(monkeypatch, backup_workflow)
    form = {
        "import_reference": reference,
        "duplicate_action": "skip_duplicates",
        "next": "/transactions?period=all&search=Keep",
    }

    response = csrf_client.post("/transactions/backup/import", data=form, follow_redirects=True)

    message = (
        "All transactions were skipped as duplicates. Nothing was imported."
        if language == "en"
        else "Toutes les transactions ont \u00e9t\u00e9 ignor\u00e9es comme doublons. Aucune transaction n'a \u00e9t\u00e9 import\u00e9e."
    )
    assert_visible_text(response, message)
    assert not recorder
    assert not parse_html(response).has_element("a", attrs={"href": "/jobs"}, text="View processing")
    assert {
        table.name: core_conn.execute(select(func.count()).select_from(table)).scalar_one() for table in tables
    } == before_counts
    account = core_conn.execute(select(accounts).where(accounts.c.id == account_id)).mappings().one()
    assert account["account_type"] == "checking"
    assert account["paid_from_account_id"] is None
    replay = csrf_client.post("/transactions/backup/import", data=form, follow_redirects=True)
    assert_visible_text(replay, "Transaction backup import preview expired." if language == "en" else "a expir\u00e9")
    assert not recorder


def test_backup_confirmation_rechecks_rows_added_after_preview(csrf_client, core_conn, monkeypatch):
    """A formerly new row becoming a duplicate before confirmation must not create a job."""
    reference = upload_backup(
        csrf_client,
        backup_csv(
            [
                backup_row(
                    description="Keep concurrent import",
                    fingerprint="backup-confirmation-race",
                )
            ]
        ),
    )
    insert_transaction(core_conn, description="Keep concurrent import", fingerprint="backup-confirmation-race")
    recorder = capture_background_jobs(monkeypatch, backup_workflow)

    response = csrf_client.post(
        "/transactions/backup/import",
        data={
            "import_reference": reference,
            "duplicate_action": "skip_duplicates",
        },
        follow_redirects=True,
    )

    assert_visible_text(response, "All transactions were skipped as duplicates. Nothing was imported.")
    assert not recorder


def test_backup_preview_invalid_file_returns_translated_error_without_pending_upload(
    csrf_client, core_conn, monkeypatch
):
    """Surface a localized parser error without leaving an upload or processing item."""
    set_owner_setting(core_conn, "ui_language", "fr")
    recorder = capture_background_jobs(monkeypatch, backup_workflow)
    response = csrf_client.post(
        "/transactions/backup/preview",
        data={"backup_file": (io.BytesIO(b""), "bad.csv")},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert_visible_text(response, "Le CSV de sauvegarde des transactions est vide.")
    assert not parse_html(response).has_element("input", attrs={"name": "import_reference"})
    with csrf_client.session_transaction() as session:
        assert TRANSACTION_BACKUP_IMPORT_REFERENCE not in session
    assert not recorder


@pytest.mark.parametrize(
    "next_target",
    [
        "https://example.invalid/transactions",
        "//example.invalid/transactions",
        "http://[",
        "/transactions/backup/import",
    ],
)
def test_backup_preview_redirect_rejects_unsafe_or_malformed_destinations(csrf_client, next_target):
    """Invalid uploads must return to the GET list instead of external or POST-only URLs."""
    response = csrf_client.post("/transactions/backup/preview", data={"next": next_target})

    assert response.status_code == 302
    assert response.headers["Location"] == "/transactions"


def test_backup_preview_localizes_row_errors_and_blocks_partial_import(csrf_client, core_conn, monkeypatch):
    """French preview errors keep imported names unchanged and block invalid backups."""
    set_owner_setting(core_conn, "ui_language", "fr")
    csv_text = backup_csv(
        [
            backup_row(tx_date="bad-date"),
            backup_row(description="Keep original name <b>literal</b>", fingerprint="localized-valid-row"),
        ]
    )
    reference = upload_backup(csrf_client, csv_text)
    # Re-show the pending preview without consuming its confirmation reference.
    with csrf_client.session_transaction() as session:
        session[TRANSACTION_BACKUP_IMPORT_REFERENCE] = reference
    response = csrf_client.get("/transactions?period=all")
    assert_visible_text(response, "Ligne 2 : tx_date doit \u00eatre une date ISO.")
    assert_has_element(response, "button", attrs={"name": "duplicate_action", "value": "import_all", "disabled": True})
    recorder = capture_background_jobs(monkeypatch, backup_workflow)
    confirmation = csrf_client.post(
        "/transactions/backup/import",
        data={"import_reference": reference, "duplicate_action": "import_all"},
        follow_redirects=True,
    )
    assert_visible_text(confirmation, "La sauvegarde contient des lignes invalides")
    assert not recorder


def test_backup_preview_expiry_before_confirmation_is_reported(csrf_client, monkeypatch):
    """An expired preview cannot enqueue work even when the form is retained."""
    now = 100.0
    monkeypatch.setattr(backup_payloads.time, "monotonic", lambda: now)
    reference = upload_backup(csrf_client, backup_csv([backup_row()]))
    now += backup_payloads.TRANSACTION_BACKUP_IMPORT_PAYLOAD_TTL_SECONDS + 1
    recorder = capture_background_jobs(monkeypatch, backup_workflow)
    response = csrf_client.post(
        "/transactions/backup/import",
        data={"import_reference": reference, "duplicate_action": "import_all"},
        follow_redirects=True,
    )
    assert_visible_text(response, "Transaction backup import preview expired.")
    assert not recorder
