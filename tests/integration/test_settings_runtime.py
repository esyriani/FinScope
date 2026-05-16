"""Tests for runtime settings persistence helpers."""

import pytest

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
    update_unknown_category,
    upsert_setting,
)


def test_seeded_runtime_settings_default_to_dark_theme(db_conn):
    """Verify new databases default to dark mode."""
    assert get_all_settings(db_conn)["theme_mode"] == "dark"


def test_runtime_settings_helpers_support_core_connections(app, db_conn):
    """Verify runtime settings can be read and written through SQLAlchemy Core."""
    del app
    active_rows = get_statement_type_options(db_conn)
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
        assert update_unknown_category(conn, "CORE UNKNOWN") == "CORE UNKNOWN"

    active = {
        row["name"]: row["parser_type"]
        for row in get_statement_type_options(db_conn)
    }
    assert get_all_settings(db_conn)["theme_mode"] == "light"
    assert get_setting_with_fallback("theme_mode", "dark") == "light"
    assert get_unknown_category(db_conn) == "CORE UNKNOWN"
    assert active == {
        "Core bank account": "bank_account",
        "Core rewards card": "credit_card",
    }


def test_sync_statement_types_updates_adds_and_inactivates_rows(db_conn):
    """Verify statement type sync keeps submitted rows active and retires omitted ones."""
    active_rows = get_statement_type_options(db_conn)
    keep_row = active_rows[0]

    sync_statement_types(
        db_conn,
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
    db_conn.commit()

    active = {
        row["name"]: (row["parser_type"], row["import_mode"], row["default_account_type"])
        for row in get_statement_type_options(db_conn)
    }
    inactive = [
        row
        for row in get_statement_type_options(db_conn, include_inactive=True)
        if not row["active"]
    ]
    assert active == {
        "Daily bank account": ("bank_account", "ledger", "checking"),
        "Rewards card": ("credit_card", "ledger", "credit_card"),
    }
    assert len(inactive) == 2
    assert get_statement_type_by_id(db_conn, inactive[0]["id"]) is None
    assert get_statement_type_by_parser_type(db_conn, "bank_account")["name"] == "Daily bank account"


def test_sync_statement_types_rejects_duplicate_or_empty_rows(db_conn):
    """Verify statement type sync enforces unique active names and non-empty sets."""
    with pytest.raises(ValueError, match="Statement type names must be unique."):
        sync_statement_types(
            db_conn,
            [
                {"id": "", "name": "Credit", "parser_type": "credit_card"},
                {"id": "", "name": " credit ", "parser_type": "bank_account"},
            ],
        )

    with pytest.raises(ValueError, match="Add at least one statement type."):
        sync_statement_types(
            db_conn,
            [
                {"id": "", "name": "  ", "parser_type": "credit_card"},
            ],
        )


def test_statement_parser_type_validation_defaults_unknown_values(db_conn):
    """Verify invalid parser types normalize to the default credit card parser."""
    sync_statement_types(
        db_conn,
        [
            {
                "id": "",
                "name": "Imported file",
                "parser_type": "spreadsheet",
            },
        ],
    )
    db_conn.commit()

    assert normalize_statement_parser_type("bank_account") == "bank_account"
    assert normalize_statement_parser_type("interac_etransfer") == "interac_etransfer"
    assert normalize_statement_parser_type("spreadsheet") == "credit_card"
    assert normalize_statement_import_mode("enrichment", parser_type="interac_etransfer") == "enrichment"
    assert normalize_statement_import_mode("ledger", parser_type="interac_etransfer") == "enrichment"
    assert normalize_statement_import_mode("unknown", parser_type="credit_card") == "ledger"
    assert normalize_default_account_type("credit_card") == "credit_card"
    assert normalize_default_account_type("unknown", parser_type="credit_card") == "credit_card"
    assert get_statement_type_options(db_conn)[0]["parser_type"] == "credit_card"


def test_update_unknown_category_renames_stable_category_and_refreshes_caches(db_conn):
    """Verify unknown-category renames preserve the category id used by transactions and rules."""
    unknown_id = db_conn.execute(
        """
        SELECT id
        FROM categories
        WHERE name = 'UNKNOWN'
        """
    ).fetchone()["id"]
    db_conn.execute(
        """
        INSERT INTO transactions (tx_date, description, amount, category_id, fingerprint)
        VALUES ('2026-01-02', 'UNKNOWN SHOP', 12.34, ?, 'unknown-rename-tx')
        """,
        (unknown_id,),
    )
    db_conn.execute(
        """
        INSERT INTO category_rules (keyword, category_id)
        VALUES ('UNKNOWN SHOP', ?)
        """,
        (unknown_id,),
    )

    updated = update_unknown_category(db_conn, "UNCATEGORIZED")
    db_conn.commit()

    category = db_conn.execute(
        """
        SELECT id, name
        FROM categories
        WHERE id = ?
        """,
        (unknown_id,),
    ).fetchone()
    transaction = db_conn.execute(
        """
        SELECT category_id, category
        FROM transactions
        WHERE fingerprint = 'unknown-rename-tx'
        """
    ).fetchone()
    rule = db_conn.execute(
        """
        SELECT category_id, category
        FROM category_rules
        WHERE keyword = 'UNKNOWN SHOP'
        """
    ).fetchone()

    assert updated == "UNCATEGORIZED"
    assert tuple(category) == (unknown_id, "UNCATEGORIZED")
    assert tuple(transaction) == (unknown_id, "UNCATEGORIZED")
    assert tuple(rule) == (unknown_id, "UNCATEGORIZED")
    assert get_unknown_category(db_conn) == "UNCATEGORIZED"


def test_update_unknown_category_defaults_blank_values(db_conn):
    """Verify blank unknown-category values normalize to the application default."""
    assert update_unknown_category(db_conn, "   ") == "UNKNOWN"
    assert get_unknown_category(db_conn) == "UNKNOWN"
