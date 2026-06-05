"""SQLAlchemy Core tests for rules service helpers."""

from sqlalchemy import text
from werkzeug.datastructures import MultiDict

from finance_app.database.engine import db_core_transaction
from finance_app.database.tables import category_rules as category_rules_table
from finance_app.modules.categories.taxonomy import get_rule_tags_by_rule_id
from finance_app.modules.rules.service import (
    approve_automatic_rule,
    create_rule_from_form,
    delete_rule,
    get_rule_for_apply,
    update_rule_from_form,
)


def test_rules_service_mutations_support_core_connections(app, core_conn):
    """Verify rule create, update, approval, lookup, and delete support Core."""
    del app
    auto_rule_id = core_conn.execute(text("""
        INSERT INTO category_rules (keyword, category, source, ai_approved)
        VALUES ('AUTO STORE', 'Food', 'automatic', 0)
        """)).lastrowid
    core_conn.commit()

    with db_core_transaction() as conn:
        keyword = create_rule_from_form(
            conn,
            MultiDict(
                [
                    ("keyword", "Metro Grocery"),
                    ("category", "Food"),
                    ("tags", "Tax"),
                    ("amount_min", "10"),
                    ("amount_max", "20"),
                ]
            ),
        )
        assert keyword == "METRO GROCERY"

        rule_id = conn.execute(
            category_rules_table.select()
            .with_only_columns(category_rules_table.c.id)
            .where(category_rules_table.c.keyword == "METRO GROCERY")
        ).scalar_one()
        update_rule_from_form(
            conn,
            rule_id,
            MultiDict(
                [
                    ("keyword", "Metro Grocery updated"),
                    ("category", "Utilities"),
                    ("tags", "Government"),
                    ("amount_min", "25"),
                    ("amount_max", "50"),
                ]
            ),
        )

        approved_keyword, changed = approve_automatic_rule(conn, auto_rule_id)
        assert (approved_keyword, changed) == ("AUTO STORE", True)

        rule = get_rule_for_apply(conn, rule_id)
        assert rule["keyword"] == "METRO GROCERY UPDATED"
        assert rule["category"] == "Utilities"
        assert rule["tags"] == ["Government"]
        assert delete_rule(conn, auto_rule_id)

    updated_rule = core_conn.execute(text("""
        SELECT keyword, category, amount_min, amount_max, source, ai_approved
        FROM category_rules
        WHERE keyword = 'METRO GROCERY UPDATED'
        """)).fetchone()
    auto_count = core_conn.execute(text("""
        SELECT COUNT(*) AS count
        FROM category_rules
        WHERE id = :p0
        """), {"p0": auto_rule_id}).fetchone()._mapping["count"]

    assert tuple(updated_rule) == ("METRO GROCERY UPDATED", "Utilities", 25.0, 50.0, "manual", 0)
    assert get_rule_tags_by_rule_id(core_conn, [rule_id])[rule_id] == ["Government"]
    assert auto_count == 0
