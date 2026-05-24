"""Route tests for authentication and authorization."""

from finance_app.core.constants import USER_ROLE_EDITOR, USER_ROLE_OWNER, USER_ROLE_VIEWER
from finance_app.core.csrf import CSRF_FIELD_NAME, CSRF_SESSION_KEY
from finance_app.core.filters import format_datetime
from finance_app.modules.auth import repository as auth_repository
from finance_app.modules.auth.service import create_managed_user, hash_password, utc_now


EDITOR_PASSWORD = "EditorPass123!"
VIEWER_PASSWORD = "ViewerPass123!"


def set_csrf_token(client, token="test-csrf-token"):
    """Store a CSRF token in the test client's session."""
    with client.session_transaction() as session:
        session[CSRF_SESSION_KEY] = token
    return token


def login_session(client, user_id):
    """Authenticate a test client as a persisted user."""
    with client.session_transaction() as session:
        session["_user_id"] = str(user_id)
        session["_fresh"] = True


def create_test_user(conn, username, role, password):
    """Insert an active test user with a known password."""
    return auth_repository.insert_user(
        conn,
        username,
        hash_password(password),
        role,
        must_change_password=False,
        now=utc_now(),
    )


def user_by_username(conn, username):
    """Return one user row by username for route assertions."""
    return conn.execute(
        """
        SELECT *
        FROM users
        WHERE username = ?
        """,
        (username,),
    ).fetchone()


def test_anonymous_routes_redirect_to_login(anonymous_client):
    """Verify protected pages require authentication after owner bootstrap."""
    response = anonymous_client.get("/dashboard")

    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_first_run_bootstrap_creates_owner(anonymous_client, db_conn):
    """Verify the first-run bootstrap creates the initial owner account."""
    db_conn.execute("DELETE FROM audit_log")
    db_conn.execute("DELETE FROM users")
    db_conn.commit()

    response = anonymous_client.post(
        "/auth/bootstrap",
        data={
            CSRF_FIELD_NAME: set_csrf_token(anonymous_client),
            "username": "firstowner",
            "display_name": "First Owner",
            "password": "OwnerPass123!",
            "confirm_password": "OwnerPass123!",
        },
        follow_redirects=False,
    )

    owner = user_by_username(db_conn, "firstowner")
    assert response.status_code == 302
    assert owner["role"] == USER_ROLE_OWNER
    assert owner["display_name"] == "First Owner"
    assert owner["is_active"] == 1
    assert owner["must_change_password"] == 0
    assert owner["password_hash"] != "OwnerPass123!"


def test_login_success_failure_logout_and_lockout(client, anonymous_client, db_conn):
    """Verify login, generic failures, logout, and temporary lockout behavior."""
    failed_response = anonymous_client.post(
        "/login",
        data={
            CSRF_FIELD_NAME: set_csrf_token(anonymous_client),
            "username": "owner",
            "password": "wrong-password",
        },
        follow_redirects=True,
    )
    assert b"Invalid username or password." in failed_response.data
    assert user_by_username(db_conn, "owner")["failed_login_count"] == 1

    for _ in range(4):
        anonymous_client.post(
            "/login",
            data={
                CSRF_FIELD_NAME: set_csrf_token(anonymous_client),
                "username": "owner",
                "password": "wrong-password",
            },
        )
    locked_owner = user_by_username(db_conn, "owner")
    assert locked_owner["failed_login_count"] == 5
    assert locked_owner["locked_until"] is not None

    locked_response = anonymous_client.post(
        "/login",
        data={
            CSRF_FIELD_NAME: set_csrf_token(anonymous_client),
            "username": "owner",
            "password": "OwnerPass123!",
        },
        follow_redirects=True,
    )
    assert b"Invalid username or password." in locked_response.data

    logout_response = client.post(
        "/logout",
        data={CSRF_FIELD_NAME: set_csrf_token(client)},
        follow_redirects=False,
    )
    assert logout_response.status_code == 302
    assert "/login" in logout_response.headers["Location"]


def test_inactive_user_cannot_login(anonymous_client, core_conn):
    """Verify inactive users cannot authenticate."""
    user_id = create_test_user(core_conn, "inactive", USER_ROLE_VIEWER, VIEWER_PASSWORD)
    auth_repository.update_user_active(core_conn, user_id, False, utc_now())
    core_conn.commit()

    response = anonymous_client.post(
        "/login",
        data={
            CSRF_FIELD_NAME: set_csrf_token(anonymous_client),
            "username": "inactive",
            "password": VIEWER_PASSWORD,
        },
        follow_redirects=True,
    )

    assert b"Invalid username or password." in response.data


