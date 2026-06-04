"""Route-level tests for the Flask application."""

from sqlalchemy import text
from datetime import date as real_date
import io
import re

import pytest

from finance_app.core.csrf import CSRF_FIELD_NAME
from finance_app.modules.comparison import service as comparison_service
from tests.support.html import (
    assert_has_element,
    assert_markup,
    assert_not_markup,
    assert_not_visible_text,
    assert_visible_text,
    parse_html,
    response_html,
    visible_html,
)
from tests.support.web import set_csrf_token


class FixedDate(real_date):
    """Fixed replacement for date.today in comparison route tests."""

    @classmethod
    def today(cls):
        """Return a deterministic current date."""
        return cls(2026, 5, 11)


def asset_reference_values(response):
    """Return parsed src and href asset references from a route response."""
    document = parse_html(response)
    values = []
    for element in document.elements:
        for attr_name in ("src", "href"):
            value = element.attrs.get(attr_name)
            if value:
                values.append(value)
    return values


def assert_asset_reference(response, pattern):
    """Assert that a parsed asset reference matches a regular expression."""
    assert any(re.search(pattern, value) for value in asset_reference_values(response))


def asset_reference_index(response, pattern):
    """Return the first parsed asset reference index matching a regular expression."""
    for index, value in enumerate(asset_reference_values(response)):
        if re.search(pattern, value):
            return index
    raise AssertionError(f"No asset reference matched {pattern!r}")


def assert_no_asset_reference(response, snippet):
    """Assert that parsed asset references do not contain a snippet."""
    assert all(snippet not in value for value in asset_reference_values(response))


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


def test_taxonomy_category_create_and_delete_routes_persist_changes(client, core_conn):
    """Verify that category create and delete routes update the database."""
    token = set_csrf_token(client)

    create_response = client.post(
        "/taxonomy/categories/create",
        data={
            CSRF_FIELD_NAME: token,
            "name": "Subscriptions",
            "description": "Recurring paid services",
            "instruction": "Use for streaming and software subscriptions.",
        },
        follow_redirects=True,
    )

    category = core_conn.execute(text("""
        SELECT id, description, instruction
        FROM categories
        WHERE name = 'Subscriptions'
        """)).fetchone()
    assert create_response.status_code == 200
    assert category is not None
    assert category._mapping["description"] == "Recurring paid services"

    delete_response = client.post(
        "/taxonomy/categories/delete",
        data={
            CSRF_FIELD_NAME: token,
            "category_id": category._mapping["id"],
        },
        follow_redirects=True,
    )

    remaining = core_conn.execute(text("""
        SELECT COUNT(*) AS count
        FROM categories
        WHERE name = 'Subscriptions'
        """)).fetchone()._mapping["count"]
    assert delete_response.status_code == 200
    assert remaining == 0


def test_taxonomy_category_delete_route_refuses_in_use_category(client, core_conn):
    """Verify that the category delete route keeps categories used by transactions."""
    category_id = core_conn.execute(text("""
        INSERT INTO categories (name)
        VALUES ('Transit')
        """)).lastrowid
    core_conn.execute(text("""
        INSERT INTO transactions (
            tx_date,
            description,
            amount,
            category_id,
            fingerprint
        )
        VALUES ('2026-01-02', 'METRO PASS', 91.25, :p0, 'route-delete-guard')
        """), {"p0": category_id})
    core_conn.commit()

    response = client.post(
        "/taxonomy/categories/delete",
        data={
            CSRF_FIELD_NAME: set_csrf_token(client),
            "category_id": category_id,
        },
        follow_redirects=True,
    )

    category_count = core_conn.execute(text("""
        SELECT COUNT(*) AS count
        FROM categories
        WHERE id = :p0
        """), {"p0": category_id}).fetchone()._mapping["count"]
    assert response.status_code == 200
    assert_visible_text(response, "Only unused categories can be deleted.")
    assert_not_visible_text(response, "Category Transit cannot be deleted because it is in use")
    assert_not_markup(response, "bi-lock")
    assert category_count == 1


