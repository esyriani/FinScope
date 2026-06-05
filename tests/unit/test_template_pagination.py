"""Static checks for shared pagination template usage."""

from pathlib import Path


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
