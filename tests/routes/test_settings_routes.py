"""Route tests for the settings feature."""

from finance_app.core.csrf import CSRF_FIELD_NAME, CSRF_SESSION_KEY
from finance_app.core.constants import USER_ROLE_VIEWER
from finance_app.modules.auth import repository as auth_repository
from finance_app.modules.auth.service import hash_password, utc_now
from finance_app.modules.settings import service as settings_service
from finance_app.modules.settings.runtime import get_all_settings, get_statement_type_options, get_unknown_category


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


def settings_form_data(conn, **overrides):
    """Build a complete settings form payload with overridable fields."""
    statement_types = get_statement_type_options(conn)
    data = {
        "default_table_page_size": "25",
        "comparison_max_years": "3",
        "home_top_category_limit": "7",
        "merchant_table_limit": "11",
        "rule_preview_limit": "9",
        "llm_confidence_threshold": "0.75",
        "verify_threshold": "0.90",
        "openai_model": "gpt-4o-mini",
        "recurrence_minimum_occurrences": "4",
        "recurrence_date_tolerance_days": "6",
        "recurrence_amount_tolerance_absolute": "12.5",
        "recurrence_amount_tolerance_percent": "0.20",
        "recurrence_missed_cycles_before_inactive": "3",
        "theme_mode": "dark",
        "ui_language": "en",
        "statement_type_ids": [str(row["id"]) for row in statement_types],
        "statement_type_names": [row["name"] for row in statement_types],
        "statement_type_parser_types": [row["parser_type"] for row in statement_types],
    }
    data.update(overrides)
    return data


def user_settings(conn, username="owner"):
    """Return user-specific settings for a test username."""
    rows = conn.execute(
        """
        SELECT us.key, us.value
        FROM user_settings us
        JOIN users u ON u.id = us.user_id
        WHERE u.username = ?
        """,
        (username,),
    ).fetchall()
    return {row["key"]: row["value"] for row in rows}


def test_settings_page_uses_dark_theme_by_default(client):
    """Verify the settings page renders with dark mode selected for a new database."""
    response = client.get("/settings")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert 'data-bs-theme="dark"' in html
    assert 'id="theme_mode_dark"' in html
    dark_input = html[
        html.index('id="theme_mode_dark"') : html.index(
            '<label class="theme-mode-option" for="theme_mode_dark">'
        )
    ]
    assert "checked" in dark_input


def test_settings_post_saves_runtime_settings_theme_recurrence_and_statement_types(client, db_conn):
    """Verify that settings POST persists runtime settings and statement type edits."""
    active_types = get_statement_type_options(db_conn)
    keep_type = active_types[0]
    form = settings_form_data(
        db_conn,
        statement_type_ids=[str(keep_type["id"]), ""],
        statement_type_names=["Everyday checking", "Corporate card"],
        statement_type_parser_types=["bank_account", "credit_card"],
    )

    response = client.post(
        "/settings",
        data={CSRF_FIELD_NAME: set_csrf_token(client), **form},
        follow_redirects=True,
    )

    settings = get_all_settings(db_conn)
    owner_settings = user_settings(db_conn)
    active_names = {
        row["name"]: row["parser_type"]
        for row in get_statement_type_options(db_conn)
    }
    all_statement_types = {
        row["name"]: row["active"]
        for row in get_statement_type_options(db_conn, include_inactive=True)
    }
    assert response.status_code == 200
    assert b"Settings saved." in response.data
    assert owner_settings["default_table_page_size"] == "25"
    assert owner_settings["comparison_max_years"] == "3"
    assert owner_settings["home_top_category_limit"] == "7"
    assert owner_settings["merchant_table_limit"] == "11"
    assert owner_settings["rule_preview_limit"] == "9"
    assert settings["llm_confidence_threshold"] == "0.75"
    assert settings["verify_threshold"] == "0.90"
    assert settings["openai_model"] == "gpt-4o-mini"
    assert settings["recurrence_minimum_occurrences"] == "4"
    assert settings["recurrence_date_tolerance_days"] == "6"
    assert settings["recurrence_amount_tolerance_absolute"] == "12.5"
    assert settings["recurrence_amount_tolerance_percent"] == "0.20"
    assert settings["recurrence_missed_cycles_before_inactive"] == "3"
    assert owner_settings["theme_mode"] == "dark"
    assert owner_settings["ui_language"] == "en"
    assert active_names == {
        "Corporate card": "credit_card",
        "Everyday checking": "bank_account",
    }
    assert all_statement_types["Everyday checking"] == 1
    assert all_statement_types["Corporate card"] == 1
    assert any(
        row["active"] == 0
        for row in get_statement_type_options(db_conn, include_inactive=True)
        if row["name"] not in active_names
    )


