"""Route-level tests for merchant lookup and analytics filters."""

from tests.support.database import insert_merchant, insert_transaction, set_owner_setting
from tests.support.html import (
    assert_has_element,
    assert_markup,
    assert_not_visible_text,
    assert_visible_text,
    parse_html,
    response_html,
)


def test_merchant_suggestions_require_authentication(anonymous_client):
    """Verify merchant suggestions are available only to signed-in users."""
    response = anonymous_client.get("/merchants/suggestions?q=metro")

    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_merchant_suggestions_match_partials_and_apply_limit(client, core_conn):
    """Verify merchant suggestions search partial text and honor the result cap."""
    first_id = insert_merchant(core_conn, "METRO GROCERY")
    insert_merchant(core_conn, "METRO PHARMACY")
    insert_merchant(core_conn, "HYDRO QUEBEC")

    response = client.get("/merchants/suggestions?q=metro&limit=1")
    payload = response.get_json()

    assert response.status_code == 200
    assert payload == {
        "ok": True,
        "query": "metro",
        "suggestions": [
            {
                "id": first_id,
                "label": "METRO GROCERY",
                "value": "METRO GROCERY",
            }
        ],
    }


def test_merchant_suggestions_match_spaced_partial_text(client, core_conn):
    """Verify merchant suggestions match all words in a spaced query."""
    merchant_id = insert_merchant(core_conn, "UDEM PAYROLL PAIE")
    insert_merchant(core_conn, "UDEM BOOKSTORE")
    insert_merchant(core_conn, "PAIE SERVICES")

    response = client.get("/merchants/suggestions?q=UDEM+PAIE")
    payload = response.get_json()

    assert response.status_code == 200
    assert payload["suggestions"] == [
        {
            "id": merchant_id,
            "label": "UDEM PAYROLL PAIE",
            "value": "UDEM PAYROLL PAIE",
        }
    ]


def test_merchant_suggestions_honor_configured_limit(client, core_conn):
    """Verify the runtime setting caps returned merchant suggestions."""
    set_owner_setting(core_conn, "merchant_suggestion_limit", "2")
    first_id = insert_merchant(core_conn, "UDEM ALPHA")
    second_id = insert_merchant(core_conn, "UDEM BETA")
    insert_merchant(core_conn, "UDEM GAMMA")

    response = client.get("/merchants/suggestions?q=udem&limit=10")
    payload = response.get_json()

    assert response.status_code == 200
    assert payload["suggestions"] == [
        {
            "id": first_id,
            "label": "UDEM ALPHA",
            "value": "UDEM ALPHA",
        },
        {
            "id": second_id,
            "label": "UDEM BETA",
            "value": "UDEM BETA",
        },
    ]


def test_merchant_suggestions_return_json_for_markup_names(client, core_conn):
    """Verify suggestion labels remain JSON data when merchant names contain markup."""
    merchant_id = insert_merchant(core_conn, "ACME <SCRIPT>")

    response = client.get("/merchants/suggestions?q=acme")
    payload = response.get_json()

    assert response.status_code == 200
    assert response.is_json
    assert payload["suggestions"] == [
        {
            "id": merchant_id,
            "label": "ACME <SCRIPT>",
            "value": "ACME <SCRIPT>",
        }
    ]


def test_dashboard_route_preserves_selected_merchant_filter(client, core_conn):
    """Verify dashboard renders the merchant autocomplete state from query args."""
    merchant_id = insert_merchant(core_conn, "METRO <GROCERY>")
    insert_transaction(
        core_conn,
        "Card purchase",
        42.00,
        "Food",
        merchant_id=merchant_id,
        tx_date="2026-01-05",
        fingerprint="dashboard-route-selected-merchant",
        category_source="rule",
        needs_review=0,
    )

    response = client.get(f"/dashboard?period=all&merchant_id={merchant_id}&merchant_query=METRO+%3CGROCERY%3E")
    body = response_html(response)

    assert response.status_code == 200
    assert_has_element(
        response,
        "div",
        attrs={
            "data-merchant-autocomplete": True,
            "data-suggestions-url": "/merchants/suggestions",
        },
    )
    assert_has_element(response, "input", attrs={"type": "hidden", "name": "merchant_id", "value": merchant_id})
    assert_has_element(
        response,
        "input",
        attrs={
            "id": "dashboard-merchant-search",
            "name": "merchant_query",
            "value": "METRO <GROCERY>",
            "data-selected-merchant-label": "METRO <GROCERY>",
        },
    )
    assert_markup(response, "js/merchant-autocomplete.js")
    assert "METRO &lt;GROCERY&gt;" in body
    assert "METRO <GROCERY>" not in body
    assert_has_element(response, "div", attrs={"data-suggestions-limit": "5"})


