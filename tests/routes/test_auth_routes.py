"""Route tests for authentication and authorization."""

from sqlalchemy import text
from finance_app.core.constants import USER_ROLE_EDITOR, USER_ROLE_OWNER, USER_ROLE_VIEWER
from finance_app.core.csrf import CSRF_FIELD_NAME
from finance_app.core.filters import format_datetime
from finance_app.modules.auth import repository as auth_repository
from finance_app.modules.auth.service import create_managed_user, hash_password, utc_now
from tests.support.html import (
    assert_input,
    assert_no_element,
    assert_not_visible_text,
    assert_visible_text,
)
from tests.support.web import set_csrf_token

VIEWER_PASSWORD = "ViewerPass123!"


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
    return (
        conn.execute(
            text("""
        SELECT *
        FROM users
        WHERE username = :p0
        """),
            {"p0": username},
        )
        .mappings()
        .fetchone()
    )


def test_anonymous_routes_redirect_to_login(anonymous_client):
    """Verify protected pages require authentication after owner bootstrap."""
    response = anonymous_client.get("/dashboard")

    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_first_run_bootstrap_creates_owner(anonymous_client, core_conn):
    """Verify the first-run bootstrap creates the initial owner account."""
    core_conn.execute(text("DELETE FROM audit_log"))
    core_conn.execute(text("DELETE FROM users"))
    core_conn.commit()

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

    owner = user_by_username(core_conn, "firstowner")
    assert response.status_code == 302
    assert owner["role"] == USER_ROLE_OWNER
    assert owner["display_name"] == "First Owner"
    assert owner["is_active"] == 1
    assert owner["must_change_password"] == 0
    assert owner["password_hash"] != "OwnerPass123!"


def test_login_success_failure_logout_and_lockout(client, anonymous_client, core_conn):
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
    assert_visible_text(failed_response, "Invalid username or password.")
    assert user_by_username(core_conn, "owner")["failed_login_count"] == 1

    for _ in range(4):
        anonymous_client.post(
            "/login",
            data={
                CSRF_FIELD_NAME: set_csrf_token(anonymous_client),
                "username": "owner",
                "password": "wrong-password",
            },
        )
    locked_owner = user_by_username(core_conn, "owner")
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
    assert_visible_text(locked_response, "Invalid username or password.")

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

    assert_visible_text(response, "Invalid username or password.")


def test_owner_editor_and_viewer_authorization(client, editor_client, viewer_client, core_conn):
    """Verify role-specific route permissions are enforced in the backend."""
    tx_id = core_conn.execute(text("""
        INSERT INTO transactions (tx_date, description, amount, category, fingerprint)
        VALUES ('2026-01-02', 'Viewer blocked store', 12.34, 'UNKNOWN', 'viewer-blocked')
        """)).lastrowid
    core_conn.commit()

    assert client.get("/admin/users").status_code == 200
    assert editor_client.get("/upload").status_code == 200
    assert editor_client.get("/admin/users").status_code == 403
    assert viewer_client.get("/transactions").status_code == 200
    assert viewer_client.get("/upload").status_code == 403

    transactions_response = viewer_client.get("/transactions")
    recurring_response = viewer_client.get("/recurring")
    home_response = viewer_client.get("/")
    assert_no_element(transactions_response, "a", attrs={"href": f"/transactions/{tx_id}/verify"})
    assert_no_element(transactions_response, "form", attrs={"action": f"/transactions/{tx_id}/ignored"})
    assert_no_element(transactions_response, None, attrs={"data-row-edit-target": True})
    assert_not_visible_text(transactions_response, "Categorize transaction")
    assert_no_element(recurring_response, None, attrs={"data-recurring-confirm-action": True})
    assert_no_element(recurring_response, None, attrs={"data-recurring-ignore-action": True})
    assert_no_element(recurring_response, None, attrs={"data-recurring-edit-action": True})
    assert_no_element(recurring_response, None, attrs={"data-recurring-save-edit-action": True})
    assert_no_element(home_response, "a", attrs={"href": "/upload"})
    assert_no_element(home_response, "a", attrs={"href": "/rules"})
    assert_no_element(home_response, "a", attrs={"href": "/jobs"})
    assert_no_element(home_response, "a", attrs={"href": "/review"})

    blocked = viewer_client.post(
        f"/transactions/{tx_id}/ignored",
        data={CSRF_FIELD_NAME: set_csrf_token(viewer_client), "ignored": "1"},
    )
    ignored = (
        core_conn.execute(text("SELECT ignored FROM transactions WHERE id = :p0"), {"p0": tx_id})
        .fetchone()
        ._mapping["ignored"]
    )
    assert blocked.status_code == 403
    assert ignored == 0


def test_named_client_fixtures_exercise_authentication_guards(
    client,
    editor_client,
    viewer_client,
    stale_session_client,
    must_change_password_client,
    anonymous_client,
):
    """Verify named clients cover route-visible authentication states."""
    assert client.get("/admin/users").status_code == 200
    assert editor_client.get("/upload").status_code == 200
    assert editor_client.get("/admin/users").status_code == 403
    assert viewer_client.get("/transactions").status_code == 200
    assert viewer_client.get("/upload").status_code == 403
    assert stale_session_client.get("/dashboard").status_code == 200

    must_change_response = must_change_password_client.get("/dashboard", follow_redirects=False)
    anonymous_response = anonymous_client.get("/dashboard", follow_redirects=False)

    assert must_change_response.status_code == 302
    assert must_change_response.headers["Location"].endswith("/password")
    assert anonymous_response.status_code == 302
    assert "/login" in anonymous_response.headers["Location"]


