"""Tests for category rule form parsing and CSV import/export."""

from sqlalchemy import text
import csv
import io

import pytest

from finance_app.modules.categories.service import normalize_merchant_description
from finance_app.modules.categories.repository import rename_category, resolve_category_id
from finance_app.modules.categories.taxonomy import get_rule_tags_by_rule_id
from finance_app.modules.rules.forms import amount_bounds_label, parse_amount_bounds
from finance_app.modules.rules.import_export import (
    export_rules_csv,
    import_rules_job,
    import_rules_override,
    import_rules_add,
    parse_rules_csv,
    preview_rules_import,
    undo_import_rules_job,
    undo_rules_override_import,
)


def test_parse_amount_bounds_normalizes_optional_values():
    """Verify that rule form amount bounds parse, round, and sort values."""
    assert parse_amount_bounds("20,255", "10") == (10.00, 20.25)
    assert parse_amount_bounds("", "$15.499") == (None, 15.50)
    assert amount_bounds_label(10, 20) == " from 10.00 to 20.00"
    assert amount_bounds_label(None, 20) == " up to 20.00"


def test_parse_amount_bounds_rejects_invalid_numbers():
    """Verify that invalid form amounts return a user-facing validation error."""
    with pytest.raises(ValueError, match="Amount bounds must be valid numbers"):
        parse_amount_bounds("abc", "")


def test_parse_rules_csv_normalizes_headers_tags_and_amount_range():
    """Verify that imported rule CSV rows accept legacy-style column names."""
    raw_text = "\n".join(
        [
            "Merchant,Category,Rule Tags,Amount,Source,Created",
            "Hydro Quebec,Utilities,Tax; Government,$10 - $25,manual,2026-01-02T00:00:00Z",
        ]
    )

    rules = parse_rules_csv(raw_text)

    assert rules == [
            {
                "keyword": normalize_merchant_description("Hydro Quebec"),
                "account_name": "",
                "merchant_name": "",
                "category": "Utilities",
                "tags": ["Tax", "Government"],
                "amount_min": 10.0,
                "amount_max": 25.0,
                "direction": "any",
                "source": "manual",
                "created_at": "2026-01-02T00:00:00Z",
            }
    ]


def test_parse_rules_csv_rejects_missing_required_fields():
    """Verify that malformed rule imports fail before touching the database."""
    with pytest.raises(ValueError, match="Row 2: keyword or merchant_name is required"):
        parse_rules_csv("keyword,category\n,Utilities\n")

    with pytest.raises(ValueError, match="source must be one of automatic, manual"):
        parse_rules_csv("keyword,category,source\nStore,Groceries,system\n")


def test_parse_rules_csv_accepts_automatic_source():
    """Verify automatic rule sources can round-trip through rule CSV imports."""
    rules = parse_rules_csv("keyword,category,source\nMetro Grocery,Food,automatic\n")

    assert rules[0]["source"] == "automatic"


def test_import_rules_add_skips_duplicates_and_persists_tags(core_conn):
    """Verify that add-mode imports skip duplicate rows and attach rule tags."""
    imported_rules = parse_rules_csv(
        "\n".join(
            [
                "keyword,category,tags,amount_min,amount_max",
                "Hydro Quebec,Utilities,Tax; Government,10,25",
                "Hydro Quebec,Utilities,Tax; Government,10,25",
            ]
        )
    )
    undo_state = {}

    message = import_rules_add(core_conn, imported_rules, undo_state)
    core_conn.commit()

    row = core_conn.execute(text("""
        SELECT id, keyword, category, amount_min, amount_max
        FROM category_rules
        WHERE keyword = :p0
        """), {"p0": normalize_merchant_description("Hydro Quebec")}).fetchone()
    assert "Imported 1 new rule." in message
    assert "Skipped 1 duplicate row in the file." in message
    assert row is not None
    assert tuple(row[1:]) == (
        normalize_merchant_description("Hydro Quebec"),
        "Utilities",
        10.0,
        25.0,
    )
    assert get_rule_tags_by_rule_id(core_conn, [row._mapping["id"]])[row._mapping["id"]] == ["Government", "Tax"]
    assert undo_state["mode"] == "add"
    assert len(undo_state["inserted_rules"]) == 1


