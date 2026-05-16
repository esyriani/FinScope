"""Tests for taxonomy admin route registration."""


def test_taxonomy_admin_routes_are_registered_and_rules_category_routes_are_removed(app):
    """Verify taxonomy admin routes are registered and legacy rules category routes are absent."""
    routes = {str(rule.rule) for rule in app.url_map.iter_rules()}

    assert "/taxonomy" in routes
    assert "/taxonomy/categories/create" in routes
    assert "/taxonomy/categories/update" in routes
    assert "/taxonomy/categories/delete" in routes
    assert "/taxonomy/tags/create" in routes
    assert "/taxonomy/tags/update" in routes
    assert "/rules/categories/create" not in routes
    assert "/rules/categories/rename" not in routes
