"""Route tests for the settings feature."""

from sqlalchemy import text
from tests.support.html import assert_has_element, assert_visible_text
from tests.support.web import set_csrf_token

from finance_app.core.constants import USER_ROLE_VIEWER
from finance_app.core.csrf import CSRF_FIELD_NAME
from finance_app.modules.auth import repository as auth_repository
from finance_app.modules.auth.service import hash_password, utc_now
from finance_app.modules.settings import service as settings_service
from finance_app.modules.settings.runtime import get_all_settings, get_statement_type_options, get_unknown_category


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
        "comparison_insight_card_limit": "5",
        "home_top_category_limit": "7",
        "merchant_table_limit": "11",
        "rule_preview_limit": "9",
        "rule_audit_transaction_limit": "13",
        "llm_confidence_threshold": "0.75",
        "llm_review_threshold": "0.65",
        "verify_threshold": "0.90",
        "auto_llm_categorization_enabled": "1",
        "transaction_ai_rerun_enabled": "1",
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
    rows = (
        conn.execute(
            text("""
        SELECT us.key, us.value
        FROM user_settings us
        JOIN users u ON u.id = us.user_id
        WHERE u.username = :p0
        """),
            {"p0": username},
        )
        .mappings()
        .fetchall()
    )
    return {row["key"]: row["value"] for row in rows}


def test_settings_page_uses_dark_theme_by_default(client):
    """Verify the settings page renders with safe defaults for a new database."""
    response = client.get("/settings")

    assert response.status_code == 200
    assert_has_element(response, "html", attrs={"data-bs-theme": "dark"})
    assert_has_element(response, "button", attrs={"id": "settings-general-tab", "role": "tab"})
    assert_has_element(response, "button", attrs={"id": "settings-categorization-tab", "role": "tab"})
    assert_has_element(response, "section", attrs={"id": "settings-general", "role": "tabpanel"})
    assert_has_element(response, "input", attrs={"id": "theme_mode_dark", "checked": True})
    assert_has_element(response, "input", attrs={"id": "comparison_insight_card_limit"})
    assert_has_element(
        response,
        "input",
        attrs={"id": "auto_llm_categorization_enabled", "checked": False},
    )


def test_settings_post_saves_runtime_settings_theme_recurrence_and_statement_types(client, core_conn):
    """Verify that settings POST persists runtime settings and statement type edits."""
    active_types = get_statement_type_options(core_conn)
    keep_type = active_types[0]
    form = settings_form_data(
        core_conn,
        statement_type_ids=[str(keep_type["id"]), ""],
        statement_type_names=["Everyday checking", "Corporate card"],
        statement_type_parser_types=["bank_account", "credit_card"],
    )

    response = client.post(
        "/settings",
        data={CSRF_FIELD_NAME: set_csrf_token(client), **form},
        follow_redirects=True,
    )

    settings = get_all_settings(core_conn)
    owner_settings = user_settings(core_conn)
    active_names = {row["name"]: row["parser_type"] for row in get_statement_type_options(core_conn)}
    all_statement_types = {
        row["name"]: row["active"] for row in get_statement_type_options(core_conn, include_inactive=True)
    }
    assert response.status_code == 200
    assert_visible_text(response, "Settings saved.")
    assert owner_settings["default_table_page_size"] == "25"
    assert owner_settings["comparison_max_years"] == "3"
    assert owner_settings["comparison_insight_card_limit"] == "5"
    assert owner_settings["home_top_category_limit"] == "7"
    assert owner_settings["merchant_table_limit"] == "11"
    assert owner_settings["rule_preview_limit"] == "9"
    assert owner_settings["rule_audit_transaction_limit"] == "13"
    assert settings["llm_confidence_threshold"] == "0.75"
    assert settings["llm_review_threshold"] == "0.65"
    assert settings["verify_threshold"] == "0.90"
    assert settings["auto_llm_categorization_enabled"] == "1"
    assert settings["transaction_ai_rerun_enabled"] == "1"
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
        for row in get_statement_type_options(core_conn, include_inactive=True)
        if row["name"] not in active_names
    )


