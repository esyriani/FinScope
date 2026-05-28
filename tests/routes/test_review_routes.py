"""Route tests for the review feature."""

import re

from finance_app.core.csrf import CSRF_FIELD_NAME, CSRF_SESSION_KEY
from finance_app.modules.categories.taxonomy import set_transaction_tags
from finance_app.modules.review import controller as review_controller
from finance_app.modules.review.service import apply_review_group_job, undo_review_group_job


def set_csrf_token(client, token="test-csrf-token"):
    """Store a CSRF token in the test client's session."""
    with client.session_transaction() as session:
        session[CSRF_SESSION_KEY] = token
    return token


def insert_review_transaction(
    conn,
    description="Metro Grocery #1",
    fingerprint="review-route",
    category="UNKNOWN",
    source="unknown",
    confidence=None,
    tags=None,
):
    """Insert a transaction that can be reviewed."""
    tx_id = conn.execute(
        """
        INSERT INTO transactions (
            tx_date,
            description,
            amount,
            category,
            category_source,
            category_confidence,
            needs_review,
            fingerprint
        )
        VALUES ('2026-01-02', ?, 12.34, ?, ?, ?, 1, ?)
        """,
        (description, category, source, confidence, fingerprint),
    ).lastrowid
    if tags:
        set_transaction_tags(conn, tx_id, tags, source=source)
    conn.commit()
    return tx_id


def capture_review_jobs(monkeypatch):
    """Patch review route background submission and return captured jobs."""
    submitted_jobs = []

    def capture_job(label, func, *args, undo_handler=None, undo_args=None, **kwargs):
        """Capture submitted review job metadata."""
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
        return "reviewjob123"

    monkeypatch.setattr(review_controller, "submit_background_job", capture_job)
    return submitted_jobs


