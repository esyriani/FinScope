"""Route and service tests for taxonomy admin mutations."""

from sqlalchemy import text
from tests.support.html import assert_not_markup, assert_not_visible_text, assert_visible_text
from tests.support.web import set_csrf_token

from finance_app.core.csrf import CSRF_FIELD_NAME


def insert_category(conn, name):
    """Insert a category and return its id."""
    category_id = conn.execute(
        text("""
        INSERT INTO categories (name)
        VALUES (:p0)
        """),
        {"p0": name},
    ).lastrowid
    conn.commit()
    return category_id


def insert_tag(conn, name, color="#64748b"):
    """Insert a tag and return its id."""
    tag_id = conn.execute(
        text("""
        INSERT INTO tags (name, color)
        VALUES (:p0, :p1)
        """),
        {"p0": name, "p1": color},
    ).lastrowid
    conn.commit()
    return tag_id


def test_taxonomy_category_create_and_delete_routes_persist_changes(client, core_conn):
    """Verify that category create and delete routes update the database."""
    token = set_csrf_token(client)

    create_response = client.post(
        "/taxonomy/categories/create",
        data={
            CSRF_FIELD_NAME: token,
            "name": "Subscriptions",
            "description": "Recurring paid services",
            "instruction": "Use for streaming and software subscriptions.",
        },
        follow_redirects=True,
    )

    category = core_conn.execute(text("""
        SELECT id, description, instruction
        FROM categories
        WHERE name = 'Subscriptions'
        """)).fetchone()
    assert create_response.status_code == 200
    assert category is not None
    assert category._mapping["description"] == "Recurring paid services"

    delete_response = client.post(
        "/taxonomy/categories/delete",
        data={
            CSRF_FIELD_NAME: token,
            "category_id": category._mapping["id"],
        },
        follow_redirects=True,
    )

    remaining = core_conn.execute(text("""
        SELECT COUNT(*) AS count
        FROM categories
        WHERE name = 'Subscriptions'
        """)).fetchone()._mapping["count"]
    assert delete_response.status_code == 200
    assert remaining == 0


def test_taxonomy_category_delete_route_refuses_in_use_category(client, core_conn):
    """Verify that the category delete route keeps categories used by transactions."""
    category_id = core_conn.execute(text("""
        INSERT INTO categories (name)
        VALUES ('Transit')
        """)).lastrowid
    core_conn.execute(
        text("""
        INSERT INTO transactions (
            tx_date,
            description,
            amount,
            category_id,
            fingerprint
        )
        VALUES ('2026-01-02', 'METRO PASS', 91.25, :p0, 'route-delete-guard')
        """),
        {"p0": category_id},
    )
    core_conn.commit()

    response = client.post(
        "/taxonomy/categories/delete",
        data={
            CSRF_FIELD_NAME: set_csrf_token(client),
            "category_id": category_id,
        },
        follow_redirects=True,
    )

    category_count = (
        core_conn.execute(
            text("""
        SELECT COUNT(*) AS count
        FROM categories
        WHERE id = :p0
        """),
            {"p0": category_id},
        )
        .fetchone()
        ._mapping["count"]
    )
    assert response.status_code == 200
    assert_visible_text(response, "Only unused categories can be deleted.")
    assert_not_visible_text(response, "Category Transit cannot be deleted because it is in use")
    assert_not_markup(response, "bi-lock")
    assert category_count == 1


def test_category_update_route_rejects_rename_conflict(client, core_conn):
    """Verify category renames cannot collide with an existing category."""
    source_id = insert_category(core_conn, "Pets")
    insert_category(core_conn, "Food Delivery")

    response = client.post(
        "/taxonomy/categories/update",
        data={
            CSRF_FIELD_NAME: set_csrf_token(client),
            "category_id": source_id,
            "name": "Food Delivery",
            "description": "Pet care",
            "instruction": "Use for pet expenses.",
        },
        follow_redirects=True,
    )

    source = core_conn.execute(
        text("""
        SELECT name, description, instruction
        FROM categories
        WHERE id = :p0
        """),
        {"p0": source_id},
    ).fetchone()
    assert response.status_code == 200
    assert_visible_text(response, "Choose a unique category name.")
    assert tuple(source) == ("Pets", None, None)


