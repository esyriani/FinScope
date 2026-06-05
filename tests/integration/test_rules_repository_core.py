"""SQLAlchemy Core tests for rules repository helpers."""

from sqlalchemy import text
from sqlalchemy import delete, insert

from finance_app.database.dates import format_utc_datetime
from finance_app.database.engine import db_core_transaction
from finance_app.database.tables import (
    category_rules as category_rules_table,
    transactions as transactions_table,
)
from finance_app.modules.categories.repository import resolve_category_id
from finance_app.modules.rules.repository import (
    category_rule_exists,
    ensure_import_category,
    existing_category_names,
    insert_imported_rule,
    remove_imported_categories,
    restore_category_rules,
    rule_reference_count,
    snapshot_category_rules,
    snapshot_rule_by_id,
    snapshot_transaction_rule_refs,
)


def test_rules_repository_import_helpers_support_core_connections(app, core_conn):
    """Verify imported-rule persistence helpers can run through SQLAlchemy Core."""
    del app
    rule = {
        "keyword": "CORE MARKET",
        "merchant_name": "Core Market",
        "category": "Core Import",
        "tags": ["Tax"],
        "amount_min": 10.0,
        "amount_max": 20.0,
        "source": "manual",
        "created_at": "2026-01-02 03:04:05",
    }

    with db_core_transaction() as conn:
        categories = existing_category_names(conn)
        created_categories = []
        ensure_import_category(conn, rule["category"], categories, created_categories)
        category_id = resolve_category_id(conn, rule["category"])

        assert created_categories == ["Core Import"]
        assert not category_rule_exists(conn, rule)

        rule_id = insert_imported_rule(conn, rule)
        result = conn.execute(
            insert(transactions_table).values(
                tx_date="2026-01-03",
                description="Core Market",
                amount=12.34,
                category="Core Import",
                category_rule_id=rule_id,
                fingerprint="core-rules-repository-ref",
            )
        )
        transaction_id = result.inserted_primary_key[0]

        snapshot = snapshot_rule_by_id(conn, rule_id)
        assert category_rule_exists(conn, rule)
        assert snapshot["category_id"] == category_id
        assert snapshot["merchant_name"] == "CORE MARKET"
        assert snapshot["tags"] == ["Tax"]
        assert rule_reference_count(conn, rule_id) == 1
        assert snapshot_transaction_rule_refs(conn) == [
            {"transaction_id": transaction_id, "category_rule_id": rule_id}
        ]
        assert snapshot in snapshot_category_rules(conn)

    persisted = core_conn.execute(text("""
        SELECT category_rules.keyword, category_rules.category, merchants.merchant_key
        FROM category_rules
        JOIN merchants ON merchants.id = category_rules.merchant_id
        WHERE category_rules.keyword = 'CORE MARKET'
        """)).fetchone()
    assert tuple(persisted) == ("CORE MARKET", "Core Import", "CORE MARKET")


def test_rules_repository_restore_and_cleanup_support_core_connections(app, core_conn):
    """Verify rule restore and imported category cleanup support Core connections."""
    del app, core_conn
    rule = {
        "id": 7001,
        "account_id": None,
        "account_name": None,
        "merchant_id": None,
        "merchant_name": None,
        "keyword": "CORE RESTORE",
        "category": "Core Restored",
        "amount_min": None,
        "amount_max": None,
        "direction": "any",
        "source": "manual",
        "ai_approved": 0,
        "created_at": "2026-01-04 05:06:07",
        "tags": ["Government"],
    }

    with db_core_transaction() as conn:
        categories = existing_category_names(conn)
        created_categories = []
        ensure_import_category(conn, rule["category"], categories, created_categories)
        rule["category_id"] = resolve_category_id(conn, rule["category"])
        restore_category_rules(conn, [rule])

        expected_rule = {
            **rule,
            "created_at": format_utc_datetime(rule["created_at"]),
        }
        assert snapshot_rule_by_id(conn, rule["id"]) == expected_rule
        assert remove_imported_categories(conn, created_categories) == 0

        conn.execute(
            delete(category_rules_table).where(category_rules_table.c.id == rule["id"])
        )
        assert remove_imported_categories(conn, created_categories) == 1