def test_upload_route_rejects_missing_file_without_statement_insert(client, core_conn):
    """Verify that upload validation exits before creating a statement row."""
    statement_type_id = core_conn.execute(text("""
        SELECT id
        FROM statement_types
        WHERE active = 1
        ORDER BY id
        LIMIT 1
        """)).fetchone()._mapping["id"]

    response = client.post(
        "/upload",
        data={
            CSRF_FIELD_NAME: set_csrf_token(client),
            "account_name": "Personal",
            "statement_type_id": str(statement_type_id),
        },
        follow_redirects=True,
    )

    statement_count = core_conn.execute(text("SELECT COUNT(*) AS count FROM statements")).fetchone()._mapping["count"]
    assert response.status_code == 200
    assert_visible_text(response, "Please choose a statement file.")
    assert statement_count == 0


def test_upload_route_renders_statement_detail_modal(client, core_conn):
    """Verify uploaded statement rows open processed details by double-click target."""
    paid_from_account_id = core_conn.execute(text("""
        INSERT INTO accounts (name, account_type)
        VALUES ('Main checking', 'checking')
        """)).lastrowid
    account_id = core_conn.execute(text("""
        INSERT INTO accounts (name, account_type, paid_from_account_id)
        VALUES ('RBC Visa', 'credit_card', :p0)
        """), {"p0": paid_from_account_id}).lastrowid
    statement_type_id = core_conn.execute(text("""
        SELECT id
        FROM statement_types
        WHERE parser_type = 'credit_card'
        LIMIT 1
        """)).fetchone()._mapping["id"]
    statement_id = core_conn.execute(text("""
        INSERT INTO statements (
            account_id,
            statement_type_id,
            filename,
            checksum,
            extension,
            raw_text,
            import_status,
            import_started_at,
            import_finished_at,
            imported_count,
            skipped_count,
            ignored_count,
            llm_candidate_count,
            uploaded_at
        )
        VALUES (
            :p0, :p1, 'visa.csv', 'statement-detail-route',
            'csv', 'Date,Description,Amount\n2026-01-02,Corner store,12.34',
            'completed', '2026-05-11T10:00:00Z', '2026-05-11T10:00:02Z',
            2, 1, 3, 4, '2026-05-11T09:59:59Z'
        )
        """), {"p0": account_id, "p1": statement_type_id}).lastrowid
    core_conn.execute(text("""
        INSERT INTO transactions (
            statement_id,
            account_id,
            tx_date,
            description,
            amount,
            category,
            fingerprint
        )
        VALUES (:p0, :p1, :p2, :p3, :p4, 'Food', :p5)
        """), [{"p0": statement_id, "p1": account_id, "p2": "2026-01-02", "p3": "Corner store", "p4": 12.34, "p5": "statement-detail-1"}, {"p0": statement_id, "p1": account_id, "p2": "2026-01-03", "p3": "Cafe", "p4": 4.56, "p5": "statement-detail-2"}])
    core_conn.commit()

    response = client.get("/upload")

    assert response.status_code == 200
    assert_markup(
        response,
        f'data-row-edit-target="#statement-details-{statement_id}"',
        f'id="statement-details-{statement_id}"',
        "data-row-action",
    )
    assert_visible_text(
        response,
        "Statement details",
        "Processing summary",
        "Current statement transactions",
        "Main checking",
        "Date,Description,Amount",
    )


