"""Smoke tests for high-value application workflows."""

import io
import time

import pytest
from sqlalchemy import insert, select

from finance_app.background import runner
from finance_app.core.csrf import CSRF_FIELD_NAME, CSRF_SESSION_KEY
from finance_app.database.tables import (
    category_rules as category_rules_table,
    statement_types as statement_types_table,
    transactions as transactions_table,
)
from finance_app.modules.categories.repository import resolve_category_id
from finance_app.modules.categories.taxonomy import set_rule_tags


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


def set_csrf_token(client, token="smoke-csrf-token"):
    """Store a CSRF token in the test client's session."""
    with client.session_transaction() as session:
        session[CSRF_SESSION_KEY] = token
    return token


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


def post_csv_upload(client, db_conn, filename, raw_csv, account_name="Personal"):
    """Upload a CSV statement through the real route and return its import job."""
    response = client.post(
        "/upload",
        data={
            CSRF_FIELD_NAME: set_csrf_token(client),
            "account_name": account_name,
            "statement_type_id": str(statement_type_id(db_conn, "credit_card")),
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
            (
                item
                for item in runner.list_background_jobs(limit=None)
                if item["label"] == label
            ),
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


def insert_rule(conn, keyword, category, tags=None):
    """Insert a category rule with optional tags."""
    rule_id = conn.execute(
        insert(category_rules_table).values(
            keyword=keyword,
            category=category,
            category_id=resolve_category_id(conn, category),
            source="manual",
        )
    ).inserted_primary_key[0]
    set_rule_tags(conn, rule_id, tags or [])
    conn.commit()
    return rule_id


def insert_transaction(conn, description, amount, category, needs_review, fingerprint, tx_date="2026-01-02"):
    """Insert one transaction row and return its id."""
    tx_id = conn.execute(
        insert(transactions_table).values(
            tx_date=tx_date,
            description=description,
            amount=amount,
            category=category,
            category_id=resolve_category_id(conn, category),
            needs_review=needs_review,
            fingerprint=fingerprint,
        )
    ).inserted_primary_key[0]
    conn.commit()
    return tx_id


def transaction_category(conn, description):
    """Return category state for a transaction description."""
    return conn.execute(
        select(
            transactions_table.c.category,
            transactions_table.c.needs_review,
            transactions_table.c.category_source,
            transactions_table.c.reviewed_at,
        ).where(transactions_table.c.description == description)
    ).mappings().fetchone()


def test_smoke_csv_upload_creates_transaction_visible_in_list(client, db_conn):
    """Upload a CSV through the app and verify the transaction list sees it."""
    response, job = post_csv_upload(
        client,
        db_conn,
        "smoke-end-to-end.csv",
        "Date,Description,Amount\n2026-01-02,Smoke End To End Market,12.34\n",
    )

    transactions_page = client.get("/transactions?period=all")
    body = transactions_page.get_data(as_text=True)

    assert response.status_code == 200
    assert "Statement queued for background import and categorization." in response.get_data(as_text=True)
    assert "Added 1 transactions" in job["result"]
    assert transactions_page.status_code == 200
    assert "Smoke End To End Market" in body
    assert "UNKNOWN" in body


def test_smoke_rule_creation_auto_categorizes_uploaded_matching_transaction(client, db_conn):
    """Create a rule, upload a matching CSV, and verify import categorization."""
    rule_response = client.post(
        "/rules/create",
        data={
            CSRF_FIELD_NAME: set_csrf_token(client),
            "keyword": "Smoke Grocery",
            "category": "Food",
            "tags": ["Tax"],
        },
        follow_redirects=True,
    )

    _, job = post_csv_upload(
        client,
        db_conn,
        "smoke-rule-match.csv",
        "Date,Description,Amount\n2026-01-03,Smoke Grocery #777,45.67\n",
    )

    tx = transaction_category(db_conn, "Smoke Grocery #777")
    transactions_page = client.get("/transactions?period=all&category=Food")
    body = transactions_page.get_data(as_text=True)

    assert rule_response.status_code == 200
    assert "Rule saved for: SMOKE GROCERY" in rule_response.get_data(as_text=True)
    assert "Added 1 transactions" in job["result"]
    assert (tx["category"], tx["needs_review"], tx["category_source"]) == ("Food", 0, "rule")
    assert "Smoke Grocery #777" in body
    assert "Food" in body


def test_smoke_manual_transaction_edit_persists_and_category_filter_uses_new_value(client, db_conn):
    """Edit a transaction through the route and verify filtered list output."""
    edited_id = insert_transaction(
        db_conn,
        "Smoke Manual Cafe",
        18.00,
        "UNKNOWN",
        1,
        "smoke-manual-edit",
    )
    insert_transaction(
        db_conn,
        "Smoke Other Grocery",
        30.00,
        "Food",
        0,
        "smoke-manual-other",
    )

    response = client.post(
        f"/transactions/{edited_id}/category",
        data={
            CSRF_FIELD_NAME: set_csrf_token(client),
            "category": "Utilities",
            "rule_action": "transaction_only",
        },
        follow_redirects=True,
    )
    filtered_page = client.get("/transactions?period=all&category=Utilities")
    body = filtered_page.get_data(as_text=True)
    tx = transaction_category(db_conn, "Smoke Manual Cafe")

    assert response.status_code == 200
    assert "Category updated for this transaction only." in response.get_data(as_text=True)
    assert (tx["category"], tx["needs_review"], tx["category_source"]) == ("Utilities", 0, "manual")
    assert tx["reviewed_at"] is not None
    assert "Smoke Manual Cafe" in body
    assert "Utilities" in body
    assert "Smoke Other Grocery" not in body


def test_smoke_mark_reviewed_removes_transaction_from_review_filter(client, db_conn):
    """Mark a transaction reviewed and verify the needs-review route no longer returns it."""
    tx_id = insert_transaction(
        db_conn,
        "Smoke Needs Review",
        22.00,
        "UNKNOWN",
        1,
        "smoke-review-needed",
    )
    before = client.get("/transactions?period=all&review=needs_review")

    response = client.post(
        f"/transactions/{tx_id}/verify",
        data={CSRF_FIELD_NAME: set_csrf_token(client)},
        follow_redirects=True,
    )
    after = client.get("/transactions?period=all&review=needs_review")
    tx = transaction_category(db_conn, "Smoke Needs Review")

    assert "Smoke Needs Review" in before.get_data(as_text=True)
    assert response.status_code == 200
    assert "Transaction marked verified." in response.get_data(as_text=True)
    assert tx["needs_review"] == 0
    assert tx["reviewed_at"] is not None
    assert "Smoke Needs Review" not in after.get_data(as_text=True)


def test_smoke_upload_job_undo_removes_statement_transactions_and_tags(client, db_conn):
    """Upload through a background job, undo it through /jobs, and verify cleanup."""
    insert_rule(db_conn, "UNDO MARKET", "Food", tags=["Tax"])
    _, job = post_csv_upload(
        client,
        db_conn,
        "smoke-undo.csv",
        "Date,Description,Amount\n2026-01-04,Undo Market,10.00\n",
    )
    tx = db_conn.execute(
        """
        SELECT id, statement_id
        FROM transactions
        WHERE description = 'Undo Market'
        """
    ).fetchone()
    tag_count_before = db_conn.execute(
        """
        SELECT COUNT(*) AS count
        FROM transaction_tags
        WHERE transaction_id = ?
        """,
        (tx["id"],),
    ).fetchone()["count"]

    undo_response = client.post(
        f"/jobs/{job['id']}/undo",
        data={CSRF_FIELD_NAME: set_csrf_token(client)},
        follow_redirects=True,
    )
    transaction_count = db_conn.execute(
        """
        SELECT COUNT(*) AS count
        FROM transactions
        WHERE id = ?
        """,
        (tx["id"],),
    ).fetchone()["count"]
    statement_count = db_conn.execute(
        """
        SELECT COUNT(*) AS count
        FROM statements
        WHERE id = ?
        """,
        (tx["statement_id"],),
    ).fetchone()["count"]
    tag_count_after = db_conn.execute(
        """
        SELECT COUNT(*) AS count
        FROM transaction_tags
        WHERE transaction_id = ?
        """,
        (tx["id"],),
    ).fetchone()["count"]

    assert tag_count_before == 1
    assert undo_response.status_code == 200
    assert "Removed statement smoke-undo.csv and 1 transaction." in undo_response.get_data(as_text=True)
    assert runner.get_background_job(job["id"])["undo_status"] == "undone"
    assert transaction_count == 0
    assert statement_count == 0
    assert tag_count_after == 0


def test_smoke_dashboard_with_data_exposes_chart_payload_and_tables(client, db_conn):
    """Load dashboard with seeded data and verify chart payload plus table rows."""
    insert_transaction(db_conn, "Smoke Dashboard Grocery", 80.00, "Food", 0, "smoke-dashboard-food", "2026-01-05")
    insert_transaction(db_conn, "Smoke Dashboard Hydro", 120.00, "Utilities", 0, "smoke-dashboard-utilities", "2026-01-06")
    insert_transaction(db_conn, "Smoke Dashboard Payroll", -1000.00, "Income", 0, "smoke-dashboard-income", "2026-01-07")

    response = client.get("/dashboard?period=all")
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert 'id="dashboard-chart-data"' in body
    assert '"categoryLabels": ["Utilities", "Food"]' in body
    assert '"categoryTotals": [120.0, 80.0]' in body
    assert 'id="categoryChart"' in body
    assert 'id="netChart"' in body
    assert "Merchant analytics" in body
    assert "SMOKE DASHBOARD HYDRO" in body
    assert "Category detail" in body
    assert "Utilities" in body


def test_smoke_transaction_route_sort_orders_change_visible_order(client, db_conn):
    """Verify transaction route sorting changes rendered row order."""
    insert_transaction(db_conn, "Smoke Sort Recent", 10.00, "Food", 0, "smoke-sort-recent", "2026-01-03")
    insert_transaction(db_conn, "Smoke Sort Expensive", 99.00, "Food", 0, "smoke-sort-expensive", "2026-01-01")

    date_page = client.get("/transactions?period=all&sort=date&direction=desc").get_data(as_text=True)
    amount_page = client.get("/transactions?period=all&sort=amount&direction=desc").get_data(as_text=True)

    assert date_page.index("Smoke Sort Recent") < date_page.index("Smoke Sort Expensive")
    assert amount_page.index("Smoke Sort Expensive") < amount_page.index("Smoke Sort Recent")


def test_smoke_same_transaction_can_import_for_different_accounts(client, db_conn):
    """Verify account-scoped fingerprints do not dedupe transactions across accounts."""
    insert_rule(db_conn, "SHARED ACCOUNT MERCHANT", "Food")
    first_csv = "Date,Description,Amount\n2026-01-08,Shared Account Merchant,12.34\n"
    second_csv = (
        "Date,Description,Amount\n"
        "2026-01-08,Shared Account Merchant,12.34\n"
        "not a date,ignored row,\n"
    )

    post_csv_upload(client, db_conn, "smoke-account-a.csv", first_csv, account_name="Account A")
    post_csv_upload(client, db_conn, "smoke-account-b.csv", second_csv, account_name="Account B")

    rows = db_conn.execute(
        """
        SELECT accounts.name AS account_name, transactions.fingerprint, transactions.category
        FROM transactions
        JOIN accounts ON accounts.id = transactions.account_id
        WHERE transactions.description = 'Shared Account Merchant'
        ORDER BY accounts.name
        """
    ).fetchall()
    transactions_page = client.get("/transactions?period=all&search=Shared+Account+Merchant").get_data(as_text=True)

    assert [(row["account_name"], row["category"]) for row in rows] == [
        ("Account A", "Food"),
        ("Account B", "Food"),
    ]
    assert rows[0]["fingerprint"] != rows[1]["fingerprint"]
    assert "Showing 1-2 of 2 transactions" in transactions_page
    assert "Account A" in transactions_page
    assert "Account B" in transactions_page
