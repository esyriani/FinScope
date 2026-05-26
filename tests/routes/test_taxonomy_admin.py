"""Tests for taxonomy admin route registration."""

import io

from sqlalchemy import select

from finance_app.core.csrf import CSRF_FIELD_NAME, CSRF_SESSION_KEY
from finance_app.database.tables import categories as categories_table, tags as tags_table


def set_csrf_token(client, token="test-csrf-token"):
    """Store a CSRF token in the test client's session."""
    with client.session_transaction() as session:
        session[CSRF_SESSION_KEY] = token
    return token


def test_taxonomy_admin_routes_are_registered_and_rules_category_routes_are_removed(app):
    """Verify taxonomy admin routes are registered and legacy rules category routes are absent."""
    routes = {str(rule.rule) for rule in app.url_map.iter_rules()}

    assert "/taxonomy" in routes
    assert "/taxonomy/export.yml" in routes
    assert "/taxonomy/import" in routes
    assert "/taxonomy/categories/create" in routes
    assert "/taxonomy/categories/update" in routes
    assert "/taxonomy/categories/delete" in routes
    assert "/taxonomy/tags/create" in routes
    assert "/taxonomy/tags/update" in routes
    assert "/rules/categories/create" not in routes
    assert "/rules/categories/rename" not in routes


def test_taxonomy_page_exposes_yaml_import_and_export_controls(client):
    """Verify taxonomy import and export controls are rendered."""
    response = client.get("/taxonomy")
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Import taxonomy" in body
    assert "Export YAML" in body
    assert "/taxonomy/export.yml" in body
    assert "import-taxonomy-modal" in body
    assert "taxonomy_file" in body


def test_taxonomy_export_route_returns_yaml(client):
    """Verify taxonomy YAML export includes category and tag metadata."""
    response = client.get("/taxonomy/export.yml")
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert response.mimetype == "application/x-yaml"
    assert "taxonomy.yml" in response.headers["Content-Disposition"]
    assert "categories:" in body
    assert "tags:" in body
    assert 'name: "Income"' in body
    assert 'name: "Reimbursable"' in body
    assert "builtin_key:" in body
    assert "color:" in body


def test_taxonomy_import_route_upserts_yaml_metadata(client, db_conn):
    """Verify taxonomy YAML import creates and updates category and tag metadata."""
    token = set_csrf_token(client)
    payload = b"""
categories:
  - name: "Custom admin"
    description: "Administrative category from YAML"
    instruction: "Use for custom admin rows."
tags:
  - name: "Audit trail"
    description: "Imported tag"
    instruction: "Use when audit context is relevant."
    color: "#123abc"
"""

    response = client.post(
        "/taxonomy/import",
        data={
            CSRF_FIELD_NAME: token,
            "taxonomy_file": (io.BytesIO(payload), "taxonomy.yml"),
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    category = db_conn.execute(
        select(
            categories_table.c.name,
            categories_table.c.description,
            categories_table.c.instruction,
        ).where(categories_table.c.name == "Custom admin")
    ).mappings().fetchone()
    tag = db_conn.execute(
        select(
            tags_table.c.name,
            tags_table.c.description,
            tags_table.c.instruction,
            tags_table.c.color,
        ).where(tags_table.c.name == "Audit trail")
    ).mappings().fetchone()

    assert response.status_code == 200
    assert "Imported 1 categories and 1 tags." in response.get_data(as_text=True)
    assert category["description"] == "Administrative category from YAML"
    assert category["instruction"] == "Use for custom admin rows."
    assert tag["description"] == "Imported tag"
    assert tag["instruction"] == "Use when audit context is relevant."
    assert tag["color"] == "#123abc"
