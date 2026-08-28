"""Tests for taxonomy admin route registration."""

import io

from sqlalchemy import select
from tests.support.html import assert_has_element, assert_not_visible_text, assert_visible_text, parse_html
from tests.support.web import set_csrf_token

from finance_app.core.csrf import CSRF_FIELD_NAME
from finance_app.database.tables import categories as categories_table
from finance_app.database.tables import tags as tags_table


def test_taxonomy_admin_routes_are_registered_and_rules_category_routes_are_removed(app):
    """Verify taxonomy admin routes are registered and rule category routes are absent."""
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


def test_taxonomy_page_exposes_yaml_import_and_export_controls(owner_client):
    """Verify taxonomy import/export controls and category/tag tabs render."""
    response = owner_client.get("/taxonomy")
    document = parse_html(response)

    assert response.status_code == 200
    assert_visible_text(
        response,
        "Import categories and tags",
        "Export YAML",
        "Categories",
        "Tags",
        "Built-in",
    )
    assert_not_visible_text(
        response,
        "Affects reports",
        "Workflow ready",
        "Ordinary income",
        "Default category for ordinary income; income totals use transaction type.",
        "Excluded from ordinary income; allocations offset covered expenses.",
    )
    assert_has_element(response, "a", attrs={"href": "/taxonomy/export.yml"}, text="Export YAML")
    assert_has_element(response, "div", attrs={"id": "import-taxonomy-modal"})
    assert_has_element(response, "input", attrs={"name": "taxonomy_file"})
    assert_has_element(
        response,
        "div",
        attrs={"class": "taxonomy-tabs", "role": "tablist", "aria-label": "Categories and tags"},
    )
    assert_has_element(
        response,
        "button",
        attrs={
            "id": "taxonomy-categories-tab",
            "role": "tab",
            "data-bs-toggle": "tab",
            "data-bs-target": "#taxonomy-categories-panel",
            "aria-controls": "taxonomy-categories-panel",
            "aria-selected": "true",
        },
        text="Categories",
    )
    assert_has_element(
        response,
        "button",
        attrs={
            "id": "taxonomy-tags-tab",
            "role": "tab",
            "data-bs-toggle": "tab",
            "data-bs-target": "#taxonomy-tags-panel",
            "aria-controls": "taxonomy-tags-panel",
            "aria-selected": "false",
        },
        text="Tags",
    )
    assert_has_element(
        response,
        "section",
        attrs={
            "id": "taxonomy-categories-panel",
            "role": "tabpanel",
            "aria-labelledby": "taxonomy-categories-tab",
        },
    )
    assert_has_element(
        response,
        "section",
        attrs={
            "id": "taxonomy-tags-panel",
            "role": "tabpanel",
            "aria-labelledby": "taxonomy-tags-tab",
        },
    )
    assert not document.has_element(
        "button",
        attrs={"data-bs-target": "#create-category-modal", "class": "btn-primary"},
        text="New category",
    )
    assert not document.has_element(
        "button",
        attrs={"data-bs-target": "#create-tag-modal", "class": "btn-outline-primary"},
        text="New tag",
    )
    assert_has_element(response, "button", attrs={"data-bs-target": "#create-category-modal"}, text="Add")
    assert_has_element(response, "button", attrs={"data-bs-target": "#create-tag-modal"}, text="Add")


