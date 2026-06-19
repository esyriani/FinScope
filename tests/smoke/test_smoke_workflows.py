"""Smoke tests for high-value application workflows."""

import io
import time

import pytest
from sqlalchemy import select, text
from tests.support.database import insert_rule, insert_transaction
from tests.support.web import set_csrf_token

from finance_app.background import runner
from finance_app.core.csrf import CSRF_FIELD_NAME
from finance_app.database.tables import (
    statement_types as statement_types_table,
)


@pytest.fixture(autouse=True)
def isolated_background_jobs():
    """Clear in-memory background jobs around smoke tests."""
    with runner._lock:
        runner._jobs.clear()
        runner._job_sequence = 0
    yield
    wait_for_all_jobs()
    with runner._lock:
        runner._jobs.clear()
        runner._job_sequence = 0


def statement_type_id(conn, parser_type="credit_card"):
    """Return an active statement type id for a parser type."""
    return conn.execute(
        select(statement_types_table.c.id)
        .where(
            statement_types_table.c.parser_type == parser_type,
            statement_types_table.c.active == 1,
        )
        .order_by(statement_types_table.c.id)
        .limit(1)
    ).scalar_one()


def post_csv_upload(client, core_conn, filename, raw_csv, account_name="Personal"):
    """Upload a CSV statement through the real route and return its import job."""
    response = client.post(
        "/upload",
        data={
            CSRF_FIELD_NAME: set_csrf_token(client),
            "account_name": account_name,
            "statement_type_id": str(statement_type_id(core_conn, "credit_card")),
            "statement": (io.BytesIO(raw_csv.encode("utf-8")), filename),
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    job = wait_for_job_label(f"Import {filename}")
    return response, job


def wait_for_job_label(label, timeout=5):
    """Wait until a job with a label exists and finishes."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = next(
            (item for item in runner.list_background_jobs(limit=None) if item["label"] == label),
            None,
        )
        if job is not None:
            return wait_for_job(job["id"], timeout=max(0.1, deadline - time.monotonic()))
        time.sleep(0.01)
    raise AssertionError(f"Background job did not start: {label}")


def wait_for_job(job_id, timeout=5):
    """Wait until one background job reaches a terminal state."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = runner.get_background_job(job_id)
        if job and job["status"] in runner.FINISHED_STATUSES:
            assert job["status"] == "completed", job
            return job
        time.sleep(0.01)
    raise AssertionError(f"Background job did not finish: {job_id}")


def wait_for_all_jobs(timeout=5):
    """Wait for currently tracked jobs to finish."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        jobs = runner.list_background_jobs(limit=None)
        if all(job["status"] in runner.FINISHED_STATUSES for job in jobs):
            return jobs
        time.sleep(0.01)
    return runner.list_background_jobs(limit=None)


def insert_smoke_transaction(conn, description, amount, category, needs_review, fingerprint, tx_date="2026-01-02"):
    """Insert one transaction row and return its id."""
    return insert_transaction(
        conn,
        description=description,
        amount=amount,
        category=category,
        needs_review=needs_review,
        fingerprint=fingerprint,
        tx_date=tx_date,
    )


def transaction_category(conn, description):
    """Return the current category for a transaction description."""
    return conn.execute(
        text("""
        SELECT category
        FROM transactions
        WHERE description = :p0
        """),
        {"p0": description},
    ).scalar_one_or_none()


def transaction_count(conn, description):
    """Return how many transactions exist for one description."""
    return conn.execute(
        text("""
        SELECT COUNT(*) AS count
        FROM transactions
        WHERE description = :p0
        """),
        {"p0": description},
    ).scalar_one()


def test_smoke_csv_upload_creates_transaction_visible_in_list(client, core_conn):
    """Upload a CSV through the app and verify the transaction list sees it."""
    response, job = post_csv_upload(
        client,
        core_conn,
        "smoke-end-to-end.csv",
        "Date,Description,Amount\n2026-01-02,Smoke End To End Market,12.34\n",
    )

    transactions_page = client.get("/transactions?period=all")
    body = transactions_page.get_data(as_text=True)

    assert response.status_code == 200
    assert "Added 1 transactions" in job["result"]
    assert transaction_count(core_conn, "Smoke End To End Market") == 1
    assert transactions_page.status_code == 200
    assert "Smoke End To End Market" in body


def test_smoke_rule_creation_auto_categorizes_uploaded_matching_transaction(client, core_conn):
    """Create a rule, upload a matching CSV, and verify import categorization."""
    rule_response = client.post(
        "/rules/create",
        data={
            CSRF_FIELD_NAME: set_csrf_token(client),
            "confirm_preview": "1",
            "keyword": "Smoke Grocery",
            "category": "Food",
            "tags": ["Tax"],
        },
        follow_redirects=True,
    )

    _, job = post_csv_upload(
        client,
        core_conn,
        "smoke-rule-match.csv",
        "Date,Description,Amount\n2026-01-03,Smoke Grocery #777,45.67\n",
    )

    transactions_page = client.get("/transactions?period=all&category=Food")
    body = transactions_page.get_data(as_text=True)

    assert rule_response.status_code == 200
    assert "Added 1 transactions" in job["result"]
    assert transaction_category(core_conn, "Smoke Grocery #777") == "Food"
    assert transactions_page.status_code == 200
    assert "Smoke Grocery #777" in body


def test_smoke_upload_job_can_be_undone(client, core_conn):
    """Upload through a background job, undo it through /jobs, and verify removal."""
    insert_rule(core_conn, "UNDO MARKET", "Food", tags=["Tax"])
    _, job = post_csv_upload(
        client,
        core_conn,
        "smoke-undo.csv",
        "Date,Description,Amount\n2026-01-04,Undo Market,10.00\n",
    )

    undo_response = client.post(
        f"/jobs/{job['id']}/undo",
        data={CSRF_FIELD_NAME: set_csrf_token(client)},
        follow_redirects=True,
    )

    assert undo_response.status_code == 200
    assert runner.get_background_job(job["id"])["undo_status"] == "undone"
    assert transaction_count(core_conn, "Undo Market") == 0


def test_smoke_dashboard_loads_with_seeded_data(client, core_conn):
    """Load the dashboard with seeded data and verify the page reaches content."""
    insert_smoke_transaction(
        core_conn,
        "Smoke Dashboard Grocery",
        80.00,
        "Food",
        0,
        "smoke-dashboard-food",
        "2026-01-05",
    )
    insert_smoke_transaction(
        core_conn,
        "Smoke Dashboard Hydro",
        120.00,
        "Utilities",
        0,
        "smoke-dashboard-utilities",
        "2026-01-06",
    )
    insert_smoke_transaction(
        core_conn,
        "Smoke Dashboard Payroll",
        -1000.00,
        "Income",
        0,
        "smoke-dashboard-income",
        "2026-01-07",
    )

    response = client.get("/dashboard?period=all")
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert 'id="dashboard-chart-data"' not in body
    assert "Reports" in body
    assert "Income and credits" in body


def test_smoke_same_transaction_can_import_for_different_accounts(client, core_conn):
    """Upload the same merchant for two accounts and verify both appear."""
    insert_rule(core_conn, "SHARED ACCOUNT MERCHANT", "Food")
    first_csv = "Date,Description,Amount\n2026-01-08,Shared Account Merchant,12.34\n"
    second_csv = "Date,Description,Amount\n" "2026-01-08,Shared Account Merchant,12.34\n" "not a date,ignored row,\n"

    post_csv_upload(client, core_conn, "smoke-account-a.csv", first_csv, account_name="Account A")
    post_csv_upload(client, core_conn, "smoke-account-b.csv", second_csv, account_name="Account B")

    rows = core_conn.execute(text("""
        SELECT accounts.name AS account_name, transactions.category
        FROM transactions
        JOIN accounts ON accounts.id = transactions.account_id
        WHERE transactions.description = 'Shared Account Merchant'
        ORDER BY accounts.name
        """)).mappings().fetchall()
    transactions_page = client.get("/transactions?period=all&search=Shared+Account+Merchant")
    body = transactions_page.get_data(as_text=True)

    assert [(row["account_name"], row["category"]) for row in rows] == [
        ("Account A", "Food"),
        ("Account B", "Food"),
    ]
    assert transactions_page.status_code == 200
    assert "Shared Account Merchant" in body
