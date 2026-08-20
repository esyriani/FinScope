"""Tests for runtime settings persistence helpers."""

import pytest
from flask_login import login_user  # type: ignore[import-untyped]
from sqlalchemy import text

from finance_app.core.constants import USER_ROLE_EDITOR
from finance_app.core.runtime_settings import CONFIRM_AI_TOKEN_USAGE_SETTING_KEY
from finance_app.database.engine import db_core_transaction
from finance_app.database.seeds import seed_runtime_settings_defaults
from finance_app.modules.auth import repository as auth_repository
from finance_app.modules.auth.service import hash_password, load_login_user, utc_now
from finance_app.modules.categories.service import rename_category
from finance_app.modules.recurring.queries import get_recurrence_detection_settings
from finance_app.modules.settings.runtime import (
    get_all_settings,
    get_bool_setting,
    get_float_setting,
    get_int_setting,
    get_setting,
    get_setting_with_fallback,
    get_statement_type_by_id,
    get_statement_type_by_parser_type,
    get_statement_type_options,
    get_unknown_category,
    normalize_default_account_type,
    normalize_statement_import_mode,
    normalize_statement_parser_type,
    sync_statement_types,
    upsert_setting,
    upsert_user_setting,
)


def test_seeded_runtime_settings_default_to_dark_theme(core_conn):
    """Verify new databases default to dark mode."""
    assert get_all_settings(core_conn)["theme_mode"] == "dark"


def test_runtime_settings_helpers_support_core_connections(app, core_conn):
    """Verify runtime settings can be read and written through SQLAlchemy Core."""
    del app
    active_rows = get_statement_type_options(core_conn)
    keep_row = active_rows[0]

    with db_core_transaction() as conn:
        seed_runtime_settings_defaults(conn)
        upsert_setting(conn, "theme_mode", "light")
        sync_statement_types(
            conn,
            [
                {
                    "id": keep_row["id"],
                    "name": "Core bank account",
                    "parser_type": "bank_account",
                },
                {
                    "id": "",
                    "name": "Core rewards card",
                    "parser_type": "credit_card",
                },
            ],
        )
        assert get_statement_type_by_parser_type(conn, "bank_account")["name"] == "Core bank account"
        upsert_setting(conn, "unknown_category", "CORE UNKNOWN")

    active = {row["name"]: row["parser_type"] for row in get_statement_type_options(core_conn)}
    assert get_all_settings(core_conn)["theme_mode"] == "light"
    assert get_setting_with_fallback("theme_mode", "dark") == "light"
    assert get_unknown_category(core_conn) == "UNKNOWN"
    assert active == {
        "Core bank account": "bank_account",
        "Core rewards card": "credit_card",
    }


def test_owner_managed_settings_resolve_from_owner_for_request_user(app, core_conn):
    """Verify advanced settings ignore stale non-owner setting rows."""
    owner = auth_repository.get_user_by_username(core_conn, "owner")
    assert owner is not None
    editor_id = auth_repository.insert_user(
        core_conn,
        "settings-editor",
        hash_password("EditorPass123!"),
        USER_ROLE_EDITOR,
        must_change_password=False,
        now=utc_now(),
    )
    seed_runtime_settings_defaults(core_conn)
    upsert_user_setting(core_conn, owner["id"], "openai_model", "owner-model")
    upsert_user_setting(core_conn, owner["id"], "llm_confidence_threshold", "0.88")
    upsert_user_setting(core_conn, owner["id"], "transaction_ai_rerun_enabled", "0")
    upsert_user_setting(core_conn, owner["id"], CONFIRM_AI_TOKEN_USAGE_SETTING_KEY, "1")
    upsert_user_setting(core_conn, owner["id"], "recurrence_minimum_occurrences", "7")
    upsert_user_setting(core_conn, owner["id"], "recurrence_amount_tolerance_percent", "0.33")
    upsert_user_setting(core_conn, editor_id, "theme_mode", "light")
    upsert_user_setting(core_conn, editor_id, "openai_model", "editor-model")
    upsert_user_setting(core_conn, editor_id, "llm_confidence_threshold", "0.11")
    upsert_user_setting(core_conn, editor_id, "transaction_ai_rerun_enabled", "1")
    upsert_user_setting(core_conn, editor_id, CONFIRM_AI_TOKEN_USAGE_SETTING_KEY, "0")
    upsert_user_setting(core_conn, editor_id, "recurrence_minimum_occurrences", "2")
    upsert_user_setting(core_conn, editor_id, "recurrence_amount_tolerance_percent", "0.01")
    core_conn.commit()

    with app.test_request_context("/transactions"):
        editor = load_login_user(editor_id)
        assert editor is not None
        login_user(editor)

        settings = get_all_settings(core_conn)

        assert get_setting(core_conn, "theme_mode") == "light"
        assert settings["theme_mode"] == "light"
        assert get_setting(core_conn, "openai_model") == "owner-model"
        assert settings["openai_model"] == "owner-model"
        assert get_float_setting(core_conn, "llm_confidence_threshold", 0.5) == 0.88
        assert get_bool_setting(core_conn, "transaction_ai_rerun_enabled", True) is False
        assert get_bool_setting(core_conn, CONFIRM_AI_TOKEN_USAGE_SETTING_KEY, False) is True
        assert get_int_setting(core_conn, "recurrence_minimum_occurrences", 3) == 7
        recurrence_settings = get_recurrence_detection_settings(core_conn)
        assert recurrence_settings.minimum_occurrences == 7
        assert recurrence_settings.amount_tolerance_percent == 0.33