def test_upload_route_renders_interac_import_guidance(client, core_conn):
    """Verify Interac uploads explain ordering and skipped or ignored rows."""
    account_id = core_conn.execute(text("""
        INSERT INTO accounts (name, account_type)
        VALUES ('TD Interac Sent', 'checking')
        """)).lastrowid
    statement_type_id = core_conn.execute(text("""
        SELECT id
        FROM statement_types
        WHERE parser_type = 'interac_etransfer'
        LIMIT 1
        """)).fetchone()._mapping["id"]
    core_conn.execute(text("""
        INSERT INTO statements (
            account_id,
            statement_type_id,
            filename,
            checksum,
            extension,
            raw_text,
            import_status,
            imported_count,
            skipped_count,
            ignored_count,
            uploaded_at
        )
        VALUES (
            :p0, :p1, 'interac-sent.csv', 'interac-guidance-route',
            'csv', 'Date Sent,Recipient,Amount,Method,Status',
            'completed', 29, 1, 76, '2026-05-14T17:41:24Z'
        )
        """), {"p0": account_id, "p1": statement_type_id})
    core_conn.commit()

    response = client.get("/upload")

    assert response.status_code == 200
    assert_visible_text(
        response,
        "Import matching checking statements first",
        "Interac history only enriches existing checking rows.",
        "skipped rows are ambiguous matches",
        "no matching checking transaction yet",
    )


def test_transactions_route_renders_category_source_badges_and_filter(client, core_conn):
    """Verify transaction source provenance is visible on the transaction page."""
    rule_id = core_conn.execute(text("""
        INSERT INTO category_rules (keyword, category, source)
        VALUES ('RULE CATEGORIZED STORE', 'Food', 'manual')
        """)).lastrowid
    core_conn.execute(text("""
        INSERT INTO transactions (
            tx_date,
            description,
            amount,
            category,
            category_source,
            category_confidence,
            fingerprint
        )
        VALUES ('2026-01-02', 'AI categorized store', 12.34, 'Food', 'ai', 0.91, 'route-ai-source')
        """))
    core_conn.execute(text("""
        INSERT INTO transactions (
            tx_date,
            description,
            amount,
            category,
            category_source,
            category_confidence,
            category_rule_id,
            fingerprint
        )
        VALUES (
            '2026-01-03',
            'Rule categorized store',
            23.45,
            'Food',
            'rule',
            0.96,
            :p0,
            'route-rule-source-link'
        )
        """), {"p0": rule_id})
    core_conn.commit()

    response = client.get("/transactions?period=all")
    body = response_html(response)
    compact_body = " ".join(body.split())
    expected_rule_url = f'/rules/audit/rule/{rule_id}'

    assert response.status_code == 200
    assert_visible_text(response, "Categorization method", "All methods", "Pending approval")
    assert_not_visible_text(response, "Ready to approve", "Unverified")
    assert "&middot; AI 91%" in compact_body
    assert "<th>Kind</th>" not in body
    assert "<span>Verify</span>" not in body
    assert '<th class="text-end">Actions</th>' in body
    assert 'data-transaction-batch-bar' in body
    assert 'data-transaction-select-all' in body
    assert 'data-transaction-row-checkbox' in body
    assert 'data-all-transaction-ids="[' in body
    assert "Approve selected" in body
    assert "Ignore selected" in body
    assert "Recategorize selected" in body
    assert "data-busy-overlay-root" in body
    assert "js/busy-overlay.js" in body
    assert 'data-busy-message="Recategorizing selected transactions..."' in body
    assert 'data-busy-message="Suggesting category..."' in body
    assert 'class="transaction-date text-nowrap"' in body
    assert "transaction-action-menu" in body
    assert "Edit category" in body
    assert "Ignore transaction" in body
    assert "View evidence" not in body
    assert f'href="{expected_rule_url}"' in body
    assert 'target="_blank"' in body
    assert 'rel="noopener noreferrer"' in body
    assert f'href="{expected_rule_url}"' in body.split('Category source', 1)[1]
    assert_markup(
        response,
        "data-category-description-select",
        'value="transaction_only" data-rule-save-mode checked',
        "data-rule-save-only",
        "modal-dialog-fit-content",
        'value="12.34"',
        "data-rule-exact-amount",
    )
    assert_has_element(
        response,
        "option",
        attrs={
            "data-category-description": (
                "Food and drink, including groceries, restaurants, cafes, bakeries, "
                "takeout, delivery, and prepared meals."
            )
        },
    )
    assert_has_element(
        response,
        "label",
        attrs={
            "title": (
                "Marks transactions that may be useful for tax preparation, accounting, "
                "or year-end review."
            )
        },
    )
    assert body.index("This transaction only") < body.index("Save rule")


