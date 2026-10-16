"""Dashboard presentation and shared chart-control HTTP contracts.

Use rendered HTML and shared database factories to verify scoped controls,
asset ordering, and chart export metadata without running external providers.
"""

from tests.support.database import insert_transaction
from tests.support.html import (
    assert_asset_reference,
    assert_has_element,
    assert_markup,
    assert_no_element,
    assert_not_markup,
    assert_not_visible_text,
    assert_visible_text,
    asset_reference_index,
)


def test_dashboard_renders_scoped_trends_and_shared_chart_controls(owner_client, core_conn):
    """Show export-enabled trend charts while keeping assignment-only controls out of filters."""
    insert_transaction(
        core_conn,
        tx_date="2026-01-02",
        description="Dashboard Store",
        amount=12.34,
        category="Food",
        category_source="rule",
        needs_review=0,
        fingerprint="route-dashboard-tooltip",
    )

    response = owner_client.get("/dashboard?period=all")
    tag_response = owner_client.get("/dashboard?period=all&tags=Tax")
    untagged_response = owner_client.get("/dashboard?period=all&quick_view=all")

    assert response.status_code == 200
    assert tag_response.status_code == 200
    assert untagged_response.status_code == 200
    assert_has_element(response, "div", attrs={"role": "radiogroup", "aria-label": "Scope"})
    assert_has_element(
        response, "input", attrs={"type": "radio", "name": "quick_view", "value": "categorized", "checked": True}
    )
    assert_has_element(response, "input", attrs={"type": "radio", "name": "quick_view", "value": "all"})
    assert_no_element(response, "input", attrs={"type": "hidden", "name": "quick_view"})
    assert_not_markup(response, "data-category-description-select")
    assert_no_element(response, None, attrs={"data-select-all-label": "Select all categories"})
    assert_no_element(response, None, attrs={"data-select-all-label": "Select all tags"})
    assert_visible_text(
        response,
        "What happened?",
        "Analysis readiness",
        "Trend preview",
        "Top drivers",
        "Explore reports",
        "Overview",
        "Income and credits",
        "Categories and tags",
        "Open report",
    )
    assert_not_visible_text(
        tag_response,
        "Spending by tag",
        "Tag detail",
        "Tagged spending can count the same transaction more than once.",
        "Show untagged",
    )
    assert_not_visible_text(response, "Choose filters")
    assert_markup(response, 'name="merchant_query"', 'placeholder="Search merchant"', "data-merchant-autocomplete")
    assert_not_markup(response, "data-dashboard-custom-categories", "data-dashboard-custom-tags")
    assert_markup(response, 'href="/reports?period=all&amp;quick_view=categorized"')
    assert_markup(response, 'id="dashboard-chart-data"')
    for chart_id, title in (("netChart", "Net cash flow"), ("spendingIncomeChart", "Spending vs income")):
        assert_has_element(
            response, "div", attrs={"id": chart_id, "data-chart-export": True, "data-export-title": title}
        )
    assert_asset_reference(response, r"/static/css/exports\.css\?v=[0-9a-f]{12}")
    assert_asset_reference(response, r"/static/js/exports\.js\?v=[0-9a-f]{12}")
    assert asset_reference_index(response, r"/static/js/dashboard-charts\.js\?v=[0-9a-f]{12}") < asset_reference_index(
        response, r"/static/js/exports\.js\?v=[0-9a-f]{12}"
    )
    assert_not_markup(tag_response, '"categoryLabels"', "data-sortable-table", "data-table-export-scope")
    assert_not_visible_text(untagged_response, "Hide untagged")


def test_dashboard_keeps_review_guidance_when_categorized_scope_is_empty(owner_client, core_conn):
    """Show full-period readiness without plotting excluded unknown transactions."""
    insert_transaction(core_conn, amount=20, fingerprint="route-dashboard-unknown")

    response = owner_client.get("/dashboard?period=all&quick_view=categorized")

    assert response.status_code == 200
    assert_visible_text(
        response,
        "$0.00",
        "Categorized: 0",
        "All: 1",
        "Analysis readiness",
        "1 unknown transaction needs review.",
        "No trend data in this view.",
    )
    assert_not_visible_text(response, "No transaction data yet. Upload a CSV statement first.")
    assert_no_element(response, "div", attrs={"id": "netChart"})
    assert_no_element(response, "div", attrs={"id": "spendingIncomeChart"})