def test_preview_rules_import_add_skips_without_writing(core_conn):
    """Verify add-mode import preview reports skipped rows without mutation."""
    core_conn.execute(text("""
        INSERT INTO category_rules (keyword, category, source)
        VALUES ('HYDRO QUEBEC', 'Utilities', 'manual')
        """))
    core_conn.commit()

    preview = preview_rules_import(
        core_conn,
        "\n".join(
            [
                "keyword,category,tags",
                "Hydro Quebec,Utilities,Tax",
                "Hydro Quebec,Utilities,Tax",
                "Metro Grocery,Food,Grocery",
            ]
        ),
        "add",
    )
    stored_metro = core_conn.execute(text("SELECT id FROM category_rules WHERE keyword = 'METRO GROCERY'")).fetchone()

    assert preview.total_rows == 3
    assert preview.skipped_existing == 1
    assert preview.skipped_duplicate == 1
    assert len(preview.proposed_rules) == 1
    assert preview.proposed_rules[0]["id"] < 0
    assert preview.proposed_rules[0]["keyword"] == "METRO GROCERY"
    assert preview.proposed_rules[0]["tags"] == ["Grocery"]
    assert stored_metro is None


def test_import_rules_add_persists_account_and_direction_scope(core_conn):
    """Verify imported rules can be constrained to an existing account and direction."""
    account_id = core_conn.execute(text("INSERT INTO accounts (name) VALUES ('Scoped Checking')")).lastrowid
    imported_rules = parse_rules_csv(
        "keyword,account_name,category,direction\n"
        "Metro Grocery,Scoped Checking,Food,debit\n"
    )

    message = import_rules_add(core_conn, imported_rules, {})
    core_conn.commit()

    row = core_conn.execute(text("""
        SELECT account_id, direction
        FROM category_rules
        WHERE keyword = :p0
        """), {"p0": normalize_merchant_description("Metro Grocery")}).fetchone()
    assert message == "Imported 1 new rule."
    assert tuple(row) == (account_id, "debit")


def test_import_rules_add_rejects_unknown_account_scope(core_conn):
    """Verify misspelled account-scoped imports do not become broad rules."""
    imported_rules = parse_rules_csv(
        "keyword,account_name,category,direction\n"
        "Metro Grocery,Missing Account,Food,debit\n"
    )

    with pytest.raises(ValueError, match="Account 'Missing Account' was not found"):
        import_rules_add(core_conn, imported_rules, {})


def test_imported_rules_follow_category_rename(core_conn):
    """Verify imported category rules store the taxonomy category ID."""
    utilities_id = resolve_category_id(core_conn, "Utilities")
    undo_state = {}

    import_rules_add(
        core_conn,
        parse_rules_csv("keyword,category\nHydro Quebec,Utilities\n"),
        undo_state,
    )
    renamed = rename_category(core_conn, "Utilities", "Bills")
    core_conn.commit()

    rule = core_conn.execute(text("""
        SELECT category_id, category
        FROM category_rules
        WHERE keyword = 'HYDRO QUEBEC'
        """)).fetchone()
    assert renamed == "Bills"
    assert tuple(rule) == (utilities_id, "Bills")


def test_export_rules_csv_includes_tags_and_amount_bounds(core_conn):
    """Verify that exported rule CSV preserves rule metadata needed for round trips."""
    import_rules_add(
        core_conn,
        parse_rules_csv(
            "keyword,category,tags,amount_min,amount_max\n"
            "Hydro Quebec,Utilities,Tax; Government,10,25\n"
        ),
        {},
    )
    core_conn.commit()

    exported_rows = list(csv.DictReader(io.StringIO(export_rules_csv(core_conn))))

    assert exported_rows == [
        {
            "keyword": normalize_merchant_description("Hydro Quebec"),
            "account_name": "",
            "merchant_name": "",
            "category": "Utilities",
            "tags": "Government; Tax",
            "amount_min": "10.0",
            "amount_max": "25.0",
            "direction": "any",
            "source": "manual",
            "created_at": exported_rows[0]["created_at"],
        }
    ]
    assert exported_rows[0]["created_at"]


