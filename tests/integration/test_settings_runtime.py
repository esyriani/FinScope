"""Tests for runtime settings persistence helpers."""

import pytest
from sqlalchemy import text

from finance_app.database.engine import db_core_transaction
from finance_app.modules.settings.runtime import (
    get_all_settings,
    get_setting_with_fallback,
    get_statement_type_by_id,
    get_statement_type_by_parser_type,
    get_statement_type_options,
    get_unknown_category,
    normalize_default_account_type,
    normalize_statement_import_mode,
    normalize_statement_parser_type,
    seed_runtime_settings,
    sync_statement_types,
    upsert_setting,
)
from finance_app.modules.categories.service import rename_category


def test_seeded_runtime_settings_default_to_dark_theme(core_conn):
    """Verify new databases default to dark mode."""
    assert get_all_settings(core_conn)["theme_mode"] == "dark"


def test_runtime_settings_helpers_support_core_connections(app, core_conn):
    """Verify runtime settings can be read and written through SQLAlchemy Core."""
    del app
    active_rows = get_statement_type_options(core_conn)
    keep_row = active_rows[0]

    with db_core_transaction() as conn:
        seed_runtime_settings(conn)
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

    active = {
        row["name"]: row["parser_type"]
        for row in get_statement_type_options(core_conn)
    }
    assert get_all_settings(core_conn)["theme_mode"] == "light"
    assert get_setting_with_fallback("theme_mode", "dark") == "light"
    assert get_unknown_category(core_conn) == "UNKNOWN"
    assert active == {
        "Core bank account": "bank_account",
        "Core rewards card": "credit_card",
    }


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
    inactive = [
        row
        for row in get_statement_type_options(core_conn, include_inactive=True)
        if not row["active"]
    ]
    assert active == {
        "Daily bank account": ("bank_account", "ledger", "checking"),
        "Rewards card": ("credit_card", "ledger", "credit_card"),
    }
    assert len(inactive) == 2
    assert get_statement_type_by_id(core_conn, inactive[0]["id"]) is None
    assert get_statement_type_by_parser_type(core_conn, "bank_account")["name"] == "Daily bank account"


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
    """Verify legacy settings cannot rename the built-in Unknown category."""
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