def test_review_apply_route_queues_group_job(client, db_conn, monkeypatch):
    """Verify review group route queues a background review job."""
    insert_review_transaction(db_conn)
    submitted_jobs = capture_review_jobs(monkeypatch)

    response = client.post(
        "/review/apply",
        data={
            CSRF_FIELD_NAME: set_csrf_token(client),
            "merchant_key": "METRO GROCERY",
            "category": "Food",
            "tags": ["Tax"],
            "create_rule": "1",
            "keyword": "Metro",
            "amount_min": "10",
            "amount_max": "20",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"Review group queued in the background." in response.data
    submitted = submitted_jobs[0]
    assert submitted["label"] == "Review METRO GROCERY as Food"
    assert submitted["func"] is apply_review_group_job
    assert submitted["args"][1:] == (
        "METRO GROCERY",
        "Food",
        ["Tax"],
        True,
        "METRO",
        10.0,
        20.0,
        None,
    )
    assert isinstance(submitted["args"][0], dict)
    assert submitted["undo_handler"] is undo_review_group_job
    assert submitted["undo_args"] == (submitted["args"][0],)


def test_review_apply_route_queues_single_transaction_job(client, db_conn, monkeypatch):
    """Verify review route can queue a job for one transaction in a group."""
    tx_id = insert_review_transaction(db_conn)
    submitted_jobs = capture_review_jobs(monkeypatch)

    response = client.post(
        "/review/apply",
        data={
            CSRF_FIELD_NAME: set_csrf_token(client),
            "merchant_key": "METRO GROCERY",
            "transaction_id": str(tx_id),
            "category": "Food",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"Review transaction queued in the background." in response.data
    submitted = submitted_jobs[0]
    assert submitted["label"] == f"Review transaction {tx_id} as Food"
    assert submitted["args"][1:] == (
        "METRO GROCERY",
        "Food",
        [],
        False,
        "",
        None,
        None,
        tx_id,
    )


def test_review_apply_route_queues_selected_transactions_job(client, db_conn, monkeypatch):
    """Verify review route can queue a job for selected transactions in a group."""
    first_id = insert_review_transaction(db_conn, "Metro Grocery", "review-route-selected-1")
    second_id = insert_review_transaction(db_conn, "Metro Grocery", "review-route-selected-2")
    submitted_jobs = capture_review_jobs(monkeypatch)

    response = client.post(
        "/review/apply",
        data={
            CSRF_FIELD_NAME: set_csrf_token(client),
            "merchant_key": "METRO GROCERY",
            "transaction_ids": [str(first_id), str(second_id)],
            "category": "Food",
            "create_rule": "1",
            "keyword": "Metro",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"Review transactions queued in the background." in response.data
    submitted = submitted_jobs[0]
    assert submitted["label"] == "Review 2 transactions as Food"
    assert submitted["args"][1:] == (
        "METRO GROCERY",
        "Food",
        [],
        False,
        "",
        None,
        None,
        None,
    )
    assert submitted["kwargs"] == {"selected_transaction_ids": [first_id, second_id]}


def test_review_page_renders_group_transaction_selector(client, db_conn):
    """Verify review group modal renders its transaction selector rows."""
    first_id = insert_review_transaction(db_conn, "Metro Grocery", "review-route-modal-1")
    second_id = insert_review_transaction(db_conn, "Metro Grocery", "review-route-modal-2")

    response = client.get("/review")

    assert response.status_code == 200
    assert b"Show all transactions" in response.data
    assert f'value="{first_id}"'.encode() in response.data
    assert f'value="{second_id}"'.encode() in response.data
    assert b'name="transaction_ids"' in response.data
    assert b"data-no-export data-no-row-select" in response.data
    assert b"No transactions selected. The category will apply to the whole group." in response.data
    assert b"data-review-submit-label" in response.data
    assert b"data-category-description-select" in response.data
    assert b"Food and drink, including groceries" in response.data
    assert b"Marks transactions that may be useful for tax preparation" in response.data


def test_review_page_prefills_consistent_group_assignment(client, db_conn):
    """Verify review group modal defaults to the existing suggested assignment."""
    insert_review_transaction(
        db_conn,
        "Costco Wholesale W527 Montreal",
        "review-route-prefill-1",
        category="Food",
        source="rule",
        confidence=0.8988,
        tags=["Grocery"],
    )
    insert_review_transaction(
        db_conn,
        "Costco Wholesale W527 Montreal",
        "review-route-prefill-2",
        category="Food",
        source="rule",
        confidence=0.8988,
        tags=["Grocery"],
    )

    response = client.get("/review")
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert re.search(r'<option\s+value="Food"[^>]*selected', body, re.DOTALL)
    assert re.search(r'<input[^>]*name="tags"[^>]*value="Grocery"[^>]*checked', body, re.DOTALL)


def test_review_page_filters_by_merchant_search(client, db_conn):
    """Verify the review page can be filtered by merchant name."""
    insert_review_transaction(db_conn, "Metro Grocery", "review-route-search-metro")
    insert_review_transaction(db_conn, "Hydro Quebec", "review-route-search-hydro")

    response = client.get("/review?merchant=hydro")

    assert response.status_code == 200
    assert b'name="merchant"' in response.data
    assert b'value="hydro"' in response.data
    assert b"HYDRO QUEBEC" in response.data
    assert b"METRO GROCERY" not in response.data


def test_review_apply_route_rejects_invalid_payloads(client, db_conn, monkeypatch):
    """Verify review route validation avoids queueing malformed jobs."""
    outside_group_id = insert_review_transaction(db_conn, "Other Shop", "review-route-other")
    submitted_jobs = capture_review_jobs(monkeypatch)
    token = set_csrf_token(client)

    invalid_transaction = client.post(
        "/review/apply",
        data={
            CSRF_FIELD_NAME: token,
            "merchant_key": "METRO GROCERY",
            "transaction_id": "abc",
            "category": "Food",
        },
        follow_redirects=True,
    )
    missing_group = client.post(
        "/review/apply",
        data={
            CSRF_FIELD_NAME: token,
            "merchant_key": "",
            "category": "Food",
        },
        follow_redirects=True,
    )
    unknown_category = client.post(
        "/review/apply",
        data={
            CSRF_FIELD_NAME: token,
            "merchant_key": "METRO GROCERY",
            "category": "UNKNOWN",
        },
        follow_redirects=True,
    )
    invalid_amount = client.post(
        "/review/apply",
        data={
            CSRF_FIELD_NAME: token,
            "merchant_key": "METRO GROCERY",
            "category": "Food",
            "create_rule": "1",
            "keyword": "Metro",
            "amount_min": "abc",
        },
        follow_redirects=True,
    )
    invalid_selection = client.post(
        "/review/apply",
        data={
            CSRF_FIELD_NAME: token,
            "merchant_key": "METRO GROCERY",
            "transaction_ids": [str(outside_group_id)],
            "category": "Food",
        },
        follow_redirects=True,
    )

    assert b"Review transaction not found." in invalid_transaction.data
    assert b"Review group not found." in missing_group.data
    assert b"Choose a category before applying the review group." in unknown_category.data
    assert b"Amount bounds must be valid numbers." in invalid_amount.data
    assert b"Review transaction not found." in invalid_selection.data
    assert submitted_jobs == []