def test_import_rules_add_persists_merchant_bound_rules(core_conn):
    """Verify merchant_name imports create merchant-bound category rules."""
    imported_rules = parse_rules_csv(
        "\n".join(
            [
                "merchant_name,category,tags",
                "Metro Grocery,Food,Tax",
            ]
        )
    )
    undo_state = {}

    message = import_rules_add(core_conn, imported_rules, undo_state)
    core_conn.commit()

    rule = core_conn.execute(text("""
        SELECT category_rules.keyword, category_rules.merchant_id, merchants.merchant_key
        FROM category_rules
        JOIN merchants ON merchants.id = category_rules.merchant_id
        WHERE category_rules.keyword = 'METRO GROCERY'
        """)).fetchone()
    assert message == "Imported 1 new rule."
    assert rule._mapping["merchant_id"] is not None
    assert tuple(rule) == ("METRO GROCERY", rule._mapping["merchant_id"], "METRO GROCERY")


def test_import_rules_job_export_and_undo_use_core_transactions(app, core_conn):
    """Verify import/export job entry points use SQLAlchemy Core connections."""
    del app
    undo_state = {}

    message = import_rules_job(
        "merchant_name,category,tags\nCore Market,Core Job Category,Tax\n",
        "add",
        undo_state,
    )
    exported_rows = list(csv.DictReader(io.StringIO(export_rules_csv())))
    undo_message = undo_import_rules_job(undo_state)

    rule_count = core_conn.execute(text("""
        SELECT COUNT(*) AS count
        FROM category_rules
        WHERE keyword = 'CORE MARKET'
        """)).fetchone()._mapping["count"]
    category_count = core_conn.execute(text("""
        SELECT COUNT(*) AS count
        FROM categories
        WHERE name = 'Core Job Category'
        """)).fetchone()._mapping["count"]

    assert message == "Imported 1 new rule."
    assert exported_rows[0]["keyword"] == "CORE MARKET"
    assert exported_rows[0]["merchant_name"] == "CORE MARKET"
    assert exported_rows[0]["tags"] == "Tax"
    assert "Removed 1 imported rule." in undo_message
    assert "Removed 1 imported category." in undo_message
    assert rule_count == 0
    assert category_count == 0


def test_import_rules_override_replaces_rules_and_undo_restores_previous_state(core_conn):
    """Verify override import replaces rules, clears refs, and can be undone."""
    original_rule_id = core_conn.execute(text("""
        INSERT INTO category_rules (keyword, category, source)
        VALUES ('OLD STORE', 'Utilities', 'manual')
        """)).lastrowid
    tx_id = core_conn.execute(text("""
        INSERT INTO transactions (
            tx_date,
            description,
            amount,
            category,
            category_rule_id,
            fingerprint
        )
        VALUES ('2026-01-02', 'OLD STORE', 12.34, 'Utilities', :p0, 'override-ref')
        """), {"p0": original_rule_id}).lastrowid
    core_conn.commit()
    undo_state = {}
    imported_rules = parse_rules_csv(
        "\n".join(
            [
                "keyword,category,tags",
                "Metro Grocery,Food,Tax",
                "Metro Grocery,Food,Tax",
            ]
        )
    )

    message = import_rules_override(core_conn, imported_rules, undo_state)
    core_conn.commit()

    rules_after_import = core_conn.execute(text("""
        SELECT keyword, category
        FROM category_rules
        ORDER BY keyword
        """)).fetchall()
    tx_after_import = core_conn.execute(text("SELECT category_rule_id FROM transactions WHERE id = :p0"), {"p0": tx_id}).fetchone()
    assert message == (
        "Replaced rules with 1 imported rule. "
        "Cleared rule references on 1 transaction. "
        "Skipped 1 duplicate row in the file."
    )
    assert [tuple(row) for row in rules_after_import] == [("METRO GROCERY", "Food")]
    assert tx_after_import._mapping["category_rule_id"] is None
    assert undo_state["mode"] == "override"

    undo_message = undo_rules_override_import(undo_state)

    restored_rules = core_conn.execute(text("""
        SELECT id, keyword, category
        FROM category_rules
        ORDER BY id
        """)).fetchall()
    restored_tx = core_conn.execute(text("SELECT category_rule_id FROM transactions WHERE id = :p0"), {"p0": tx_id}).fetchone()
    assert undo_message == (
        "Restored 1 rule from before import. "
        "Restored rule references on 1 transaction."
    )
    assert [tuple(row) for row in restored_rules] == [(original_rule_id, "OLD STORE", "Utilities")]
    assert restored_tx._mapping["category_rule_id"] == original_rule_id


