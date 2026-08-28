"""SQLAlchemy Core tests for category repository helpers."""

from decimal import Decimal

from sqlalchemy import insert, select

from finance_app.core.constants import CATEGORY_RULE_SOURCE_AUTOMATIC
from finance_app.database.engine import db_core_transaction
from finance_app.database.tables import (
    categories as categories_table,
)
from finance_app.database.tables import (
    category_rules as category_rules_table,
)
from finance_app.database.tables import (
    merchants as merchants_table,
)
from finance_app.database.tables import (
    transactions as transactions_table,
)
from finance_app.modules.categories.repository import (
    create_category,
    get_category_options,
    get_category_rules,
    rename_category,
    resolve_category_id,
    save_category_rule,
)
from finance_app.modules.categories.taxonomy import get_transaction_tag_names
from finance_app.modules.rules.workflow import apply_all_rules_to_transactions
from finance_app.modules.transactions.repository import assign_manual_category


def test_category_repository_rule_helpers_support_core_connections(app, core_conn):
    """Verify category rule persistence can run through SQLAlchemy Core."""
    del app
    merchant_id = core_conn.execute(
        insert(merchants_table).values(
            merchant_key="CORE MARKET",
        )
    ).inserted_primary_key[0]
    core_conn.commit()

    with db_core_transaction() as conn:
        rule_id = save_category_rule(
            conn,
            "CORE MARKET",
            "Food",
            amount_min=10,
            amount_max=20,
            tags=["Tax"],
            merchant_id=merchant_id,
        )
        assert rule_id is not None

        same_rule_id = save_category_rule(
            conn,
            "CORE MARKET UPDATED",
            "Utilities",
            source=CATEGORY_RULE_SOURCE_AUTOMATIC,
            amount_min=10,
            amount_max=20,
            tags=["Government"],
            merchant_id=merchant_id,
            protect_user_rule=True,
        )
        assert same_rule_id is None

        updated_rule_id = save_category_rule(
            conn,
            "CORE MARKET UPDATED",
            "Utilities",
            source=CATEGORY_RULE_SOURCE_AUTOMATIC,
            amount_min=10,
            amount_max=20,
            tags=["Government"],
            merchant_id=merchant_id,
        )
        assert updated_rule_id == rule_id

        rules = get_category_rules(conn)
        rule = next(rule for rule in rules if rule["id"] == rule_id)
        assert rule["keyword"] == "CORE MARKET UPDATED"
        assert rule["category"] == "Utilities"
        assert rule["source"] == "automatic"
        assert rule["merchant_name"] == "CORE MARKET"
        assert rule["tags"] == ["Government"]

    persisted = core_conn.execute(
        select(
            category_rules_table.c.keyword,
            category_rules_table.c.category,
            category_rules_table.c.source,
            category_rules_table.c.ai_approved,
        ).where(category_rules_table.c.id == rule_id)
    ).fetchone()
    assert tuple(persisted) == ("CORE MARKET UPDATED", "Utilities", "automatic", 0)
    assert any(rule["keyword"] == "CORE MARKET UPDATED" for rule in get_category_rules())


def test_category_repository_category_helpers_support_core_connections(app, core_conn):
    """Verify category helpers can run through SQLAlchemy Core."""
    del app

    with db_core_transaction() as conn:
        assert create_category(conn, "  Pet   care ") == "Pet care"
        assert rename_category(conn, "Pet care", "Pet supplies") == "Pet supplies"
        assert "Pet supplies" in get_category_options(conn)

    category = (
        core_conn.execute(select(categories_table.c.name).where(categories_table.c.name == "Pet supplies"))
        .mappings()
        .fetchone()
    )
    assert category["name"] == "Pet supplies"
    assert "Pet supplies" in get_category_options()


def test_category_rule_amounts_are_quantized_at_repository_boundary(core_conn):
    """Verify rule persistence stores and returns fixed-scale Decimal bounds."""
    rule_id = save_category_rule(
        core_conn,
        "ROUNDING STORE",
        "Food",
        amount_min="2.675",
        amount_max="3.335",
    )
    core_conn.commit()

    persisted = core_conn.execute(
        select(category_rules_table.c.amount_min, category_rules_table.c.amount_max).where(
            category_rules_table.c.id == rule_id
        )
    ).fetchone()
    rule = next(rule for rule in get_category_rules(core_conn) if rule["id"] == rule_id)

    assert tuple(persisted) == (Decimal("2.68"), Decimal("3.34"))
    assert rule["amount_min"] == Decimal("2.68")
    assert rule["amount_max"] == Decimal("3.34")


def test_category_repository_resolves_by_database_name_key(app, core_conn):
    """Resolve and rename categories through the generated normalized name key."""
    del app

    with db_core_transaction() as conn:
        assert create_category(conn, "Case Study") == "Case Study"
        category_id = resolve_category_id(conn, " case study ")
        assert category_id == resolve_category_id(conn, "CASE STUDY")
        assert rename_category(conn, "case study", "Case Studies") == "Case Studies"
        assert resolve_category_id(conn, " case STUDIES ") == category_id


def test_category_rename_updates_manual_rule_and_rule_applied_rows(app, core_conn):
    """Verify category renames reach rows created by normal category workflows."""
    del app
    food_id = resolve_category_id(core_conn, "Food")
    unknown_id = resolve_category_id(core_conn, "UNKNOWN")
    manual_id = core_conn.execute(
        insert(transactions_table).values(
            tx_date="2026-01-02",
            description="Metro Grocery",
            amount=12.34,
            category="UNKNOWN",
            category_id=unknown_id,
            needs_review=1,
            fingerprint="rename-manual",
        )
    ).inserted_primary_key[0]
    rule_target_id = core_conn.execute(
        insert(transactions_table).values(
            tx_date="2026-01-03",
            description="Express Market",
            amount=22.00,
            category="UNKNOWN",
            category_id=unknown_id,
            needs_review=1,
            fingerprint="rename-rule-target",
        )
    ).inserted_primary_key[0]
    core_conn.commit()

    with db_core_transaction() as conn:
        result = assign_manual_category(
            conn,
            manual_id,
            "Food",
            tag_names=["Tax"],
            rule_keyword="EXPRESS MARKET",
        )
        assert result.saved_rule_id is not None
        updated_count, _ = apply_all_rules_to_transactions(conn, capture_undo=True)
        assert updated_count == 1
        assert rename_category(conn, "Food", "Meals") == "Meals"

    rows = core_conn.execute(
        select(
            transactions_table.c.fingerprint,
            transactions_table.c.category_id,
            transactions_table.c.category,
        )
        .where(transactions_table.c.id.in_((manual_id, rule_target_id)))
        .order_by(transactions_table.c.fingerprint)
    ).fetchall()
    rule = core_conn.execute(
        select(
            category_rules_table.c.category_id,
            category_rules_table.c.category,
        ).where(category_rules_table.c.keyword == "EXPRESS MARKET")
    ).fetchone()

    assert [tuple(row) for row in rows] == [
        ("rename-manual", food_id, "Meals"),
        ("rename-rule-target", food_id, "Meals"),
    ]
    assert tuple(rule) == (food_id, "Meals")
    assert get_transaction_tag_names(core_conn, manual_id) == ["Tax"]


def test_resolve_category_id_uses_existing_taxonomy_rows(app):
    """Verify the category resolver links labels without creating categories."""
    del app

    with db_core_transaction() as conn:
        food_id = resolve_category_id(conn, " food ")
        missing_id = resolve_category_id(conn, "Not a configured category")

    assert food_id is not None
    assert missing_id is None