def test_transactions_route_escapes_imported_merchant_keys(client, core_conn):
    """Verify merchant keys in transaction modals cannot render imported markup."""
    core_conn.execute(text("""
        INSERT INTO transactions (
            tx_date,
            description,
            amount,
            category,
            needs_review,
            fingerprint
        )
        VALUES (
            '2026-01-02',
            '<img src=x onerror=alert(1)>',
            12.34,
            'UNKNOWN',
            1,
            'route-escaped-merchant'
        )
        """))
    core_conn.commit()

    response = client.get("/transactions?period=all")
    body = response_html(response)

    assert response.status_code == 200
    assert "&lt;IMG SRC=X ONERROR=ALERT 1 &gt;" in body
    assert "<IMG SRC=X ONERROR=ALERT 1>" not in body
    assert "<IMG SRC=X ONERROR=ALERT 1 >" not in body
    assert "<img src=x onerror=alert(1)>" not in body


def test_dashboard_route_does_not_render_assignment_tooltips(client, core_conn):
    """Verify category assignment tooltips stay out of dashboard filters."""
    core_conn.execute(text("""
        INSERT INTO transactions (tx_date, description, amount, category, fingerprint)
        VALUES ('2026-01-02', 'Dashboard Store', 12.34, 'Food', 'route-dashboard-tooltip')
        """))
    core_conn.commit()

    response = client.get("/dashboard?period=all")
    tag_response = client.get("/dashboard?period=all&breakdown=tag")
    untagged_response = client.get("/dashboard?period=all&breakdown=tag&show_untagged=1")

    assert response.status_code == 200
    assert tag_response.status_code == 200
    assert untagged_response.status_code == 200
    assert_not_markup(response, "data-category-description-select")
    assert_has_element(response, "div", attrs={"role": "group", "aria-label": "Breakdown"})
    assert_has_element(response, None, attrs={"data-select-all-label": "Select all categories"})
    assert_has_element(response, None, attrs={"data-select-all-label": "Select all tags"})
    assert_visible_text(
        response,
        "Spending by category",
        "Category detail",
        "Show income",
    )
    assert_visible_text(
        tag_response,
        "Spending by tag",
        "Tag detail",
        "Tagged spending can count the same transaction more than once.",
        "Show untagged",
    )
    assert_not_visible_text(response, "Choose filters")
    assert_markup(response, 'name="merchant_search"', 'placeholder="Search merchant"', "data-ajax-refresh-link")
    assert_not_markup(response, "data-dashboard-custom-categories", "data-dashboard-custom-tags")
    assert_markup(tag_response, '"categoryLabels": []')
    assert_visible_text(untagged_response, "Hide untagged")
    assert_markup(untagged_response, '"categoryLabels": ["Untagged"]')


def test_category_filters_offer_analysis_category_preset(client, core_conn):
    """Verify category filters can bulk-select categories used for analysis."""
    core_conn.execute(text("""
        INSERT INTO categories (name, builtin_key, description, instruction)
        VALUES ('System adjustment', 'system_adjustment', '', '')
        """))
    core_conn.execute(text("""
        INSERT INTO transactions (tx_date, description, amount, category, category_source, fingerprint)
        VALUES ('2026-01-02', 'Analysis category store', 12.34, 'Food', 'rule', 'route-analysis-category-filter')
        """))
    core_conn.commit()

    expected_counts = {
        "/dashboard?period=all": 1,
        "/comparison": 2,
        "/calendar": 1,
        "/recurring": 1,
        "/rules": 1,
        "/transactions": 1,
    }

    for path, expected_count in expected_counts.items():
        response = client.get(path)
        body = response_html(response)

        assert response.status_code == 200
        assert body.count('data-select-preset-label="Select analysis categories"') == expected_count
        preset_values = re.findall(r"data-select-preset-exclude-values='([^']+)'", body)
        assert len(preset_values) == expected_count
        assert all("System adjustment" in value for value in preset_values)
        assert "Transfers" in body
        assert "UNKNOWN" in body


