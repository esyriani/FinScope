"""Route tests for primary navigation and page-scoped assets.

Verifies that primary pages render and that shared templates expose local,
hashed browser assets without leaking feature-specific bundles onto unrelated
pages.
"""

import pytest

from tests.support.html import (
    assert_asset_reference,
    assert_no_asset_reference,
    asset_reference_index,
    asset_reference_values,
    response_html,
)


@pytest.mark.parametrize(
    "path",
    [
        "/",
        "/dashboard",
        "/comparison",
        "/calendar",
        "/recurring",
        "/review",
        "/transactions",
        "/rules",
        "/upload",
        "/jobs",
        "/taxonomy",
        "/settings",
    ],
)
def test_primary_get_routes_render_successfully(client, path):
    """Verify that primary navigation routes render against an empty database."""
    response = client.get(path)

    assert response.status_code == 200
    assert "<html" in response_html(response).lower()


def test_base_template_uses_local_hashed_assets(client):
    """Verify that shared browser assets are served locally with content hashes."""
    response = client.get("/")

    assert response.status_code == 200
    assert_no_asset_reference(response, "cdn.jsdelivr.net")
    assert all(not value.endswith("?v=1") for value in asset_reference_values(response))
    assert_asset_reference(
        response,
        r"/static/vendor/bootstrap/5\.3\.3/css/bootstrap\.min\.css\?v=[0-9a-f]{12}",
    )
    assert_asset_reference(response, r"/static/js/app-boot\.js\?v=[0-9a-f]{12}")
    assert_asset_reference(response, r"/static/js/core\.js\?v=[0-9a-f]{12}")


def test_base_template_keeps_feature_assets_page_scoped(client):
    """Verify the home page does not inherit feature assets from unrelated pages."""
    response = client.get("/")

    assert response.status_code == 200
    for snippet in (
        "vendor/flatpickr",
        "js/upload.js",
        "js/jobs.js",
        "js/rules.js",
        "js/review.js",
        "js/dashboard.js",
        "js/tables.js",
        "js/dates.js",
        "js/calendar.js",
        "js/recurring.js",
        "js/exports.js",
        "js/tag-multiselect.js",
        "css/comparison.css",
        "css/page-tabs.css",
        "css/calendar-recurring.css",
        "css/rules-list.css",
        "css/settings.css",
        "css/review.css",
    ):
        assert_no_asset_reference(response, snippet)


def test_dashboard_route_loads_dashboard_assets(client):
    """Verify dashboard-specific assets are declared by the dashboard page."""
    response = client.get("/dashboard")

    assert response.status_code == 200
    for pattern in (
        r"/static/vendor/flatpickr/4\.6\.13/flatpickr\.min\.css\?v=[0-9a-f]{12}",
        r"/static/vendor/flatpickr/4\.6\.13/flatpickr\.min\.js\?v=[0-9a-f]{12}",
        r"/static/vendor/echarts/5\.6\.0/echarts\.min\.js\?v=[0-9a-f]{12}",
        r"/static/js/dashboard\.js\?v=[0-9a-f]{12}",
        r"/static/js/chart-utils\.js\?v=[0-9a-f]{12}",
        r"/static/js/dashboard-charts\.js\?v=[0-9a-f]{12}",
    ):
        assert_asset_reference(response, pattern)
    assert asset_reference_index(response, r"/static/js/chart-utils\.js") < asset_reference_index(
        response,
        r"/static/js/dashboard-charts\.js",
    )


def test_tabbed_pages_load_shared_tab_stylesheet(client):
    """Verify tabbed pages opt into shared tab styles without loading them globally."""
    comparison_response = client.get("/comparison")
    settings_response = client.get("/settings")
    taxonomy_response = client.get("/taxonomy")

    assert comparison_response.status_code == 200
    assert settings_response.status_code == 200
    assert taxonomy_response.status_code == 200
    assert_asset_reference(comparison_response, r"/static/css/page-tabs\.css\?v=[0-9a-f]{12}")
    assert_asset_reference(settings_response, r"/static/css/page-tabs\.css\?v=[0-9a-f]{12}")
    assert_asset_reference(taxonomy_response, r"/static/css/page-tabs\.css\?v=[0-9a-f]{12}")
    assert asset_reference_index(comparison_response, r"/static/css/page-tabs\.css") < asset_reference_index(
        comparison_response,
        r"/static/css/comparison\.css",
    )
    assert asset_reference_index(settings_response, r"/static/css/page-tabs\.css") < asset_reference_index(
        settings_response,
        r"/static/css/settings\.css",
    )
    assert asset_reference_index(taxonomy_response, r"/static/css/page-tabs\.css") < asset_reference_index(
        taxonomy_response,
        r"/static/css/home-dashboard\.css",
    )
