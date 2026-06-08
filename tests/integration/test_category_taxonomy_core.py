"""SQLAlchemy Core tests for category taxonomy helpers."""

from sqlalchemy import text
from finance_app.database.engine import db_core_transaction
from finance_app.modules.categories.taxonomy import (
    get_rule_tags_by_rule_id,
    get_tag_options,
    get_transaction_tag_names,
    get_transaction_tags_by_id,
    set_rule_tags,
    set_transaction_tags,
    upsert_category_metadata,
    upsert_tag_metadata,
)


def test_taxonomy_helpers_support_core_connections(app, core_conn):
    """Verify taxonomy helpers can read and write through SQLAlchemy Core."""
    del app
    rule_id = core_conn.execute(text("""
        INSERT INTO category_rules (keyword, category)
        VALUES ('CORE STORE', 'UNKNOWN')
        """)).lastrowid
    transaction_id = core_conn.execute(text("""
        INSERT INTO transactions (tx_date, description, amount, category, fingerprint)
        VALUES ('2026-01-02', 'CORE STORE', 12.34, 'UNKNOWN', 'core-taxonomy-tx')
        """)).lastrowid
    core_conn.commit()

    with db_core_transaction() as conn:
        assert upsert_category_metadata(conn, "Transit", "City travel", "Use for transit.") == "Transit"
        assert upsert_tag_metadata(conn, "Audit", "Needs audit", "Review later.", "#123abc") == "Audit"
        assert upsert_tag_metadata(conn, "Audit", "Reviewed", "Already reviewed.", "#abcdef") == "Audit"
        set_rule_tags(conn, rule_id, ["Audit"])
        set_transaction_tags(conn, transaction_id, ["Audit"], source="manual", rule_id=rule_id)

        assert "Audit" in get_tag_options(conn)
        assert get_rule_tags_by_rule_id(conn, [rule_id])[rule_id] == ["Audit"]
        assert get_transaction_tag_names(conn, transaction_id) == ["Audit"]
        assert get_transaction_tags_by_id(conn, [transaction_id])[transaction_id] == ["Audit"]

    category = core_conn.execute(text("""
        SELECT description, instruction
        FROM categories
        WHERE name = 'Transit'
        """)).fetchone()
    tag = core_conn.execute(text("""
        SELECT description, instruction, color
        FROM tags
        WHERE name = 'Audit'
        """)).fetchone()
    transaction_tag = core_conn.execute(
        text("""
        SELECT source, rule_id
        FROM transaction_tags
        WHERE transaction_id = :p0
        """),
        {"p0": transaction_id},
    ).fetchone()

    assert tuple(category) == ("City travel", "Use for transit.")
    assert tuple(tag) == ("Reviewed", "Already reviewed.", "#123abc")
    assert tuple(transaction_tag) == ("manual", rule_id)