def test_owner_editor_and_viewer_authorization(client, app, core_conn, db_conn):
    """Verify role-specific route permissions are enforced in the backend."""
    editor_id = create_test_user(core_conn, "editor", USER_ROLE_EDITOR, EDITOR_PASSWORD)
    viewer_id = create_test_user(core_conn, "viewer", USER_ROLE_VIEWER, VIEWER_PASSWORD)
    core_conn.commit()

    editor_client = app.test_client()
    login_session(editor_client, editor_id)
    viewer_client = app.test_client()
    login_session(viewer_client, viewer_id)

    tx_id = db_conn.execute(
        """
        INSERT INTO transactions (tx_date, description, amount, category, fingerprint)
        VALUES ('2026-01-02', 'Viewer blocked store', 12.34, 'UNKNOWN', 'viewer-blocked')
        """
    ).lastrowid
    db_conn.commit()

    assert client.get("/admin/users").status_code == 200
    assert editor_client.get("/upload").status_code == 200
    assert editor_client.get("/admin/users").status_code == 403
    assert viewer_client.get("/transactions").status_code == 200
    assert viewer_client.get("/upload").status_code == 403

    transactions_html = viewer_client.get("/transactions").get_data(as_text=True)
    recurring_html = viewer_client.get("/recurring").get_data(as_text=True)
    home_html = viewer_client.get("/").get_data(as_text=True)
    assert "Approve" not in transactions_html
    assert "/ignored" not in transactions_html
    assert "data-row-edit-target" not in transactions_html
    assert "Categorize transaction" not in transactions_html
    assert "data-recurring-confirm-action" not in recurring_html
    assert "data-recurring-ignore-action" not in recurring_html
    assert "data-recurring-edit-action" not in recurring_html
    assert "data-recurring-save-edit-action" not in recurring_html
    assert 'href="/upload"' not in home_html
    assert 'href="/rules"' not in home_html
    assert 'href="/jobs"' not in home_html
    assert 'href="/review"' not in home_html

    blocked = viewer_client.post(
        f"/transactions/{tx_id}/ignored",
        data={CSRF_FIELD_NAME: set_csrf_token(viewer_client), "ignored": "1"},
    )
    ignored = db_conn.execute(
        "SELECT ignored FROM transactions WHERE id = ?",
        (tx_id,),
    ).fetchone()["ignored"]
    assert blocked.status_code == 403
    assert ignored == 0


def test_owner_user_management_and_last_owner_guard(client, db_conn):
    """Verify owner-managed user lifecycle routes and last-owner protection."""
    create_response = client.post(
        "/admin/users/create",
        data={
            CSRF_FIELD_NAME: set_csrf_token(client),
            "username": "managed",
            "display_name": "Managed Person",
            "role": USER_ROLE_VIEWER,
        },
        follow_redirects=True,
    )
    managed = user_by_username(db_conn, "managed")
    db_conn.execute(
        "UPDATE users SET last_login_at = ? WHERE username = ?",
        ("2026-05-17T14:42:11Z", "managed"),
    )
    db_conn.commit()
    assert b"Temporary password" in create_response.data
    assert b"Managed Person" in create_response.data
    assert f"Temporary password for user {managed['id']}".encode() not in create_response.data
    assert managed["display_name"] == "Managed Person"
    assert managed["role"] == USER_ROLE_VIEWER
    assert managed["must_change_password"] == 1

    role_response = client.post(
        f"/admin/users/{managed['id']}/role",
        data={CSRF_FIELD_NAME: set_csrf_token(client), "role": USER_ROLE_EDITOR},
        follow_redirects=True,
    )
    assert b"User role updated." in role_response.data
    assert user_by_username(db_conn, "managed")["role"] == USER_ROLE_EDITOR

    deactivate_response = client.post(
        f"/admin/users/{managed['id']}/deactivate",
        data={CSRF_FIELD_NAME: set_csrf_token(client)},
        follow_redirects=True,
    )
    assert b"User deactivated." in deactivate_response.data
    assert user_by_username(db_conn, "managed")["is_active"] == 0

    reactivate_response = client.post(
        f"/admin/users/{managed['id']}/reactivate",
        data={CSRF_FIELD_NAME: set_csrf_token(client)},
        follow_redirects=True,
    )
    assert b"User reactivated." in reactivate_response.data
    assert user_by_username(db_conn, "managed")["is_active"] == 1

    owner = user_by_username(db_conn, "owner")
    owner_response = client.post(
        f"/admin/users/{owner['id']}/deactivate",
        data={CSRF_FIELD_NAME: set_csrf_token(client)},
        follow_redirects=True,
    )
    assert b"The last active owner cannot be changed." in owner_response.data
    assert user_by_username(db_conn, "owner")["is_active"] == 1

    users_html = client.get("/admin/users").get_data(as_text=True)
    assert "Owner password must be changed from the Account page." in users_html
    assert format_datetime("2026-05-17T14:42:11Z") in users_html
    assert f"/admin/users/{owner['id']}/reset-password" not in users_html
    assert f"/admin/users/{owner['id']}/deactivate" not in users_html
    assert 'value="owner"' not in users_html
    assert 'value="editor"' in users_html
    assert 'value="viewer"' in users_html

    owner_reset_response = client.post(
        f"/admin/users/{owner['id']}/reset-password",
        data={CSRF_FIELD_NAME: set_csrf_token(client)},
        follow_redirects=True,
    )
    assert b"Owner password must be changed from the Account page." in owner_reset_response.data

    owner_role_response = client.post(
        f"/admin/users/{owner['id']}/role",
        data={CSRF_FIELD_NAME: set_csrf_token(client), "role": USER_ROLE_EDITOR},
        follow_redirects=True,
    )
    assert b"Owner role cannot be changed." in owner_role_response.data
    assert user_by_username(db_conn, "owner")["role"] == USER_ROLE_OWNER