def test_comparison_route_renders_complete_unknown_warning(client, core_conn, monkeypatch):
    """Verify comparison warning placeholders render with category and share values."""
    monkeypatch.setattr(comparison_service, "date", FixedDate)
    core_conn.execute(text("""
        INSERT INTO transactions (tx_date, description, amount, category, category_source, fingerprint)
        VALUES (:p0, :p1, :p2, :p3, 'rule', :p4)
        """), [{"p0": "2026-04-02", "p1": "Unknown Prior", "p2": 40.00, "p3": "UNKNOWN", "p4": "route-comparison-unknown-prior"}, {"p0": "2026-04-03", "p1": "Prior Grocery", "p2": 60.00, "p3": "Food", "p4": "route-comparison-food-prior"}, {"p0": "2026-05-02", "p1": "Unknown Current", "p2": 70.00, "p3": "UNKNOWN", "p4": "route-comparison-unknown-current"}, {"p0": "2026-05-03", "p1": "Current Grocery", "p2": 30.00, "p3": "Food", "p4": "route-comparison-food-current"}])
    core_conn.commit()

    response = client.get("/comparison?years=2026&period_comparison=month_previous")
    visible_body = visible_html(response)

    assert response.status_code == 200
    assert "UNKNOWN accounts for 55.0%" in visible_body
    assert "UNKNOWN accounts for 70.0%" in visible_body
    assert "because accounts for %" not in visible_body


