"""Route and service tests for taxonomy admin mutations."""

from finance_app.core.csrf import CSRF_FIELD_NAME, CSRF_SESSION_KEY


def set_csrf_token(client, token="test-csrf-token"):
    """Store a CSRF token in the test client's session."""
    with client.session_transaction() as session:
        session[CSRF_SESSION_KEY] = token
    return token


def insert_category(conn, name):
    """Insert a category and return its id."""
    category_id = conn.execute(
        """
        INSERT INTO categories (name)
        VALUES (?)
        """,
        (name,),
    ).lastrowid
    conn.commit()
    return category_id


def insert_tag(conn, name, color="#64748b"):
    """Insert a tag and return its id."""
    tag_id = conn.execute(
        """
        INSERT INTO tags (name, color)
        VALUES (?, ?)
        """,
        (name, color),
    ).lastrowid
    conn.commit()
    return tag_id


def test_category_update_route_rejects_rename_conflict(client, db_conn):
    """Verify category renames cannot collide with an existing category."""
    source_id = insert_category(db_conn, "Pets")
    insert_category(db_conn, "Food Delivery")

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

    source = db_conn.execute(
        """
        SELECT name, description, instruction
        FROM categories
        WHERE id = ?
        """,
        (source_id,),
    ).fetchone()
    assert response.status_code == 200
    assert b"Choose a unique category name." in response.data
    assert tuple(source) == ("Pets", None, None)


def test_category_routes_protect_builtin_categories(client, db_conn):
    """Verify built-in categories cannot be created over, edited, or deleted."""
    token = set_csrf_token(client)
    unknown = db_conn.execute(
        """
        SELECT id, name, builtin_key, description, instruction
        FROM categories
        WHERE builtin_key = 'unknown'
        """
    ).fetchone()

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
            "category_id": unknown["id"],
            "name": "UNCATEGORIZED",
            "description": "Override",
            "instruction": "Override",
        },
        follow_redirects=True,
    )
    delete_response = client.post(
        "/taxonomy/categories/delete",
        data={CSRF_FIELD_NAME: token, "category_id": unknown["id"]},
        follow_redirects=True,
    )

    current = db_conn.execute(
        """
        SELECT id, name, builtin_key, description, instruction
        FROM categories
        WHERE id = ?
        """,
        (unknown["id"],),
    ).fetchone()
    assert create_response.status_code == 200
    assert b"Built-in categories are managed by FinScope." in create_response.data
    assert b"Built-in categories cannot be modified." in update_response.data
    assert b"Built-in categories cannot be deleted." in delete_response.data
    assert tuple(current) == tuple(unknown)


def test_tag_create_update_and_delete_routes(client, db_conn):
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
    tag = db_conn.execute(
        """
        SELECT id, name, description, instruction, color
        FROM tags
        WHERE name = 'Audit'
        """
    ).fetchone()
    assert create_response.status_code == 200
    assert b"Tag saved: Audit" in create_response.data
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
            "tag_id": tag["id"],
            "name": "Reviewed",
            "description": "Reviewed later",
            "instruction": "Use after manual inspection.",
            "color": "#abcdef",
        },
        follow_redirects=True,
    )
    updated = db_conn.execute(
        """
        SELECT id, name, description, instruction, color
        FROM tags
        WHERE id = ?
        """,
        (tag["id"],),
    ).fetchone()
    assert update_response.status_code == 200
    assert b"Tag updated: Reviewed" in update_response.data
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
            "tag_id": tag["id"],
        },
        follow_redirects=True,
    )
    remaining = db_conn.execute(
        "SELECT COUNT(*) AS count FROM tags WHERE id = ?",
        (tag["id"],),
    ).fetchone()["count"]
    assert delete_response.status_code == 200
    assert b"Tag deleted: Reviewed" in delete_response.data
    assert remaining == 0


def test_tag_update_route_rejects_name_conflict(client, db_conn):
    """Verify tag updates cannot collide with another tag name."""
    first_id = insert_tag(db_conn, "Audit")
    second_id = insert_tag(db_conn, "Reviewed")

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

    first = db_conn.execute("SELECT name FROM tags WHERE id = ?", (first_id,)).fetchone()
    second = db_conn.execute("SELECT name FROM tags WHERE id = ?", (second_id,)).fetchone()
    assert response.status_code == 200
    assert b"Choose a unique tag name." in response.data
    assert first["name"] == "Audit"
    assert second["name"] == "Reviewed"


def test_tag_delete_route_blocks_transaction_and_rule_usage(client, db_conn):
    """Verify used tags cannot be deleted when attached to transactions or rules."""
    transaction_tag_id = insert_tag(db_conn, "Transaction Used")
    rule_tag_id = insert_tag(db_conn, "Rule Used")
    tx_id = db_conn.execute(
        """
        INSERT INTO transactions (tx_date, description, amount, category, fingerprint)
        VALUES ('2026-01-02', 'STORE', 12.34, 'UNKNOWN', 'tag-used-tx')
        """
    ).lastrowid
    rule_id = db_conn.execute(
        """
        INSERT INTO category_rules (keyword, category)
        VALUES ('STORE', 'Food')
        """
    ).lastrowid
    db_conn.execute(
        """
        INSERT INTO transaction_tags (transaction_id, tag_id)
        VALUES (?, ?)
        """,
        (tx_id, transaction_tag_id),
    )
    db_conn.execute(
        """
        INSERT INTO category_rule_tags (rule_id, tag_id)
        VALUES (?, ?)
        """,
        (rule_id, rule_tag_id),
    )
    db_conn.commit()
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

    remaining = db_conn.execute(
        """
        SELECT COUNT(*) AS count
        FROM tags
        WHERE id IN (?, ?)
        """,
        (transaction_tag_id, rule_tag_id),
    ).fetchone()["count"]
    assert b"Only unused tags can be deleted." in transaction_response.data
    assert b"Only unused tags can be deleted." in rule_response.data
    assert remaining == 2