def test_owner_can_hand_off_ownership_to_active_user(client, db_conn):
    """Verify ownership hand-off promotes one user and demotes the old owner."""
    create_response = client.post(
        "/admin/users/create",
        data={
            CSRF_FIELD_NAME: set_csrf_token(client),
            "username": "successor",
            "display_name": "Successor",
            "role": USER_ROLE_EDITOR,
        },
        follow_redirects=True,
    )
    owner = user_by_username(db_conn, "owner")
    successor = user_by_username(db_conn, "successor")
    users_html = create_response.get_data(as_text=True)

    assert "Hand off ownership" in users_html
    assert "Confirm ownership hand-off" in users_html
    assert f"/admin/users/{owner['id']}/deactivate" not in users_html

    response = client.post(
        "/admin/users/handoff-ownership",
        data={
            CSRF_FIELD_NAME: set_csrf_token(client),
            "target_user_id": str(successor["id"]),
        },
        follow_redirects=True,
    )

    assert b"Ownership handed off. Your account is now Viewer." in response.data
    assert user_by_username(db_conn, "owner")["role"] == USER_ROLE_VIEWER
    assert user_by_username(db_conn, "successor")["role"] == USER_ROLE_OWNER
    assert client.get("/admin/users").status_code == 403


def test_must_change_password_flow(anonymous_client, db_conn):
    """Verify temporary passwords force a password change before app access."""
    user, temporary_password = create_managed_user("needschange", USER_ROLE_VIEWER, display_name="Needs Change")

    login_response = anonymous_client.post(
        "/login",
        data={
            CSRF_FIELD_NAME: set_csrf_token(anonymous_client),
            "username": "needschange",
            "password": temporary_password,
        },
        follow_redirects=False,
    )
    dashboard_response = anonymous_client.get("/dashboard", follow_redirects=False)
    password_response = anonymous_client.post(
        "/login",
        data={
            CSRF_FIELD_NAME: set_csrf_token(anonymous_client),
            "auth_action": "force_password_change",
            "current_password": temporary_password,
            "new_password": "ChangedPass123!",
            "confirm_password": "ChangedPass123!",
        },
        follow_redirects=False,
    )
    changed_user = user_by_username(db_conn, "needschange")

    assert user["id"] == changed_user["id"]
    assert login_response.status_code == 200
    assert b"Change temporary password" in login_response.data
    assert b"Needs Change" in login_response.data
    assert dashboard_response.status_code == 302
    assert "/login" in dashboard_response.headers["Location"]
    assert password_response.status_code == 302
    assert password_response.headers["Location"].endswith("/")
    assert changed_user["must_change_password"] == 0


def test_account_page_updates_display_name_and_password(client, db_conn):
    """Verify users can manage display name and password from Account."""
    account_response = client.get("/account")
    assert account_response.status_code == 200
    assert b"owner" in account_response.data

    display_response = client.post(
        "/account",
        data={
            CSRF_FIELD_NAME: set_csrf_token(client),
            "account_action": "display_name",
            "display_name": "Eugene",
        },
        follow_redirects=True,
    )
    assert b"Display name updated." in display_response.data
    assert user_by_username(db_conn, "owner")["display_name"] == "Eugene"

    password_response = client.post(
        "/account",
        data={
            CSRF_FIELD_NAME: set_csrf_token(client),
            "account_action": "password",
            "current_password": "OwnerPass123!",
            "new_password": "OwnerPass456!",
            "confirm_password": "OwnerPass456!",
        },
        follow_redirects=True,
    )
    assert b"Password changed." in password_response.data


def test_home_greets_user_by_display_name_and_shows_shared_context(client, db_conn, core_conn):
    """Verify Home title copy uses display names and subtle shared-access context."""
    owner = auth_repository.get_user_by_username(core_conn, "owner")
    auth_repository.update_display_name(
        core_conn,
        owner["id"],
        "Eugene",
        utc_now(),
    )
    auth_repository.insert_user(
        core_conn,
        "edith",
        hash_password("EdithPass123!"),
        USER_ROLE_VIEWER,
        must_change_password=False,
        now=utc_now(),
        display_name="Edith",
    )
    core_conn.commit()

    response = client.get("/")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Eugene" in html
    assert "What needs attention, what changed, and where to act next." in html
    assert "Shared with Edith" in html
