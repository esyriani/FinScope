"""Smoke tests for high-value application workflows."""

import io

import pytest
from sqlalchemy import select, text
from tests.support.database import insert_rule, insert_transaction
from tests.support.html import assert_has_element, assert_visible_text
from tests.support.jobs import clear_background_jobs, wait_for_all_background_jobs, wait_for_background_job_label
from tests.support.web import set_csrf_token

from finance_app.background import runner
from finance_app.core.csrf import CSRF_FIELD_NAME
from finance_app.database.tables import (
    statement_types as statement_types_table,
)


@pytest.fixture(autouse=True)
def isolated_background_jobs():
    """Clear in-memory background jobs around smoke tests."""
    clear_background_jobs()
    yield
    wait_for_all_background_jobs()
    clear_background_jobs()


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
    job = wait_for_background_job_label(f"Import {filename}")
    return response, job


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


def test_smoke_csv_upload_creates_transaction_visible_in_list(owner_client, core_conn):
    """Upload a CSV through the app and verify the transaction list sees it."""
    response, job = post_csv_upload(
        owner_client,
        core_conn,
        "smoke-end-to-end.csv",
        "Date,Description,Amount\n2026-01-02,Smoke End To End Market,12.34\n",
    )

    transactions_page = owner_client.get("/transactions?period=all")

    assert response.status_code == 200
    assert "Added 1 transactions" in job["result"]
    assert transaction_count(core_conn, "Smoke End To End Market") == 1
    assert transactions_page.status_code == 200
    assert_visible_text(transactions_page, "Smoke End To End Market")


def test_smoke_rule_creation_auto_categorizes_uploaded_matching_transaction(owner_client, core_conn):
    """Create a rule, upload a matching CSV, and verify import categorization."""
    rule_response = owner_client.post(
        "/rules/create",
        data={
            CSRF_FIELD_NAME: set_csrf_token(owner_client),
            "confirm_preview": "1",
            "keyword": "Smoke Grocery",
            "category": "Food",
            "tags": ["Tax"],
        },
        follow_redirects=True,
    )

    _, job = post_csv_upload(
        owner_client,
        core_conn,
        "smoke-rule-match.csv",
        "Date,Description,Amount\n2026-01-03,Smoke Grocery #777,45.67\n",
    )

    transactions_page = owner_client.get("/transactions?period=all&categories=Food")

    assert rule_response.status_code == 200
    assert "Added 1 transactions" in job["result"]
    assert transaction_category(core_conn, "Smoke Grocery #777") == "Food"
    assert transactions_page.status_code == 200
    assert_visible_text(transactions_page, "Smoke Grocery #777")


def test_smoke_upload_job_can_be_undone(owner_client, core_conn):
    """Upload through a background job, undo it through /jobs, and verify removal."""
    insert_rule(core_conn, "UNDO MARKET", "Food", tags=["Tax"])
    _, job = post_csv_upload(
        owner_client,
        core_conn,
        "smoke-undo.csv",
        "Date,Description,Amount\n2026-01-04,Undo Market,10.00\n",
    )

    undo_response = owner_client.post(
        f"/jobs/{job['id']}/undo",
        data={CSRF_FIELD_NAME: set_csrf_token(owner_client)},
        follow_redirects=True,
    )

    assert undo_response.status_code == 200
    assert runner.get_background_job(job["id"])["undo_status"] == "undone"
    assert transaction_count(core_conn, "Undo Market") == 0


def test_smoke_dashboard_loads_with_seeded_data(owner_client, core_conn):
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

    response = owner_client.get("/dashboard?period=all")

    assert response.status_code == 200
    assert_has_element(response, "script", attrs={"id": "dashboard-chart-data"})
    assert_visible_text(
        response,
        "Analysis readiness",
        "Trend preview",
        "Top drivers",
        "Explore reports",
        "Income and credits",
    )


def test_smoke_same_transaction_can_import_for_different_accounts(owner_client, core_conn):
    """Upload the same merchant for two accounts and verify both appear."""
    insert_rule(core_conn, "SHARED ACCOUNT MERCHANT", "Food")
    first_csv = "Date,Description,Amount\n2026-01-08,Shared Account Merchant,12.34\n"
    second_csv = "Date,Description,Amount\n" "2026-01-08,Shared Account Merchant,12.34\n" "not a date,ignored row,\n"

    post_csv_upload(owner_client, core_conn, "smoke-account-a.csv", first_csv, account_name="Account A")
    post_csv_upload(owner_client, core_conn, "smoke-account-b.csv", second_csv, account_name="Account B")

    rows = core_conn.execute(text("""
        SELECT accounts.name AS account_name, transactions.category
        FROM transactions
        JOIN accounts ON accounts.id = transactions.account_id
        WHERE transactions.description = 'Shared Account Merchant'
        ORDER BY accounts.name
        """)).mappings().fetchall()
    transactions_page = owner_client.get("/transactions?period=all&search=Shared+Account+Merchant")

    assert [(row["account_name"], row["category"]) for row in rows] == [
        ("Account A", "Food"),
        ("Account B", "Food"),
    ]
    assert transactions_page.status_code == 200
    assert_visible_text(transactions_page, "Shared Account Merchant")
