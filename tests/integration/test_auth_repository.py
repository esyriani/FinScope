"""Integration tests for authentication repository helpers."""

import pytest

from finance_app.core.constants import USER_ROLE_EDITOR
from finance_app.modules.auth import repository as auth_repository
from finance_app.modules.auth.service import hash_password, utc_now


def test_auth_repository_creates_users_and_user_settings(core_conn):
    """Verify Core auth repository helpers persist users and settings."""
    user_id = auth_repository.insert_user(
        core_conn,
        "repoeditor",
        hash_password("EditorPass123!"),
        USER_ROLE_EDITOR,
        must_change_password=True,
        now=utc_now(),
    )
    auth_repository.upsert_user_setting(core_conn, user_id, "theme_mode", "light", utc_now())
    auth_repository.upsert_user_setting(core_conn, user_id, "theme_mode", "dark", utc_now())

    user = auth_repository.get_user_by_username(core_conn, "RepoEditor")
    settings = auth_repository.get_user_settings(core_conn, user_id)

    assert user["id"] == user_id
    assert user["display_name"] == "repoeditor"
    assert user["password_hash"] != "EditorPass123!"
    assert user["must_change_password"] == 1
    assert settings["theme_mode"] == "dark"


def test_auth_repository_uses_database_username_key(core_conn):
    """Verify username lookups follow the database uniqueness key."""
    owner = auth_repository.get_user_by_username(core_conn, " OWNER ")

    assert owner["username"] == "owner"
    assert auth_repository.username_exists(core_conn, " OWNER ")


def test_auth_repository_writes_are_transactional(core_conn):
    """Verify auth repository writes roll back with the surrounding transaction."""
    with pytest.raises(RuntimeError):
        with core_conn.begin_nested():
            auth_repository.insert_user(
                core_conn,
                "rollbackuser",
                hash_password("RollbackPass123!"),
                USER_ROLE_EDITOR,
                must_change_password=False,
                now=utc_now(),
            )
            raise RuntimeError("rollback")

    assert auth_repository.get_user_by_username(core_conn, "rollbackuser") is None