def test_financial_reporting_pages_render_english_and_french_copy(client, core_conn, monkeypatch):
    """Verify reporting pages localize visible labels and explanatory text."""
    monkeypatch.setattr(comparison_service, "date", FixedDate)
    core_conn.execute(text("""
        INSERT INTO transactions (
            tx_date,
            description,
            amount,
            category,
            category_source,
            fingerprint
        )
        VALUES (:p0, :p1, :p2, :p3, 'rule', :p4)
        """), [{"p0": "2025-05-02", "p1": "Prior grocery", "p2": 80.00, "p3": "Food", "p4": "route-fr-prior"}, {"p0": "2026-01-02", "p1": "Utility bill", "p2": 50.00, "p3": "Utilities", "p4": "route-fr-recurring-1"}, {"p0": "2026-02-02", "p1": "Utility bill", "p2": 50.00, "p3": "Utilities", "p4": "route-fr-recurring-2"}, {"p0": "2026-03-02", "p1": "Utility bill", "p2": 50.00, "p3": "Utilities", "p4": "route-fr-recurring-3"}, {"p0": "2026-04-02", "p1": "Utility bill", "p2": 55.00, "p3": "Utilities", "p4": "route-fr-recurring-4"}, {"p0": "2026-04-04", "p1": "Prior unknown", "p2": 400.00, "p3": "UNKNOWN", "p4": "route-fr-unknown-prior"}, {"p0": "2026-05-02", "p1": "Current grocery", "p2": 120.00, "p3": "Food", "p4": "route-fr-current"}, {"p0": "2026-05-03", "p1": "Payroll", "p2": -800.00, "p3": "Income", "p4": "route-fr-income"}, {"p0": "2026-05-04", "p1": "Current unknown", "p2": 900.00, "p3": "UNKNOWN", "p4": "route-fr-unknown-current"}])
    core_conn.commit()

    english_home_response = client.get("/")
    english_dashboard_response = client.get("/dashboard?period=ytd")
    english_comparison_response = client.get("/comparison")
    english_calendar_response = client.get("/calendar")
    english_recurring_response = client.get("/recurring")

    assert_visible_text(english_home_response, "Needs attention")
    assert_visible_text(english_dashboard_response, "Dashboard", "Merchant analytics")
    assert_visible_text(
        english_comparison_response,
        "Year comparison",
        "Monthly spending by year",
        "Period comparison",
        "Category comparison may be unreliable",
        "Category insights may be incomplete",
    )
    assert_visible_text(english_calendar_response, "Calendar", "Posted outflows")
    assert_visible_text(english_recurring_response, "Recurring activity", "Frequency")
    assert_not_visible_text(english_dashboard_response, "Tableau de bord", "Analyse des marchands")

    core_conn.execute(text("""
        UPDATE user_settings
        SET value = 'fr'
        WHERE key = 'ui_language'
          AND user_id = (SELECT id FROM users WHERE username = 'owner')
        """))
    core_conn.commit()

    home_response = client.get("/")
    dashboard_response = client.get("/dashboard?period=ytd")
    comparison_response = client.get("/comparison")
    calendar_response = client.get("/calendar")
    recurring_response = client.get("/recurring")

    assert home_response.status_code == 200
    assert dashboard_response.status_code == 200
    assert comparison_response.status_code == 200
    assert calendar_response.status_code == 200
    assert recurring_response.status_code == 200

    assert_visible_text(home_response, "Ce qui demande une attention", "À traiter")
    assert_not_visible_text(home_response, "Centre de commande financier", "Financial command center", "Needs attention")

    assert_visible_text(
        dashboard_response,
        "Tableau de bord",
        "Vue actuelle : Depuis le début de l'année.",
        "Dépenses par catégorie",
        "Analyse des marchands",
    )
    assert_has_element(
        dashboard_response,
        None,
        attrs={"data-select-preset-label": "Sélectionner les catégories d’analyse"},
    )
    assert_not_visible_text(dashboard_response, "year to date", "Merchant analytics")

    assert_visible_text(
        comparison_response,
        "Comparaison annuelle",
        "Dépenses mensuelles par année",
        "Comparaison de périodes",
        "La comparaison par catégorie peut être peu fiable",
        "Les constats par catégorie peuvent être incomplets",
    )
    assert_not_visible_text(
        comparison_response,
        "Category comparison may be unreliable",
        "Category insights may be incomplete",
        "Period comparison",
    )

    assert_visible_text(calendar_response, "Calendrier", "Sorties comptabilisées", "Récurrences prévues")
    assert_not_visible_text(calendar_response, "Posted outflows")

    assert_visible_text(recurring_response, "Récurrences", "Activité récurrente", "Fréquence")
    assert_not_visible_text(recurring_response, "Recurring activity")


def test_review_route_renders_category_source_for_review_rows(client, core_conn):
    """Verify review details expose source provenance for rows needing review."""
    core_conn.execute(text("""
        INSERT INTO transactions (
            tx_date,
            description,
            amount,
            category,
            category_source,
            category_confidence,
            needs_review,
            fingerprint
        )
        VALUES ('2026-01-02', 'Low confidence AI store', 12.34, 'Food', 'ai', 0.72, 1, 'route-review-ai-source')
        """))
    core_conn.commit()

    response = client.get("/review")

    assert response.status_code == 200
    assert_visible_text(response, "Category source", "AI", "72%")