def test_owner_user_management_and_last_owner_guard(client, core_conn):
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
    managed = user_by_username(core_conn, "managed")
    core_conn.execute(
        text("UPDATE users SET last_login_at = :p0 WHERE username = :p1"),
        {"p0": "2026-05-17T14:42:11Z", "p1": "managed"},
    )
    core_conn.commit()
    assert_visible_text(create_response, "Temporary password", "Managed Person")
    assert_not_visible_text(create_response, f"Temporary password for user {managed['id']}")
    assert managed["display_name"] == "Managed Person"
    assert managed["role"] == USER_ROLE_VIEWER
    assert managed["must_change_password"] == 1

    role_response = client.post(
        f"/admin/users/{managed['id']}/role",
        data={CSRF_FIELD_NAME: set_csrf_token(client), "role": USER_ROLE_EDITOR},
        follow_redirects=True,
    )
    assert_visible_text(role_response, "User role updated.")
    assert user_by_username(core_conn, "managed")["role"] == USER_ROLE_EDITOR

    deactivate_response = client.post(
        f"/admin/users/{managed['id']}/deactivate",
        data={CSRF_FIELD_NAME: set_csrf_token(client)},
        follow_redirects=True,
    )
    assert_visible_text(deactivate_response, "User deactivated.")
    assert user_by_username(core_conn, "managed")["is_active"] == 0

    reactivate_response = client.post(
        f"/admin/users/{managed['id']}/reactivate",
        data={CSRF_FIELD_NAME: set_csrf_token(client)},
        follow_redirects=True,
    )
    assert_visible_text(reactivate_response, "User reactivated.")
    assert user_by_username(core_conn, "managed")["is_active"] == 1

    owner = user_by_username(core_conn, "owner")
    owner_response = client.post(
        f"/admin/users/{owner['id']}/deactivate",
        data={CSRF_FIELD_NAME: set_csrf_token(client)},
        follow_redirects=True,
    )
    assert_visible_text(owner_response, "The last active owner cannot be changed.")
    assert user_by_username(core_conn, "owner")["is_active"] == 1

    users_response = client.get("/admin/users")
    assert_visible_text(
        users_response,
        "Owner password must be changed from the Account page.",
        format_datetime("2026-05-17T14:42:11Z"),
    )
    assert_no_element(users_response, "a", attrs={"href": f"/admin/users/{owner['id']}/reset-password"})
    assert_no_element(users_response, "a", attrs={"href": f"/admin/users/{owner['id']}/deactivate"})
    assert_no_element(users_response, "input", attrs={"value": "owner"})
    assert_input(users_response, name="role", value="editor")
    assert_input(users_response, name="role", value="viewer")

    owner_reset_response = client.post(
        f"/admin/users/{owner['id']}/reset-password",
        data={CSRF_FIELD_NAME: set_csrf_token(client)},
        follow_redirects=True,
    )
    assert_visible_text(owner_reset_response, "Owner password must be changed from the Account page.")

    owner_role_response = client.post(
        f"/admin/users/{owner['id']}/role",
        data={CSRF_FIELD_NAME: set_csrf_token(client), "role": USER_ROLE_EDITOR},
        follow_redirects=True,
    )
    assert_visible_text(owner_role_response, "Owner role cannot be changed.")
    assert user_by_username(core_conn, "owner")["role"] == USER_ROLE_OWNER


def test_owner_can_hand_off_ownership_to_active_user(client, core_conn):
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
    owner = user_by_username(core_conn, "owner")
    successor = user_by_username(core_conn, "successor")

    assert_visible_text(create_response, "Hand off ownership", "Confirm ownership hand-off")
    assert_no_element(create_response, "a", attrs={"href": f"/admin/users/{owner['id']}/deactivate"})

    response = client.post(
        "/admin/users/handoff-ownership",
        data={
            CSRF_FIELD_NAME: set_csrf_token(client),
            "target_user_id": str(successor["id"]),
        },
        follow_redirects=True,
    )

    assert_visible_text(response, "Ownership handed off. Your account is now Viewer.")
    assert user_by_username(core_conn, "owner")["role"] == USER_ROLE_VIEWER
    assert user_by_username(core_conn, "successor")["role"] == USER_ROLE_OWNER
    assert client.get("/admin/users").status_code == 403


def test_must_change_password_flow(anonymous_client, core_conn):
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
    changed_user = user_by_username(core_conn, "needschange")

    assert user["id"] == changed_user["id"]
    assert login_response.status_code == 200
    assert_visible_text(login_response, "Change temporary password", "Needs Change")
    assert dashboard_response.status_code == 302
    assert "/login" in dashboard_response.headers["Location"]
    assert password_response.status_code == 302
    assert password_response.headers["Location"].endswith("/")
    assert changed_user["must_change_password"] == 0


def test_account_page_updates_display_name_and_password(client, core_conn):
    """Verify users can manage display name and password from Account."""
    account_response = client.get("/account")
    assert account_response.status_code == 200
    assert_visible_text(account_response, "owner")

    display_response = client.post(
        "/account",
        data={
            CSRF_FIELD_NAME: set_csrf_token(client),
            "account_action": "display_name",
            "display_name": "Eugene",
        },
        follow_redirects=True,
    )
    assert_visible_text(display_response, "Display name updated.")
    assert user_by_username(core_conn, "owner")["display_name"] == "Eugene"

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
    assert_visible_text(password_response, "Password changed.")


def test_home_greets_user_by_display_name_and_shows_shared_context(client, core_conn):
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
    assert response.status_code == 200
    assert_visible_text(
        response,
        "Eugene",
        "What needs attention, what changed, and where to act next.",
        "Shared with Edith",
    )
