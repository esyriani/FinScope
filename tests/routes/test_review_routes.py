"""Route tests for the review feature."""

from sqlalchemy import text
from tests.support.html import (
    assert_has_element,
    assert_input,
    assert_not_visible_text,
    assert_option,
    assert_visible_text,
)
from tests.support.jobs import capture_background_jobs
from tests.support.web import set_csrf_token

from finance_app.core.csrf import CSRF_FIELD_NAME
from finance_app.modules.categories.taxonomy import set_transaction_tags
from finance_app.modules.review import controller as review_controller
from finance_app.modules.review.service import apply_review_group_job, undo_review_group_job


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
        text("""
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
        VALUES ('2026-01-02', :p0, 12.34, :p1, :p2, :p3, 1, :p4)
        """),
        {"p0": description, "p1": category, "p2": source, "p3": confidence, "p4": fingerprint},
    ).lastrowid
    if tags:
        set_transaction_tags(conn, tx_id, tags, source=source)
    conn.commit()
    return tx_id


def test_review_apply_route_queues_group_job(client, core_conn, monkeypatch):
    """Verify review group route queues a background review job."""
    insert_review_transaction(core_conn)
    submitted_jobs = capture_background_jobs(monkeypatch, review_controller, job_id="reviewjob123")

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
    assert_visible_text(response, "Review group queued in the background.")
    submitted = submitted_jobs.single()
    assert submitted.label == "Review METRO GROCERY as Food"
    assert submitted.func is apply_review_group_job
    assert submitted.args[1:] == (
        "METRO GROCERY",
        "Food",
        ["Tax"],
        True,
        "METRO",
        10.0,
        20.0,
        None,
    )
    assert isinstance(submitted.args[0], dict)
    assert submitted.undo_handler is undo_review_group_job
    assert submitted.undo_args == (submitted.args[0],)


def test_review_apply_route_queues_single_transaction_job(client, core_conn, monkeypatch):
    """Verify review route can queue a job for one transaction in a group."""
    tx_id = insert_review_transaction(core_conn)
    submitted_jobs = capture_background_jobs(monkeypatch, review_controller, job_id="reviewjob123")

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
    assert_visible_text(response, "Review transaction queued in the background.")
    submitted = submitted_jobs.single()
    assert submitted.label == f"Review transaction {tx_id} as Food"
    assert submitted.args[1:] == (
        "METRO GROCERY",
        "Food",
        [],
        False,
        "",
        None,
        None,
        tx_id,
    )


def test_review_apply_route_queues_selected_transactions_job(client, core_conn, monkeypatch):
    """Verify review route can queue a job for selected transactions in a group."""
    first_id = insert_review_transaction(core_conn, "Metro Grocery", "review-route-selected-1")
    second_id = insert_review_transaction(core_conn, "Metro Grocery", "review-route-selected-2")
    submitted_jobs = capture_background_jobs(monkeypatch, review_controller, job_id="reviewjob123")

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
    assert_visible_text(response, "Review transactions queued in the background.")
    submitted = submitted_jobs.single()
    assert submitted.label == "Review 2 transactions as Food"
    assert submitted.args[1:] == (
        "METRO GROCERY",
        "Food",
        [],
        False,
        "",
        None,
        None,
        None,
    )
    assert submitted.kwargs == {"selected_transaction_ids": [first_id, second_id]}


def test_review_page_renders_group_transaction_selector(client, core_conn):
    """Verify review group modal renders its transaction selector rows."""
    first_id = insert_review_transaction(core_conn, "Metro Grocery", "review-route-modal-1")
    second_id = insert_review_transaction(core_conn, "Metro Grocery", "review-route-modal-2")

    response = client.get("/review")

    assert response.status_code == 200
    assert_visible_text(
        response,
        "Show all transactions",
        "No transactions selected. The category will apply to the whole group.",
    )
    assert_input(response, name="transaction_ids", value=str(first_id))
    assert_input(response, name="transaction_ids", value=str(second_id))
    assert_has_element(response, "table", attrs={"data-no-export": True, "data-no-row-select": True})
    assert_has_element(response, "span", attrs={"data-review-submit-label": True})
    assert_has_element(response, "select", attrs={"data-category-description-select": True})
    assert_has_element(
        response,
        "option",
        attrs={
            "data-category-description": (
                "Food and drink, including groceries, restaurants, cafes, bakeries, "
                "takeout, delivery, and prepared meals."
            )
        },
    )
    assert_has_element(
        response,
        "label",
        attrs={
            "title": ("Marks transactions that may be useful for tax preparation, accounting, " "or year-end review.")
        },
    )


def test_review_page_prefills_consistent_group_assignment(client, core_conn):
    """Verify review group modal defaults to the existing suggested assignment."""
    insert_review_transaction(
        core_conn,
        "Costco Wholesale W527 Montreal",
        "review-route-prefill-1",
        category="Food",
        source="rule",
        confidence=0.8988,
        tags=["Grocery"],
    )
    insert_review_transaction(
        core_conn,
        "Costco Wholesale W527 Montreal",
        "review-route-prefill-2",
        category="Food",
        source="rule",
        confidence=0.8988,
        tags=["Grocery"],
    )

    response = client.get("/review")

    assert response.status_code == 200
    assert_option(response, value="Food", selected=True)
    assert_input(response, name="tags", value="Grocery", checked=True)


def test_review_page_filters_by_merchant_search(client, core_conn):
    """Verify the review page can be filtered by merchant name."""
    insert_review_transaction(core_conn, "Metro Grocery", "review-route-search-metro")
    insert_review_transaction(core_conn, "Hydro Quebec", "review-route-search-hydro")

    response = client.get("/review?merchant=hydro")

    assert response.status_code == 200
    assert_input(response, name="merchant", value="hydro")
    assert_visible_text(response, "HYDRO QUEBEC")
    assert_not_visible_text(response, "METRO GROCERY")


def test_review_apply_route_rejects_invalid_payloads(client, core_conn, monkeypatch):
    """Verify review route validation avoids queueing malformed jobs."""
    outside_group_id = insert_review_transaction(core_conn, "Other Shop", "review-route-other")
    submitted_jobs = capture_background_jobs(monkeypatch, review_controller, job_id="reviewjob123")
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

    assert_visible_text(invalid_transaction, "Review transaction not found.")
    assert_visible_text(missing_group, "Review group not found.")
    assert_visible_text(unknown_category, "Choose a category before applying the review group.")
    assert_visible_text(invalid_amount, "Amount bounds must be valid numbers.")
    assert_visible_text(invalid_selection, "Review transaction not found.")
    assert len(submitted_jobs) == 0