def test_preview_rules_import_override_reports_without_writing(core_conn):
    """Verify override-mode import preview reports replacement impact only."""
    first_rule_id = core_conn.execute(text("""
        INSERT INTO category_rules (keyword, category, source)
        VALUES ('OLD STORE', 'Utilities', 'manual')
        """)).lastrowid
    core_conn.execute(text("""
        INSERT INTO category_rules (keyword, category, source)
        VALUES ('OTHER STORE', 'Food', 'manual')
        """))
    tx_id = core_conn.execute(text("""
        INSERT INTO transactions (
            tx_date,
            description,
            amount,
            category,
            category_rule_id,
            fingerprint
        )
        VALUES ('2026-01-02', 'OLD STORE', 12.34, 'Utilities', :p0, 'preview-override-ref')
        """), {"p0": first_rule_id}).lastrowid
    core_conn.commit()

    preview = preview_rules_import(
        core_conn,
        "\n".join(
            [
                "keyword,category,tags",
                "Metro Grocery,Food,Tax",
                "Metro Grocery,Food,Tax",
            ]
        ),
        "override",
    )
    rules_after_preview = core_conn.execute(
        text("SELECT keyword FROM category_rules ORDER BY keyword")
    ).mappings().fetchall()
    tx_after_preview = core_conn.execute(text("SELECT category_rule_id FROM transactions WHERE id = :p0"), {"p0": tx_id}).fetchone()

    assert preview.total_rows == 2
    assert preview.skipped_duplicate == 1
    assert preview.replaced_rules == 2
    assert preview.cleared_transaction_rule_refs == 1
    assert len(preview.proposed_rules) == 1
    assert [row["keyword"] for row in rules_after_preview] == ["OLD STORE", "OTHER STORE"]
    assert tx_after_preview._mapping["category_rule_id"] == first_rule_id


def test_undo_rules_override_import_rejects_changed_rules(core_conn):
    """Verify override undo refuses to discard rule edits made after import."""
    core_conn.execute(text("""
        INSERT INTO category_rules (keyword, category, source)
        VALUES ('OLD STORE', 'Utilities', 'manual')
        """))
    core_conn.commit()
    undo_state = {}
    import_rules_override(
        core_conn,
        parse_rules_csv("keyword,category\nMetro Grocery,Food\n"),
        undo_state,
    )
    core_conn.commit()
    core_conn.execute(text("""
        UPDATE category_rules
        SET category = 'Personal'
        WHERE keyword = 'METRO GROCERY'
        """))
    core_conn.commit()

    with pytest.raises(ValueError, match="rules changed after the import job"):
        undo_rules_override_import(undo_state)


def test_undo_rules_override_import_rejects_imported_rule_references(core_conn):
    """Verify override undo refuses when transactions now reference imported rules."""
    core_conn.execute(text("""
        INSERT INTO category_rules (keyword, category, source)
        VALUES ('OLD STORE', 'Utilities', 'manual')
        """))
    tx_id = core_conn.execute(text("""
        INSERT INTO transactions (tx_date, description, amount, category, fingerprint)
        VALUES ('2026-01-02', 'METRO GROCERY', 12.34, 'Food', 'override-imported-ref')
        """)).lastrowid
    core_conn.commit()
    undo_state = {}
    import_rules_override(
        core_conn,
        parse_rules_csv("keyword,category\nMetro Grocery,Food\n"),
        undo_state,
    )
    core_conn.commit()
    imported_rule_id = core_conn.execute(text("SELECT id FROM category_rules WHERE keyword = 'METRO GROCERY'")).fetchone()._mapping["id"]
    core_conn.execute(text("UPDATE transactions SET category_rule_id = :p0 WHERE id = :p1"), {"p0": imported_rule_id, "p1": tx_id})
    core_conn.commit()

    with pytest.raises(ValueError, match="transactions now reference imported rules"):
        undo_rules_override_import(undo_state)