def test_settings_post_rejects_invalid_numeric_values_without_partial_save(client, core_conn):
    """Verify invalid numeric settings flash errors and do not persist partial changes."""
    original_settings = get_all_settings(core_conn)
    original_user_settings = user_settings(core_conn)
    form = settings_form_data(core_conn, comparison_max_years="1")

    response = client.post(
        "/settings",
        data={CSRF_FIELD_NAME: set_csrf_token(client), **form},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert_visible_text(response, "Comparison default years must be at least 2.")
    assert get_all_settings(core_conn)["theme_mode"] == original_settings["theme_mode"]
    assert get_all_settings(core_conn)["default_table_page_size"] == original_settings["default_table_page_size"]
    assert user_settings(core_conn) == original_user_settings


def test_settings_post_ignores_unknown_category_override(client, core_conn):
    """Verify Unknown remains a fixed built-in category outside runtime settings."""
    form = settings_form_data(core_conn, unknown_category="UNCATEGORIZED", theme_mode="light")

    response = client.post(
        "/settings",
        data={CSRF_FIELD_NAME: set_csrf_token(client), **form},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert_visible_text(response, "Settings saved.")
    assert get_unknown_category(core_conn) == "UNKNOWN"
    assert user_settings(core_conn)["theme_mode"] == "light"


def test_settings_post_can_disable_automatic_ai_queueing(client, core_conn):
    """Verify the owner can pause automatic AI categorization after imports."""
    form = settings_form_data(core_conn)
    form.pop("auto_llm_categorization_enabled")

    response = client.post(
        "/settings",
        data={CSRF_FIELD_NAME: set_csrf_token(client), **form},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert get_all_settings(core_conn)["auto_llm_categorization_enabled"] == "0"


def test_settings_post_can_disable_single_transaction_ai_button(client, core_conn):
    """Verify the owner can hide the transaction AI suggestion action."""
    form = settings_form_data(core_conn)
    form.pop("transaction_ai_rerun_enabled")

    response = client.post(
        "/settings",
        data={CSRF_FIELD_NAME: set_csrf_token(client), **form},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert get_all_settings(core_conn)["transaction_ai_rerun_enabled"] == "0"


def test_settings_post_saves_ui_language_and_renders_french(client, core_conn):
    """Verify the UI language setting localizes shared and settings labels."""
    form = settings_form_data(core_conn, ui_language="fr")

    response = client.post(
        "/settings",
        data={CSRF_FIELD_NAME: set_csrf_token(client), **form},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert_has_element(response, "html", attrs={"lang": "fr"})
    assert_visible_text(
        response,
        "Paramètres enregistrés.",
        "Langue de l'interface",
        "Finances personnelles",
    )
    assert user_settings(core_conn)["ui_language"] == "fr"


def test_viewer_can_only_save_own_general_settings(app, core_conn):
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
    original_global_settings = get_all_settings(core_conn)
    form = settings_form_data(
        core_conn,
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

    viewer_settings = user_settings(core_conn, "settingsviewer")
    global_settings = get_all_settings(core_conn)
    assert response.status_code == 200
    assert viewer_settings["theme_mode"] == "light"
    assert viewer_settings["ui_language"] == "fr"
    assert viewer_settings["comparison_insight_card_limit"] == "5"
    assert global_settings["openai_model"] == original_global_settings["openai_model"]
    assert (
        global_settings["recurrence_minimum_occurrences"] == original_global_settings["recurrence_minimum_occurrences"]
    )
    assert validate_response.status_code == 403


def test_settings_post_rejects_duplicate_statement_type_names(client, core_conn):
    """Verify that statement type sync validation is surfaced by the route."""
    active_types = get_statement_type_options(core_conn)
    form = settings_form_data(
        core_conn,
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
    assert_visible_text(response, "Statement type names must be unique.")


def test_settings_post_rolls_back_when_statement_type_sync_fails(client, core_conn, monkeypatch):
    """Verify late settings-save failures do not persist earlier setting writes."""
    original_settings = get_all_settings(core_conn)
    original_user_settings = user_settings(core_conn)
    form = settings_form_data(core_conn, theme_mode="light", default_table_page_size="99")

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

    settings = get_all_settings(core_conn)
    assert response.status_code == 200
    assert_visible_text(response, "Statement type sync failed late.")
    assert settings["theme_mode"] == original_settings["theme_mode"]
    assert settings["default_table_page_size"] == original_settings["default_table_page_size"]
    assert user_settings(core_conn) == original_user_settings