def test_sync_statement_types_updates_adds_and_inactivates_rows(core_conn):
    """Verify statement type sync keeps submitted rows active and retires omitted ones."""
    active_rows = get_statement_type_options(core_conn)
    keep_row = active_rows[0]

    sync_statement_types(
        core_conn,
        [
            {
                "id": keep_row["id"],
                "name": "Daily bank account",
                "parser_type": "bank_account",
            },
            {
                "id": "",
                "name": "Rewards card",
                "parser_type": "credit_card",
            },
        ],
    )
    core_conn.commit()

    active = {
        row["name"]: (row["parser_type"], row["import_mode"], row["default_account_type"])
        for row in get_statement_type_options(core_conn)
    }
    inactive = [row for row in get_statement_type_options(core_conn, include_inactive=True) if not row["active"]]
    assert active == {
        "Daily bank account": ("bank_account", "ledger", "checking"),
        "Rewards card": ("credit_card", "ledger", "credit_card"),
    }
    assert len(inactive) == 2
    assert get_statement_type_by_id(core_conn, inactive[0]["id"]) is None
    assert get_statement_type_by_parser_type(core_conn, "bank_account")["name"] == "Daily bank account"


def test_sync_statement_types_matches_existing_rows_by_database_name_key(core_conn):
    """Synchronize statement types by lower-trimmed generated name keys."""
    keep_row = get_statement_type_options(core_conn)[0]

    sync_statement_types(
        core_conn,
        [
            {
                "id": "",
                "name": f" {str(keep_row['name']).upper()} ",
                "parser_type": "bank_account",
            },
        ],
    )
    core_conn.commit()

    active_rows = get_statement_type_options(core_conn)

    assert len(active_rows) == 1
    assert active_rows[0]["id"] == keep_row["id"]
    assert active_rows[0]["name"] == keep_row["name"]
    assert active_rows[0]["parser_type"] == "bank_account"


def test_sync_statement_types_rejects_duplicate_or_empty_rows(core_conn):
    """Verify statement type sync enforces unique active names and non-empty sets."""
    with pytest.raises(ValueError, match="Statement type names must be unique."):
        sync_statement_types(
            core_conn,
            [
                {"id": "", "name": "Credit", "parser_type": "credit_card"},
                {"id": "", "name": " credit ", "parser_type": "bank_account"},
            ],
        )

    with pytest.raises(ValueError, match="Add at least one statement type."):
        sync_statement_types(
            core_conn,
            [
                {"id": "", "name": "  ", "parser_type": "credit_card"},
            ],
        )


def test_statement_parser_type_validation_defaults_unknown_values(core_conn):
    """Verify invalid parser types normalize to the default credit card parser."""
    sync_statement_types(
        core_conn,
        [
            {
                "id": "",
                "name": "Imported file",
                "parser_type": "spreadsheet",
            },
        ],
    )
    core_conn.commit()

    assert normalize_statement_parser_type("bank_account") == "bank_account"
    assert normalize_statement_parser_type("interac_etransfer") == "interac_etransfer"
    assert normalize_statement_parser_type("spreadsheet") == "credit_card"
    assert normalize_statement_import_mode("enrichment", parser_type="interac_etransfer") == "enrichment"
    assert normalize_statement_import_mode("ledger", parser_type="interac_etransfer") == "enrichment"
    assert normalize_statement_import_mode("unknown", parser_type="credit_card") == "ledger"
    assert normalize_default_account_type("credit_card") == "credit_card"
    assert normalize_default_account_type("unknown", parser_type="credit_card") == "credit_card"
    assert get_statement_type_options(core_conn)[0]["parser_type"] == "credit_card"


def test_unknown_category_is_fixed_and_protected(core_conn):
    """Verify settings cannot rename the built-in Unknown category."""
    unknown_id = core_conn.execute(text("""
        SELECT id, builtin_key
        FROM categories
        WHERE name = 'UNKNOWN'
        """)).fetchone()
    core_conn.execute(
        text("""
        INSERT INTO transactions (tx_date, description, amount, category_id, fingerprint)
        VALUES ('2026-01-02', 'UNKNOWN SHOP', 12.34, :p0, 'unknown-rename-tx')
        """),
        {"p0": unknown_id._mapping["id"]},
    )
    core_conn.execute(
        text("""
        INSERT INTO category_rules (keyword, category_id)
        VALUES ('UNKNOWN SHOP', :p0)
        """),
        {"p0": unknown_id._mapping["id"]},
    )

    upsert_setting(core_conn, "unknown_category", "UNCATEGORIZED")
    renamed = rename_category(core_conn, "UNKNOWN", "UNCATEGORIZED")
    core_conn.commit()

    category = core_conn.execute(
        text("""
        SELECT id, name
        FROM categories
        WHERE id = :p0
        """),
        {"p0": unknown_id._mapping["id"]},
    ).fetchone()
    transaction = core_conn.execute(text("""
        SELECT category_id, category
        FROM transactions
        WHERE fingerprint = 'unknown-rename-tx'
        """)).fetchone()
    rule = core_conn.execute(text("""
        SELECT category_id, category
        FROM category_rules
        WHERE keyword = 'UNKNOWN SHOP'
        """)).fetchone()

    assert unknown_id._mapping["builtin_key"] == "unknown"
    assert renamed is None
    assert tuple(category) == (unknown_id._mapping["id"], "UNKNOWN")
    assert tuple(transaction) == (unknown_id._mapping["id"], None)
    assert tuple(rule) == (unknown_id._mapping["id"], None)
    assert get_unknown_category(core_conn) == "UNKNOWN"
