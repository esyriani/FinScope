"""Tests for shared Flask client fixtures.

Verifies that named client fixtures create explicit authentication states for
route tests without each test hand-building users and Flask-Login sessions.
"""

from finance_app.core.constants import USER_ROLE_EDITOR, USER_ROLE_OWNER, USER_ROLE_VIEWER
from finance_app.modules.auth import repository as auth_repository


def session_snapshot(client):
    """Return a plain dictionary copy of a Flask test client's session."""
    with client.session_transaction() as session:
        return dict(session)


def assert_authenticated_client_state(conn, client, *, role, fresh=True, must_change_password=False):
    """Assert a named authenticated client matches its persisted user state."""
    session = session_snapshot(client)
    user = auth_repository.get_user_by_id(conn, client.test_user["id"])

    assert session["_user_id"] == str(client.test_user["id"])
    assert session["_fresh"] is fresh
    assert user["role"] == role
    assert user["is_active"] == 1
    assert user["must_change_password"] == int(must_change_password)


def test_named_client_fixtures_expose_expected_authentication_states(
    owner_client,
    editor_client,
    viewer_client,
    stale_session_client,
    must_change_password_client,
    anonymous_client,
    core_conn,
):
    """Verify shared client fixtures cover owner, editor, viewer, and edge states."""
    assert_authenticated_client_state(core_conn, owner_client, role=USER_ROLE_OWNER)
    assert_authenticated_client_state(core_conn, editor_client, role=USER_ROLE_EDITOR)
    assert_authenticated_client_state(core_conn, viewer_client, role=USER_ROLE_VIEWER)
    assert_authenticated_client_state(
        core_conn,
        stale_session_client,
        role=USER_ROLE_OWNER,
        fresh=False,
    )
    assert_authenticated_client_state(
        core_conn,
        must_change_password_client,
        role=USER_ROLE_VIEWER,
        must_change_password=True,
    )

    anonymous_session = session_snapshot(anonymous_client)
    assert "_user_id" not in anonymous_session
    assert "_fresh" not in anonymous_session
