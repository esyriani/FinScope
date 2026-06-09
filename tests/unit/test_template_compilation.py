"""Compile checks for first-party Jinja templates."""

from jinja2 import meta


def html_template_names(app):
    """Return sorted first-party HTML templates from the configured loader."""
    return sorted(app.jinja_env.list_templates(extensions=["html"]))


def test_application_templates_compile(app):
    """Verify every first-party Jinja template parses and compiles."""
    errors = []

    for template_name in html_template_names(app):
        try:
            app.jinja_env.get_template(template_name)
        except Exception as exc:  # pragma: no cover - failure path reports template names.
            errors.append(f"{template_name}: {exc}")

    assert errors == []


def test_static_template_references_resolve(app):
    """Verify static inheritance, include, and import targets are present."""
    errors = []

    for template_name in html_template_names(app):
        source, _, _ = app.jinja_env.loader.get_source(app.jinja_env, template_name)
        parsed = app.jinja_env.parse(source)
        for referenced_name in meta.find_referenced_templates(parsed):
            if referenced_name is None:
                continue
            try:
                app.jinja_env.get_template(referenced_name)
            except Exception as exc:  # pragma: no cover - failure path reports template names.
                errors.append(f"{template_name} -> {referenced_name}: {exc}")

    assert errors == []