def test_category_routes_protect_builtin_categories(client, core_conn):
    """Verify built-in categories cannot be created over, edited, or deleted."""
    token = set_csrf_token(client)
    unknown = core_conn.execute(text("""
        SELECT id, name, builtin_key, description, instruction
        FROM categories
        WHERE builtin_key = 'unknown'
        """)).fetchone()

    create_response = client.post(
        "/taxonomy/categories/create",
        data={
            CSRF_FIELD_NAME: token,
            "name": "UNKNOWN",
            "description": "Override",
            "instruction": "Override",
        },
        follow_redirects=True,
    )
    update_response = client.post(
        "/taxonomy/categories/update",
        data={
            CSRF_FIELD_NAME: token,
            "category_id": unknown._mapping["id"],
            "name": "UNCATEGORIZED",
            "description": "Override",
            "instruction": "Override",
        },
        follow_redirects=True,
    )
    delete_response = client.post(
        "/taxonomy/categories/delete",
        data={CSRF_FIELD_NAME: token, "category_id": unknown._mapping["id"]},
        follow_redirects=True,
    )

    current = core_conn.execute(
        text("""
        SELECT id, name, builtin_key, description, instruction
        FROM categories
        WHERE id = :p0
        """),
        {"p0": unknown._mapping["id"]},
    ).fetchone()
    assert create_response.status_code == 200
    assert_visible_text(create_response, "Built-in categories are managed by FinScope.")
    assert_visible_text(update_response, "Built-in categories cannot be modified.")
    assert_visible_text(delete_response, "Built-in categories cannot be deleted.")
    assert tuple(current) == tuple(unknown)


def test_tag_create_update_and_delete_routes(client, core_conn):
    """Verify tag create, update, and unused delete behavior."""
    token = set_csrf_token(client)

    create_response = client.post(
        "/taxonomy/tags/create",
        data={
            CSRF_FIELD_NAME: token,
            "name": "Audit",
            "description": "Needs audit",
            "instruction": "Use for transactions to inspect later.",
            "color": "#123abc",
        },
        follow_redirects=True,
    )
    tag = core_conn.execute(text("""
        SELECT id, name, description, instruction, color
        FROM tags
        WHERE name = 'Audit'
    """)).fetchone()
    assert create_response.status_code == 200
    assert_visible_text(create_response, "Tag saved: Audit")
    assert tuple(tag[1:]) == (
        "Audit",
        "Needs audit",
        "Use for transactions to inspect later.",
        "#123abc",
    )

    update_response = client.post(
        "/taxonomy/tags/update",
        data={
            CSRF_FIELD_NAME: token,
            "tag_id": tag._mapping["id"],
            "name": "Reviewed",
            "description": "Reviewed later",
            "instruction": "Use after manual inspection.",
            "color": "#abcdef",
        },
        follow_redirects=True,
    )
    updated = core_conn.execute(
        text("""
        SELECT id, name, description, instruction, color
        FROM tags
        WHERE id = :p0
        """),
        {"p0": tag._mapping["id"]},
    ).fetchone()
    assert update_response.status_code == 200
    assert_visible_text(update_response, "Tag updated: Reviewed")
    assert tuple(updated[1:]) == (
        "Reviewed",
        "Reviewed later",
        "Use after manual inspection.",
        "#abcdef",
    )

    delete_response = client.post(
        "/taxonomy/tags/delete",
        data={
            CSRF_FIELD_NAME: token,
            "tag_id": tag._mapping["id"],
        },
        follow_redirects=True,
    )
    remaining = (
        core_conn.execute(
            text("SELECT COUNT(*) AS count FROM tags WHERE id = :p0"),
            {"p0": tag._mapping["id"]},
        )
        .fetchone()
        ._mapping["count"]
    )
    assert delete_response.status_code == 200
    assert_visible_text(delete_response, "Tag deleted: Reviewed")
    assert remaining == 0