def test_dashboard_route_filters_spaced_merchant_query(client, core_conn):
    """Verify dashboard analytics apply multi-word typed merchant filters."""
    insert_transaction(
        core_conn,
        "UDEM - PAIE payroll",
        15.00,
        "Food",
        tx_date="2026-01-05",
        fingerprint="dashboard-route-udem-paie",
        category_source="rule",
        needs_review=0,
    )
    insert_transaction(
        core_conn,
        "UDEM Bookstore",
        99.00,
        "Food",
        tx_date="2026-01-06",
        fingerprint="dashboard-route-udem-bookstore",
        category_source="rule",
        needs_review=0,
    )

    response = client.get("/dashboard?period=all&merchant_query=UDEM+PAIE")

    assert response.status_code == 200
    assert_visible_text(response, "Merchant: UDEM PAIE", "UDEM PAIE PAYROLL")
    assert_not_visible_text(response, "UDEM BOOKSTORE")


def test_dashboard_merchant_analytics_exports_structured_cell_parts(client, core_conn):
    """Verify merchant analytics exports split visible details into separate fields."""
    merchant_id = insert_merchant(core_conn, "METRO GROCERY")
    insert_transaction(
        core_conn,
        "Card purchase 1234 METRO",
        42.00,
        "Food",
        merchant_id=merchant_id,
        tx_date="2026-01-05",
        fingerprint="dashboard-route-export-parts",
        category_source="rule",
        needs_review=0,
    )

    response = client.get("/dashboard?period=all")

    assert response.status_code == 200
    assert_has_element(response, "strong", attrs={"data-export-part": True}, text="METRO GROCERY")
    assert_has_element(
        response,
        "div",
        attrs={
            "data-export-part": True,
            "data-export-label": "Description",
            "data-export-header": "Description",
        },
        text="Card purchase 1234 METRO",
    )
    assert_has_element(
        response,
        "span",
        attrs={
            "class": "spending-cell-amount",
            "data-export-part": True,
            "data-export-type": "money",
            "data-export-value": "42.0",
        },
    )
    assert_has_element(
        response,
        "div",
        attrs={"data-export-part": True, "data-export-label": "Details"},
        text="No comparison",
    )


def test_comparison_route_preserves_merchant_filter_in_both_tabs(client, core_conn):
    """Verify comparison renders shared merchant filters for period and year views."""
    merchant_id = insert_merchant(core_conn, "METRO GROCERY")
    insert_transaction(
        core_conn,
        "Card purchase",
        42.00,
        "Food",
        merchant_id=merchant_id,
        tx_date="2026-01-05",
        fingerprint="comparison-route-selected-merchant",
        category_source="rule",
        needs_review=0,
    )

    response = client.get(f"/comparison?merchant_id={merchant_id}&merchant_query=METRO+GROCERY")
    body = response_html(response)
    document = parse_html(response)
    merchant_id_inputs = [
        element
        for element in document.find_all("input", attrs={"name": "merchant_id", "value": merchant_id})
        if "data-merchant-autocomplete-id" in element.attrs
    ]

    assert response.status_code == 200
    assert_has_element(
        response,
        "input",
        attrs={
            "id": "comparison-period-merchant",
            "name": "merchant_query",
            "value": "METRO GROCERY",
            "data-selected-merchant-label": "METRO GROCERY",
        },
    )
    assert_has_element(
        response,
        "input",
        attrs={
            "id": "comparison-year-merchant",
            "name": "merchant_query",
            "value": "METRO GROCERY",
            "data-selected-merchant-label": "METRO GROCERY",
        },
    )
    assert len(merchant_id_inputs) == 2
    assert body.count('data-suggestions-url="/merchants/suggestions"') == 2
    assert body.count('data-suggestions-limit="5"') == 2
    assert "Merchant: METRO GROCERY" in body
    assert_markup(response, "js/merchant-autocomplete.js")


def test_comparison_route_filters_spaced_merchant_query(client, core_conn):
    """Verify comparison analytics apply multi-word typed merchant filters."""
    for description, amount, tx_date, fingerprint in [
        ("UDEM - PAIE payroll", 15.00, "2026-05-04", "comparison-route-udem-paie-current"),
        ("UDEM PAIE tuition", 7.00, "2026-04-04", "comparison-route-udem-paie-prior"),
        ("UDEM Bookstore", 99.00, "2026-05-05", "comparison-route-udem-bookstore"),
    ]:
        insert_transaction(
            core_conn,
            description,
            amount,
            "Food",
            tx_date=tx_date,
            fingerprint=fingerprint,
            category_source="rule",
            needs_review=0,
        )

    response = client.get(
        "/comparison?merchant_query=UDEM+PAIE&period_comparison=month_previous&years=2026&comparison_view=period"
    )

    assert response.status_code == 200
    assert_visible_text(response, "Merchant: UDEM PAIE", "UDEM PAIE PAYROLL")
    assert_not_visible_text(response, "UDEM BOOKSTORE")