def test_comparison_route_renders_visual_key_insights(client, core_conn, monkeypatch):
    """Verify comparison insights render as visual cards when period data exists."""
    monkeypatch.setattr(comparison_service, "date", FixedDate)
    core_conn.execute(text("""
        INSERT INTO transactions (tx_date, description, amount, category, category_source, fingerprint)
        VALUES (:p0, :p1, :p2, 'Food', 'rule', :p3)
        """), [{"p0": "2026-04-02", "p1": "Prior Grocery", "p2": 100.00, "p3": "comparison-route-prior"}, {"p0": "2026-05-02", "p1": "Current Grocery", "p2": 240.00, "p3": "comparison-route-current"}])
    core_conn.commit()

    response = client.get("/comparison")

    assert response.status_code == 200
    assert_markup(
        response,
        "comparisonInsightCarousel",
        "insight-grid",
        "insight-card-danger",
        "insight-summary text-danger",
        "insight-current-value text-danger",
        "insight-bar-fill",
    )
    assert_has_element(response, "button", attrs={"aria-label": "Previous insight group"})
    assert_has_element(response, "button", attrs={"aria-label": "Next insight group"})


def test_comparison_route_renders_year_chart_type_toggle(client, core_conn):
    """Verify the year comparison chart exposes line and bar display modes."""
    core_conn.execute(text("""
        INSERT INTO transactions (tx_date, description, amount, category, category_source, fingerprint)
        VALUES (:p0, :p1, :p2, 'Food', 'rule', :p3)
        """), [{"p0": "2025-01-02", "p1": "Prior Grocery", "p2": 100.00, "p3": "comparison-toggle-prior"}, {"p0": "2026-01-02", "p1": "Current Grocery", "p2": 120.00, "p3": "comparison-toggle-current"}])
    core_conn.commit()

    response = client.get("/comparison")

    assert response.status_code == 200
    assert_markup(response, "comparison_chart_line", "comparison_chart_bar")
    assert_has_element(response, "div", attrs={"role": "group", "aria-label": "Monthly spending chart type"})
    assert_no_asset_reference(response, "cdn.jsdelivr.net")
    assert_no_asset_reference(response, "js/comparison-charts.js?v=40")
    assert_asset_reference(response, r"/static/js/comparison\.js\?v=[0-9a-f]{12}")
    assert_asset_reference(response, r"/static/js/chart-utils\.js\?v=[0-9a-f]{12}")
    assert_asset_reference(response, r"/static/js/comparison-charts\.js\?v=[0-9a-f]{12}")
    assert_asset_reference(response, r"/static/vendor/echarts/5\.6\.0/echarts\.min\.js\?v=[0-9a-f]{12}")
    assert asset_reference_index(response, r"/static/js/chart-utils\.js") < asset_reference_index(
        response,
        r"/static/js/comparison-charts\.js",
    )


def test_upload_route_rejects_duplicate_statement_checksum(client, core_conn, monkeypatch):
    """Verify that duplicate uploads are rejected before queueing background work."""
    statement_type_id = core_conn.execute(text("""
        SELECT id
        FROM statement_types
        WHERE active = 1
        ORDER BY id
        LIMIT 1
        """)).fetchone()._mapping["id"]
    core_conn.execute(text("""
        INSERT INTO statements (
            statement_type_id,
            filename,
            checksum,
            raw_text,
            uploaded_at
        )
        VALUES (:p0, 'already.csv', :p1, 'Date,Description,Amount', '2026-05-11T12:00:00Z')
        """), {"p0": statement_type_id, "p1": "known-checksum"})
    core_conn.commit()
    monkeypatch.setattr("finance_app.modules.upload.controller.file_checksum", lambda uploaded_file: "known-checksum")

    response = client.post(
        "/upload",
        data={
            CSRF_FIELD_NAME: set_csrf_token(client),
            "account_name": "Personal",
            "statement_type_id": str(statement_type_id),
            "statement": (io.BytesIO(b"Date,Description,Amount\n"), "duplicate.csv"),
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )

    statement_count = core_conn.execute(text("SELECT COUNT(*) AS count FROM statements")).fetchone()._mapping["count"]
    assert response.status_code == 200
    assert_visible_text(response, "This statement was already uploaded as already.csv on 2026-05-11T12:00:00Z")
    assert statement_count == 1