def test_settings_post_rejects_invalid_numeric_values_without_partial_save(client, db_conn):
    """Verify invalid numeric settings flash errors and do not persist partial changes."""
    original_settings = get_all_settings(db_conn)
    original_user_settings = user_settings(db_conn)
    form = settings_form_data(db_conn, comparison_max_years="1")

    response = client.post(
        "/settings",
        data={CSRF_FIELD_NAME: set_csrf_token(client), **form},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"Comparison default years must be at least 2." in response.data
    assert get_all_settings(db_conn)["theme_mode"] == original_settings["theme_mode"]
    assert get_all_settings(db_conn)["default_table_page_size"] == original_settings["default_table_page_size"]
    assert user_settings(db_conn) == original_user_settings


def test_settings_post_ignores_unknown_category_override(client, db_conn):
    """Verify Unknown remains a fixed built-in category outside runtime settings."""
    form = settings_form_data(db_conn, unknown_category="UNCATEGORIZED", theme_mode="light")

    response = client.post(
        "/settings",
        data={CSRF_FIELD_NAME: set_csrf_token(client), **form},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"Settings saved." in response.data
    assert get_unknown_category(db_conn) == "UNKNOWN"
    assert user_settings(db_conn)["theme_mode"] == "light"


def test_settings_post_saves_ui_language_and_renders_french(client, db_conn):
    """Verify the UI language setting localizes shared and settings labels."""
    form = settings_form_data(db_conn, ui_language="fr")

    response = client.post(
        "/settings",
        data={CSRF_FIELD_NAME: set_csrf_token(client), **form},
        follow_redirects=True,
    )
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert 'lang="fr"' in html
    assert "Paramètres enregistrés." in html
    assert "Langue de l&#39;interface" in html
    assert "Finances personnelles" in html
    assert user_settings(db_conn)["ui_language"] == "fr"


def test_viewer_can_only_save_own_general_settings(app, db_conn, core_conn):
    """Verify viewers can save personal General settings but not global settings."""
    viewer_id = auth_repository.insert_user(
        core_conn,
        "settingsviewer",
        hash_password("ViewerPass123!"),
        USER_ROLE_VIEWER,
        must_change_password=False,
        now=utc_now(),
    )
    core_conn.commit()
    viewer_client = app.test_client()
    login_session(viewer_client, viewer_id)
    original_global_settings = get_all_settings(db_conn)
    form = settings_form_data(
        db_conn,
        theme_mode="light",
        ui_language="fr",
        openai_model="unauthorized-model",
        recurrence_minimum_occurrences="9",
    )

    response = viewer_client.post(
        "/settings",
        data={CSRF_FIELD_NAME: set_csrf_token(viewer_client), **form},
        follow_redirects=True,
    )
    validate_response = viewer_client.post(
        "/settings/openai-model/validate",
        data={CSRF_FIELD_NAME: set_csrf_token(viewer_client), **form},
    )

    viewer_settings = user_settings(db_conn, "settingsviewer")
    global_settings = get_all_settings(db_conn)
    assert response.status_code == 200
    assert viewer_settings["theme_mode"] == "light"
    assert viewer_settings["ui_language"] == "fr"
    assert global_settings["openai_model"] == original_global_settings["openai_model"]
    assert global_settings["recurrence_minimum_occurrences"] == original_global_settings["recurrence_minimum_occurrences"]
    assert validate_response.status_code == 403


def test_settings_post_rejects_duplicate_statement_type_names(client, db_conn):
    """Verify that statement type sync validation is surfaced by the route."""
    active_types = get_statement_type_options(db_conn)
    form = settings_form_data(
        db_conn,
        statement_type_ids=[str(active_types[0]["id"]), str(active_types[1]["id"])],
        statement_type_names=["Duplicate", " duplicate "],
        statement_type_parser_types=["bank_account", "credit_card"],
    )

    response = client.post(
        "/settings",
        data={CSRF_FIELD_NAME: set_csrf_token(client), **form},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"Statement type names must be unique." in response.data


def test_settings_post_rolls_back_when_statement_type_sync_fails(client, db_conn, monkeypatch):
    """Verify late settings-save failures do not persist earlier setting writes."""
    original_settings = get_all_settings(db_conn)
    original_user_settings = user_settings(db_conn)
    form = settings_form_data(db_conn, theme_mode="light", default_table_page_size="99")

    def fail_statement_sync(conn, rows):
        """Simulate a late validation failure after scalar settings were written."""
        del conn, rows
        raise ValueError("Statement type sync failed late.")

    monkeypatch.setattr(settings_service, "sync_statement_types", fail_statement_sync)

    response = client.post(
        "/settings",
        data={CSRF_FIELD_NAME: set_csrf_token(client), **form},
        follow_redirects=True,
    )

    settings = get_all_settings(db_conn)
    assert response.status_code == 200
    assert b"Statement type sync failed late." in response.data
    assert settings["theme_mode"] == original_settings["theme_mode"]
    assert settings["default_table_page_size"] == original_settings["default_table_page_size"]
    assert user_settings(db_conn) == original_user_settings
