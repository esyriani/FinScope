"""HTTP contracts for human transaction review and explicit reopening.

Exercise permission and CSRF boundaries, shared list/queue status, and the
distinction between rule confidence and human confirmation using test data.
"""

import pytest
from sqlalchemy import select
from tests.support.database import insert_transaction, set_owner_setting
from tests.support.html import assert_visible_text, parse_html
from tests.support.web import csrf_enabled_client

from finance_app.database.tables import transactions
from finance_app.modules.categories.history import candidate_authority
from finance_app.modules.categories.taxonomy import get_transaction_tag_names
from finance_app.modules.review.queries import review_candidate_rows


@pytest.mark.parametrize(
    ("language", "message"),
    [("en", "Transaction marked for review."), ("fr", "Transaction marqu\u00e9e pour r\u00e9vision.")],
)
def test_reopen_review_preserves_classification_and_returns_to_filtered_list(csrf_client, core_conn, language, message):
    """Reopening clears human confirmation, preserves details, and restores queue eligibility."""
    set_owner_setting(core_conn, "ui_language", language)
    transaction_id = insert_transaction(
        core_conn,
        description="Reopen category",
        category="Food",
        category_source="manual",
        category_confidence=1.0,
        needs_review=0,
        reviewed_at="2026-01-02T12:00:00Z",
        categorized_at="2026-01-01T12:00:00Z",
        tags=["Shared"],
        fingerprint="reopen-review",
    )
    before = dict(core_conn.execute(select(transactions).where(transactions.c.id == transaction_id)).mappings().one())
    assert not review_candidate_rows(core_conn, "UNKNOWN")

    response = csrf_client.post(
        f"/transactions/{transaction_id}/review", data={"next": "/transactions?period=all&search=Reopen"}
    )

    assert response.headers["Location"] == "/transactions?period=all&search=Reopen"
    assert_visible_text(csrf_client.get(response.headers["Location"]), message)
    after = dict(core_conn.execute(select(transactions).where(transactions.c.id == transaction_id)).mappings().one())
    assert after == {**before, "reviewed_at": None, "needs_review": 1}
    assert get_transaction_tag_names(core_conn, transaction_id) == ["Shared"]
    assert [row["id"] for row in review_candidate_rows(core_conn, "UNKNOWN")] == [transaction_id]
    assert candidate_authority(after) < candidate_authority(before)

    csrf_client.post(f"/transactions/{transaction_id}/verify")
    assert not review_candidate_rows(core_conn, "UNKNOWN")


def test_transaction_review_button_matches_source_and_review_state(owner_client, core_conn):
    """Render mixed review states, including automatic rule matches below 100 percent."""
    cases = [
        ("rule", 1.0, 0, None, 0, False, True, "Automatic"),
        ("rule", 0.96, 0, None, 0, True, True, "Automatic"),
        ("ai", 1.0, 0, None, 0, True, True, "Automatic"),
        ("history", 0.98, 0, None, 0, True, True, "Automatic"),
        ("rule", 1.0, 1, None, 0, True, False, "Needs review"),
        ("ai", 0.99, 1, None, 0, True, False, "Needs review"),
        ("rule", 1.0, 0, "2026-01-01T12:00:00Z", 0, False, True, "Approved"),
        ("ai", 0.98, 0, None, 1, False, False, "Ignored"),
    ]
    expectations = {}
    for index, (source, confidence, needs_review, reviewed_at, ignored, show_button, show_reopen, status) in enumerate(
        cases
    ):
        transaction_id = insert_transaction(
            core_conn,
            description=f"Review case {index}",
            category="Food",
            category_source=source,
            category_confidence=confidence,
            needs_review=needs_review,
            reviewed_at=reviewed_at,
            ignored=ignored,
            fingerprint=f"review-button-{index}",
        )
        expectations[transaction_id] = (show_button, show_reopen, status)

    response = owner_client.get("/transactions?period=all&ignored=all")
    assert response.status_code == 200
    document = parse_html(response)
    for transaction_id, (show_button, show_reopen, status) in expectations.items():
        forms = document.find_all("form", attrs={"action": f"/transactions/{transaction_id}/verify"})
        assert bool(forms) is show_button, transaction_id
        if show_button:
            assert forms[0].text == "Approve"
        reopen_forms = document.find_all("form", attrs={"action": f"/transactions/{transaction_id}/review"})
        assert bool(reopen_forms) is show_reopen, transaction_id
        if show_reopen:
            assert reopen_forms[0].text == "Mark for review"
        assert document.has_element(
            "tr", attrs={"data-row-edit-target": f"#categorize-transaction-{transaction_id}"}, text=status
        ), transaction_id
    assert {row["id"] for row in review_candidate_rows(core_conn, "UNKNOWN")} == {
        transaction_id for transaction_id, (_, _, status) in expectations.items() if status == "Needs review"
    }


@pytest.mark.parametrize(
    ("client_fixture", "status", "location"),
    [
        ("owner_client", 302, None),
        ("editor_client", 302, None),
        ("stale_session_client", 302, None),
        ("viewer_client", 403, None),
        ("anonymous_client", 302, "/login"),
        ("must_change_password_client", 302, "/password"),
    ],
)
def test_reopen_transaction_review_enforces_permissions(request, core_conn, client_fixture, status, location):
    """Only active authenticated owners and editors may reopen a transaction for review."""
    transaction_id = insert_transaction(core_conn, category="Food", needs_review=0, reviewed_at="2026-01-01T00:00:00Z")
    client = csrf_enabled_client(request.getfixturevalue(client_fixture))
    response = client.post(f"/transactions/{transaction_id}/review")
    assert response.status_code == status
    if location:
        assert location in response.headers["Location"]
    row = core_conn.execute(select(transactions).where(transactions.c.id == transaction_id)).mappings().one()
    assert row["needs_review"] == (1 if status == 302 and location is None else 0)


def test_reopen_transaction_review_requires_csrf(owner_client, core_conn):
    """Reopening without CSRF must leave the existing human review untouched."""
    transaction_id = insert_transaction(core_conn, needs_review=0, reviewed_at="2026-01-01T00:00:00Z")
    assert owner_client.post(f"/transactions/{transaction_id}/review").status_code == 403
    assert (
        core_conn.execute(select(transactions.c.reviewed_at).where(transactions.c.id == transaction_id)).scalar_one()
        is not None
    )


def test_batch_reopen_review_affects_only_selected_active_transactions(csrf_client, core_conn):
    """Batch reopening does not change ignored or unselected rows."""
    ids = [
        insert_transaction(
            core_conn,
            fingerprint=f"batch-reopen-{index}",
            category="Food",
            needs_review=0,
            reviewed_at="2026-01-01T00:00:00Z",
            ignored=1 if index == 1 else 0,
        )
        for index in range(3)
    ]
    response = csrf_client.post(
        "/transactions/batch",
        data={"batch_action": "mark_for_review", "transaction_ids": [str(ids[0]), str(ids[1]), str(ids[0]), "bad"]},
        follow_redirects=True,
    )
    assert_visible_text(response, "Selected transaction marked for review.")
    rows = {row["id"]: row for row in core_conn.execute(select(transactions)).mappings()}
    assert rows[ids[0]]["needs_review"] == 1
    assert rows[ids[0]]["reviewed_at"] is None
    assert all(
        rows[transaction_id]["needs_review"] == 0 and rows[transaction_id]["reviewed_at"] for transaction_id in ids[1:]
    )