def test_tag_routes_protect_builtin_tags(client, core_conn):
    """Verify built-in tags cannot be created over, edited, or deleted."""
    token = set_csrf_token(client)
    reimbursable = core_conn.execute(text("""
        SELECT id, name, builtin_key, description, instruction, color
        FROM tags
        WHERE builtin_key = 'reimbursable'
        """)).fetchone()

    create_response = client.post(
        "/taxonomy/tags/create",
        data={
            CSRF_FIELD_NAME: token,
            "name": "Reimbursable",
            "description": "Override",
            "instruction": "Override",
            "color": "#000000",
        },
        follow_redirects=True,
    )
    update_response = client.post(
        "/taxonomy/tags/update",
        data={
            CSRF_FIELD_NAME: token,
            "tag_id": reimbursable._mapping["id"],
            "name": "Repayable",
            "description": "Override",
            "instruction": "Override",
            "color": "#000000",
        },
        follow_redirects=True,
    )
    delete_response = client.post(
        "/taxonomy/tags/delete",
        data={CSRF_FIELD_NAME: token, "tag_id": reimbursable._mapping["id"]},
        follow_redirects=True,
    )

    current = core_conn.execute(
        text("""
        SELECT id, name, builtin_key, description, instruction, color
        FROM tags
        WHERE id = :p0
        """),
        {"p0": reimbursable._mapping["id"]},
    ).fetchone()
    assert create_response.status_code == 200
    assert_visible_text(create_response, "Built-in tags are managed by FinScope.")
    assert_visible_text(update_response, "Built-in tags cannot be modified.")
    assert_visible_text(delete_response, "Built-in tags cannot be deleted.")
    assert tuple(current) == tuple(reimbursable)


def test_tag_update_route_rejects_name_conflict(client, core_conn):
    """Verify tag updates cannot collide with another tag name."""
    first_id = insert_tag(core_conn, "Audit")
    second_id = insert_tag(core_conn, "Reviewed")

    response = client.post(
        "/taxonomy/tags/update",
        data={
            CSRF_FIELD_NAME: set_csrf_token(client),
            "tag_id": first_id,
            "name": "Reviewed",
            "description": "Conflict",
            "instruction": "Conflict",
            "color": "#123456",
        },
        follow_redirects=True,
    )

    first = core_conn.execute(text("SELECT name FROM tags WHERE id = :p0"), {"p0": first_id}).fetchone()
    second = core_conn.execute(text("SELECT name FROM tags WHERE id = :p0"), {"p0": second_id}).fetchone()
    assert response.status_code == 200
    assert_visible_text(response, "Choose a unique tag name.")
    assert first._mapping["name"] == "Audit"
    assert second._mapping["name"] == "Reviewed"


def test_tag_delete_route_blocks_transaction_and_rule_usage(client, core_conn):
    """Verify used tags cannot be deleted when attached to transactions or rules."""
    transaction_tag_id = insert_tag(core_conn, "Transaction Used")
    rule_tag_id = insert_tag(core_conn, "Rule Used")
    tx_id = core_conn.execute(text("""
        INSERT INTO transactions (tx_date, description, amount, category, fingerprint)
        VALUES ('2026-01-02', 'STORE', 12.34, 'UNKNOWN', 'tag-used-tx')
        """)).lastrowid
    rule_id = core_conn.execute(text("""
        INSERT INTO category_rules (keyword, category)
        VALUES ('STORE', 'Food')
        """)).lastrowid
    core_conn.execute(
        text("""
        INSERT INTO transaction_tags (transaction_id, tag_id)
        VALUES (:p0, :p1)
        """),
        {"p0": tx_id, "p1": transaction_tag_id},
    )
    core_conn.execute(
        text("""
        INSERT INTO category_rule_tags (rule_id, tag_id)
        VALUES (:p0, :p1)
        """),
        {"p0": rule_id, "p1": rule_tag_id},
    )
    core_conn.commit()
    token = set_csrf_token(client)

    transaction_response = client.post(
        "/taxonomy/tags/delete",
        data={CSRF_FIELD_NAME: token, "tag_id": transaction_tag_id},
        follow_redirects=True,
    )
    rule_response = client.post(
        "/taxonomy/tags/delete",
        data={CSRF_FIELD_NAME: token, "tag_id": rule_tag_id},
        follow_redirects=True,
    )

    remaining = (
        core_conn.execute(
            text("""
        SELECT COUNT(*) AS count
    FROM tags
    WHERE id IN (:p0, :p1)
        """),
            {"p0": transaction_tag_id, "p1": rule_tag_id},
        )
        .fetchone()
        ._mapping["count"]
    )
    assert_visible_text(transaction_response, "Only unused tags can be deleted.")
    assert_visible_text(rule_response, "Only unused tags can be deleted.")
    assert remaining == 2
