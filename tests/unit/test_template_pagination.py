"""Static checks for shared pagination template usage."""

from pathlib import Path

from flask import render_template_string
from tests.support.html import parse_html

ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = ROOT / "src" / "finance_app" / "templates"


def test_pagination_markup_lives_in_shared_partial():
    """Verify page templates use the shared pagination macro instead of local markup."""
    offenders = []
    for template_path in TEMPLATES.rglob("*.html"):
        if template_path.name == "_pagination.html":
            continue
        template = template_path.read_text(encoding="utf-8")
        if '<ul class="pagination' in template or 'class="page-item' in template:
            offenders.append(str(template_path.relative_to(ROOT)))

    assert offenders == []


def test_paginated_templates_import_shared_partial():
    """Verify server-rendered pagination pages import the shared macro."""
    paginated_templates = [
        "jobs.html",
        "review.html",
        "rules.html",
        "rules_audit.html",
        "rules_audit_overlap.html",
        "transactions.html",
        "upload.html",
    ]

    for template_name in paginated_templates:
        template = (TEMPLATES / template_name).read_text(encoding="utf-8")
        assert 'from "_pagination.html" import pagination with context' in template


def render_pagination(app, page):
    """Render shared pagination with a URL helper that rejects invalid pages."""

    def page_url(page_number):
        if page_number < 1 or page_number > 3:
            raise AssertionError(f"Unexpected pagination URL for page {page_number}")
        return f"/items?page={page_number}"

    with app.test_request_context("/items"):
        return render_template_string(
            """
            {% from "_pagination.html" import pagination with context %}
            {{ pagination(page, 3, page_url, "Item pages") }}
            """,
            page=page,
            page_url=page_url,
        )


def test_pagination_macro_marks_current_page_and_disabled_previous(app):
    """Verify first-page pagination exposes current and disabled states."""
    document = parse_html(render_pagination(app, 1))

    assert document.has_element("nav", attrs={"aria-label": "Item pages"})
    assert document.has_element(
        "a",
        attrs={"class": "page-link", "href": "/items?page=1", "aria-current": "page"},
        text="1",
    )
    assert document.has_element(
        "span",
        attrs={"class": "page-link", "aria-disabled": "true"},
        text="Previous",
    )
    assert not document.has_element("a", text="Previous")
    assert document.has_element(
        "a",
        attrs={"class": "page-link", "href": "/items?page=2"},
        text="Next",
    )


def test_pagination_macro_disables_next_without_invalid_href(app):
    """Verify last-page pagination disables Next without linking past the end."""
    document = parse_html(render_pagination(app, 3))

    assert document.has_element(
        "a",
        attrs={"class": "page-link", "href": "/items?page=3", "aria-current": "page"},
        text="3",
    )
    assert document.has_element(
        "a",
        attrs={"class": "page-link", "href": "/items?page=2"},
        text="Previous",
    )
    assert document.has_element(
        "span",
        attrs={"class": "page-link", "aria-disabled": "true"},
        text="Next",
    )
    assert not document.has_element("a", text="Next")