def test_taxonomy_tables_export_description_and_llm_instruction_separately(owner_client, core_conn):
    """Verify taxonomy table exports do not collapse metadata into the name column."""
    core_conn.execute(
        categories_table.insert().values(
            name="Export category",
            description="Category export description",
            instruction="Use this category for export coverage.",
        )
    )
    core_conn.execute(
        tags_table.insert().values(
            name="Export tag",
            color="#123abc",
            description="Tag export description",
            instruction="Use this tag for export coverage.",
        )
    )
    core_conn.commit()

    response = owner_client.get("/taxonomy")

    assert response.status_code == 200
    assert_has_element(response, "div", attrs={"data-export-part": True}, text="Export category")
    assert_has_element(response, "span", attrs={"data-export-part": True}, text="Export tag")
    assert_has_element(
        response,
        "span",
        attrs={
            "data-export-part": True,
            "data-export-label": "Description",
            "data-export-header": "Description",
            "data-export-text": "Category export description",
        },
    )
    assert_has_element(
        response,
        "span",
        attrs={
            "data-export-part": True,
            "data-export-label": "AI guidance",
            "data-export-header": "AI guidance",
            "data-export-text": "Use this category for export coverage.",
        },
    )
    assert_has_element(
        response,
        "span",
        attrs={
            "data-export-part": True,
            "data-export-label": "Description",
            "data-export-header": "Description",
            "data-export-text": "Tag export description",
        },
    )
    assert_has_element(
        response,
        "span",
        attrs={
            "data-export-part": True,
            "data-export-label": "AI guidance",
            "data-export-header": "AI guidance",
            "data-export-text": "Use this tag for export coverage.",
        },
    )


def test_taxonomy_export_route_returns_yaml(owner_client):
    """Verify taxonomy YAML export includes category and tag metadata."""
    response = owner_client.get("/taxonomy/export.yml")
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert response.mimetype == "application/x-yaml"
    assert "taxonomy.yml" in response.headers["Content-Disposition"]
    assert "categories:" in body
    assert "tags:" in body
    assert 'name: "Income"' in body
    assert 'name: "Reimbursable"' in body
    assert 'builtin_key: "income"' in body
    assert 'builtin_key: "reimbursable"' in body
    assert "builtin_key:" in body
    assert "color:" in body


def test_taxonomy_import_route_upserts_yaml_metadata(owner_client, core_conn):
    """Verify taxonomy YAML import creates and updates category and tag metadata."""
    token = set_csrf_token(owner_client)
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

    response = owner_client.post(
        "/taxonomy/import",
        data={
            CSRF_FIELD_NAME: token,
            "taxonomy_file": (io.BytesIO(payload), "taxonomy.yml"),
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    category = (
        core_conn.execute(
            select(
                categories_table.c.name,
                categories_table.c.description,
                categories_table.c.instruction,
            ).where(categories_table.c.name == "Custom admin")
        )
        .mappings()
        .fetchone()
    )
    tag = (
        core_conn.execute(
            select(
                tags_table.c.name,
                tags_table.c.description,
                tags_table.c.instruction,
                tags_table.c.color,
            ).where(tags_table.c.name == "Audit trail")
        )
        .mappings()
        .fetchone()
    )

    assert response.status_code == 200
    assert "Imported 1 categories and 1 tags." in response.get_data(as_text=True)
    assert category["description"] == "Administrative category from YAML"
    assert category["instruction"] == "Use for custom admin rows."
    assert tag["description"] == "Imported tag"
    assert tag["instruction"] == "Use when audit context is relevant."
    assert tag["color"] == "#123abc"


def test_taxonomy_import_route_skips_builtin_tag_metadata(owner_client, core_conn):
    """Verify taxonomy YAML import cannot mutate built-in tag definitions."""
    token = set_csrf_token(owner_client)
    original = (
        core_conn.execute(
            select(tags_table.c.id, tags_table.c.description, tags_table.c.instruction, tags_table.c.color).where(
                tags_table.c.builtin_key == "reimbursable"
            )
        )
        .mappings()
        .one()
    )
    payload = b"""
tags:
  - name: "Reimbursable"
    description: "Override"
    instruction: "Override"
    color: "#000000"
    builtin_key: "reimbursable"
"""

    response = owner_client.post(
        "/taxonomy/import",
        data={
            CSRF_FIELD_NAME: token,
            "taxonomy_file": (io.BytesIO(payload), "taxonomy.yml"),
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    current = (
        core_conn.execute(
            select(tags_table.c.id, tags_table.c.description, tags_table.c.instruction, tags_table.c.color).where(
                tags_table.c.builtin_key == "reimbursable"
            )
        )
        .mappings()
        .one()
    )

    assert response.status_code == 200
    assert_visible_text(response, "Only built-in categories or tags were found. Nothing was imported.")
    assert dict(current) == dict(original)
