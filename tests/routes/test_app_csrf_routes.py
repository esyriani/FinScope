"""CSRF protection tests against real application routes."""

from sqlalchemy import text
from tests.support.html import assert_visible_text
from tests.support.web import set_csrf_token

from finance_app.core.csrf import CSRF_FIELD_NAME, CSRF_HEADER_NAME
from finance_app.modules.recurring.patterns import get_recurring_pattern


def category_count(conn, name):
    """Return how many categories exist with the supplied name."""
    return (
        conn.execute(
            text("""
        SELECT COUNT(*) AS count
        FROM categories
        WHERE name = :p0
        """),
            {"p0": name},
        )
        .fetchone()
        ._mapping["count"]
    )


def test_real_app_form_post_rejects_missing_csrf_token(client, core_conn):
    """Verify real form routes reject missing CSRF tokens before mutation."""
    response = client.post(
        "/taxonomy/categories/create",
        data={"name": "CSRF Missing Category"},
    )

    assert response.status_code == 403
    assert category_count(core_conn, "CSRF Missing Category") == 0


def test_real_app_form_post_rejects_invalid_csrf_token(client, core_conn):
    """Verify real form routes reject invalid CSRF tokens before mutation."""
    set_csrf_token(client, "expected-token")

    response = client.post(
        "/taxonomy/categories/create",
        data={
            CSRF_FIELD_NAME: "wrong-token",
            "name": "CSRF Invalid Category",
        },
    )

    assert response.status_code == 403
    assert category_count(core_conn, "CSRF Invalid Category") == 0


def test_real_app_form_post_accepts_valid_csrf_token(client, core_conn):
    """Verify real form routes accept valid CSRF tokens and continue to the route."""
    response = client.post(
        "/taxonomy/categories/create",
        data={
            CSRF_FIELD_NAME: set_csrf_token(client),
            "name": "CSRF Valid Category",
            "description": "Created through a protected route.",
            "instruction": "Use only in CSRF tests.",
        },
        follow_redirects=True,
    )

    row = core_conn.execute(text("""
        SELECT description, instruction
        FROM categories
        WHERE name = 'CSRF Valid Category'
    """)).fetchone()
    assert response.status_code == 200
    assert_visible_text(response, "Category saved: CSRF Valid Category")
    assert tuple(row) == (
        "Created through a protected route.",
        "Use only in CSRF tests.",
    )


def test_real_app_json_post_rejects_missing_csrf_token_as_json(client, core_conn):
    """Verify real JSON routes return JSON CSRF errors before mutation."""
    response = client.post(
        "/recurring/patterns/confirm",
        json={
            "patternKey": "CSRF JSON::spending",
            "merchant": "CSRF JSON",
            "type": "spending",
        },
    )

    assert response.status_code == 403
    assert response.get_json() == {
        "ok": False,
        "message": "Invalid CSRF token.",
    }
    assert get_recurring_pattern(core_conn, "CSRF JSON::spending") is None


def test_real_app_json_post_accepts_valid_csrf_header(client, core_conn):
    """Verify real JSON routes accept valid CSRF headers and mutate state."""
    response = client.post(
        "/recurring/patterns/confirm",
        json={
            "patternKey": "CSRF Header::spending",
            "merchant": "CSRF Header",
            "type": "spending",
        },
        headers={CSRF_HEADER_NAME: set_csrf_token(client)},
    )

    pattern = get_recurring_pattern(core_conn, "CSRF Header::spending")
    assert response.status_code == 200
    assert response.get_json() == {
        "ok": True,
        "userStatus": "confirmed",
        "active": 1,
    }
    assert pattern["user_status"] == "confirmed"
    assert pattern["merchant_id"] is None
    assert pattern["active"] == 1


def test_real_app_fetch_form_post_returns_json_csrf_error(client, core_conn):
    """Verify fetch-style form posts get JSON CSRF errors from real routes."""
    response = client.post(
        "/taxonomy/categories/create",
        data={"name": "CSRF Fetch Category"},
        headers={"X-Requested-With": "fetch"},
    )

    assert response.status_code == 403
    assert response.get_json() == {
        "ok": False,
        "message": "Invalid CSRF token.",
    }
    assert category_count(core_conn, "CSRF Fetch Category") == 0
