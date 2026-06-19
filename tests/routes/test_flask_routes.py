"""Route-level tests for the Flask application."""

import re
from datetime import date as real_date

from sqlalchemy import text
from tests.support.html import (
    assert_asset_reference,
    assert_has_element,
    assert_markup,
    assert_no_asset_reference,
    assert_no_element,
    assert_not_markup,
    assert_not_visible_text,
    assert_visible_text,
    asset_reference_index,
    response_html,
    visible_html,
)

from finance_app.modules.comparison import service as comparison_service
from finance_app.modules.home import service as home_service


class FixedDate(real_date):
    """Fixed replacement for date.today in comparison route tests."""

    @classmethod
    def today(cls):
        """Return a deterministic current date."""
        return cls(2026, 5, 11)


def test_navigation_pages_render_distinct_browser_titles(client):
    """Verify browser history labels include the active navigation destination."""
    expected_titles = {
        "/": "FinScope - Home",
        "/account": "FinScope - Account",
        "/dashboard": "FinScope - Dashboard",
        "/reports": "FinScope - Reports",
        "/comparison": "FinScope - Comparison",
        "/calendar": "FinScope - Calendar",
        "/recurring": "FinScope - Recurring",
        "/upload": "FinScope - Statements",
        "/transactions": "FinScope - Transactions",
        "/review": "FinScope - Review",
        "/rules": "FinScope - Rules",
        "/taxonomy": "FinScope - Categories and tags",
        "/jobs": "FinScope - Processing",
        "/settings": "FinScope - Settings",
        "/admin/users": "FinScope - Users",
    }

    for path, expected_title in expected_titles.items():
        response = client.get(path)

        assert response.status_code == 200
        assert_has_element(response, "title", text=expected_title)


def test_transactions_route_renders_category_source_badges_and_filter(client, core_conn):
    """Verify transaction source provenance is visible on the transaction page."""
    account_id = core_conn.execute(text("""
        INSERT INTO accounts (name)
        VALUES ('Route Visa')
        """)).lastrowid
    rule_id = core_conn.execute(text("""
        INSERT INTO category_rules (keyword, category, source)
        VALUES ('RULE CATEGORIZED STORE', 'Food', 'manual')
        """)).lastrowid
    core_conn.execute(
        text("""
        INSERT INTO transactions (
            tx_date,
            description,
            amount,
            account_id,
            category,
            category_source,
            category_confidence,
            fingerprint
        )
        VALUES ('2026-01-02', 'AI categorized store', 12.34, :p0, 'Food', 'ai', 0.91, 'route-ai-source')
        """),
        {"p0": account_id},
    )
    core_conn.execute(
        text("""
        INSERT INTO transactions (
            tx_date,
            description,
            amount,
            account_id,
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
            :p1,
            'Food',
            'rule',
            0.96,
            :p0,
            'route-rule-source-link'
        )
        """),
        {"p0": rule_id, "p1": account_id},
    )
    core_conn.commit()

    response = client.get(f"/transactions?period=all&account_id={account_id}")
    body = response_html(response)
    expected_rule_url = f"/rules/audit/rule/{rule_id}"
    modal_summary = body.split('id="categorize-transaction-', 1)[1].split("</dl>", 1)[0]

    assert response.status_code == 200
    assert_visible_text(response, "How categorized", "All methods", "Pending approval", "Route Visa")
    assert_has_element(response, "select", attrs={"id": "transaction-account", "name": "account_id"})
    assert_has_element(response, "option", attrs={"value": str(account_id), "selected": True}, text="Route Visa")
    assert_not_visible_text(response, "Ready to approve", "Unverified")
    assert_has_element(response, "span", attrs={"data-export-label": "Method"}, text="AI")
    assert_has_element(
        response,
        "span",
        attrs={"data-export-label": "Score", "data-export-value": "0.91"},
        text="91%",
    )
    assert "<th>Kind</th>" not in body
    assert "<span>Verify</span>" not in body
    assert '<th class="text-end">Actions</th>' in body
    assert "data-transaction-batch-bar" in body
    assert "data-transaction-select-all" in body
    assert "data-transaction-row-checkbox" in body
    assert 'data-all-transaction-ids="[' in body
    assert "Approve selected" in body
    assert "Ignore selected" in body
    assert "Recategorize selected" in body
    assert "data-busy-overlay-root" in body
    assert "js/busy-overlay.js" in body
    assert 'data-busy-message="Recategorizing selected transactions..."' in body
    assert 'data-busy-message="Suggesting category..."' in body
    assert modal_summary.index("<dt>Kind</dt>") < modal_summary.index("<dt>Account</dt>")
    assert modal_summary.index("<dt>Account</dt>") < modal_summary.index("<dt>Status</dt>")
    assert "Route Visa" in modal_summary
    assert "Pending approval" in modal_summary
    assert 'class="transaction-date text-nowrap"' in body
    assert "transaction-action-menu" in body
    assert "Edit category" in body
    assert "Ignore transaction" in body
    assert "View evidence" not in body
    assert f'href="{expected_rule_url}"' in body
    assert 'target="_blank"' in body
    assert 'rel="noopener noreferrer"' in body
    assert f'href="{expected_rule_url}"' in body.split("Categorized by", 1)[1]
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
            "title": ("Marks transactions that may be useful for tax preparation, accounting, " "or year-end review.")
        },
    )
    assert body.index("Apply once") < body.index("Remember for future matches")


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
    tag_response = client.get("/dashboard?period=all&tags=Tax")
    untagged_response = client.get("/dashboard?period=all&quick_view=all")

    assert response.status_code == 200
    assert tag_response.status_code == 200
    assert untagged_response.status_code == 200
    assert_not_markup(response, "data-category-description-select")
    assert_has_element(response, None, attrs={"data-select-all-label": "Select all categories"})
    assert_has_element(response, None, attrs={"data-select-all-label": "Select all tags"})
    assert_visible_text(
        response,
        "Reports",
        "Overview",
        "Income and credits",
        "Category or tag",
        "Open reports",
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
    assert_not_markup(tag_response, "dashboard-chart-data", '"categoryLabels"')
    assert_not_visible_text(untagged_response, "Hide untagged")


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
        assert body.count('data-select-preset-summary-label="Analysis categories"') == expected_count
        preset_values = re.findall(r"data-select-preset-exclude-values='([^']+)'", body)
        assert len(preset_values) == expected_count
        assert all("System adjustment" in value for value in preset_values)
        assert "Transfers" in body
        assert "UNKNOWN" in body


def test_calendar_route_renders_bookmarkable_merchant_filter(client):
    """Verify calendar exposes merchant autocomplete and preserves query filters."""
    response = client.get("/calendar?month=2026-05&account_id=12&merchant_id=34&merchant_query=NETFLIX")
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert_markup(
        response,
        "data-calendar-dynamic",
        "data-calendar-ajax-form",
        "data-calendar-ajax-link",
        "data-flatpickr-submit-on-change",
        "js/merchant-autocomplete.js",
        "data-merchant-autocomplete",
        'name="merchant_id"',
        'value="34"',
        'name="merchant_query"',
        'value="NETFLIX"',
        "merchant_id=34",
        "merchant_query=NETFLIX",
    )
    assert body.index('id="calendar-filters"') < body.index("data-calendar-ajax-form")
    assert body.index("data-calendar-ajax-form") < body.index("Daily summaries.")
    assert_visible_text(response, "Merchant: NETFLIX", "No posted transactions match this account and merchant.")


def test_comparison_route_renders_complete_unknown_warning(client, core_conn, monkeypatch):
    """Verify comparison warning placeholders render with category and share values."""
    monkeypatch.setattr(comparison_service, "date", FixedDate)
    core_conn.execute(
        text("""
        INSERT INTO transactions (tx_date, description, amount, category, category_source, fingerprint)
        VALUES (:p0, :p1, :p2, :p3, 'rule', :p4)
        """),
        [
            {
                "p0": "2026-04-02",
                "p1": "Unknown Prior",
                "p2": 40.00,
                "p3": "UNKNOWN",
                "p4": "route-comparison-unknown-prior",
            },
            {"p0": "2026-04-03", "p1": "Prior Grocery", "p2": 60.00, "p3": "Food", "p4": "route-comparison-food-prior"},
            {
                "p0": "2026-05-02",
                "p1": "Unknown Current",
                "p2": 70.00,
                "p3": "UNKNOWN",
                "p4": "route-comparison-unknown-current",
            },
            {
                "p0": "2026-05-03",
                "p1": "Current Grocery",
                "p2": 30.00,
                "p3": "Food",
                "p4": "route-comparison-food-current",
            },
        ],
    )
    core_conn.commit()

    response = client.get("/comparison?years=2026&period_comparison=month_previous")
    visible_body = visible_html(response)

    assert response.status_code == 200
    assert "UNKNOWN accounts for 55.0%" in visible_body
    assert "UNKNOWN accounts for 70.0%" in visible_body
    assert "because accounts for %" not in visible_body


def test_home_route_renders_quick_insight_cards(client, core_conn, monkeypatch):
    """Verify Home renders ranked quick insights as linked compact cards."""
    monkeypatch.setattr(home_service, "date", FixedDate)
    monkeypatch.setattr(comparison_service, "date", FixedDate)
    rows = [
        ("2026-04-02", "Alpha Store", 500.00, "Food", "home-route-alpha-prior"),
        ("2026-04-03", "Charlie Store", 400.00, "Food", "home-route-charlie-prior"),
        ("2026-04-04", "Delta Store", 300.00, "Food", "home-route-delta-prior"),
        ("2026-04-05", "Echo Store", 200.00, "Food", "home-route-echo-prior"),
        ("2026-04-06", "Bravo Store", 100.00, "Food", "home-route-bravo-prior"),
        ("2026-04-07", "Foxtrot Utilities", 700.00, "Utilities", "home-route-foxtrot-prior"),
        ("2026-05-02", "Bravo Store", 600.00, "Food", "home-route-bravo-current"),
        ("2026-05-03", "Alpha Store", 500.00, "Food", "home-route-alpha-current"),
        ("2026-05-04", "Charlie Store", 400.00, "Food", "home-route-charlie-current"),
        ("2026-05-05", "Delta Store", 300.00, "Food", "home-route-delta-current"),
        ("2026-05-06", "Echo Store", 200.00, "Food", "home-route-echo-current"),
        ("2026-05-07", "Foxtrot Utilities", 50.00, "Utilities", "home-route-foxtrot-current"),
    ]
    core_conn.execute(
        text("""
        INSERT INTO transactions (tx_date, description, amount, category, category_source, fingerprint)
        VALUES (:p0, :p1, :p2, :p3, 'rule', :p4)
        """),
        [dict(zip(("p0", "p1", "p2", "p3", "p4"), row)) for row in rows],
    )
    core_conn.commit()

    response = client.get("/")

    assert response.status_code == 200
    assert_visible_text(response, "Quick insights", "Merchant moved up", "5 places", "BRAVO STORE")
    assert_has_element(response, "section", attrs={"class": "home-insight-panel"})
    assert_has_element(response, "a", attrs={"class": "home-insight-item"}, text="Merchant moved up")
    assert_has_element(response, "i", attrs={"class": "bi-arrow-up-right-circle"})
    assert_has_element(
        response,
        "a",
        attrs={
            "href": (
                "/transactions?period=custom&ignored=active&date_from=2026-05-01"
                "&date_to=2026-05-11&amount_type=spending&merchant_key=BRAVO+STORE"
            )
        },
        text="BRAVO STORE",
    )


def test_home_quick_insights_escape_user_data(client, core_conn, monkeypatch):
    """Verify Home quick insights escape category names from user data."""
    monkeypatch.setattr(home_service, "date", FixedDate)
    monkeypatch.setattr(comparison_service, "date", FixedDate)
    category = 'Food <img src="x" onerror="alert(1)">'
    rows = [
        ("2026-04-02", "Prior escaped store", 40.00, category, "home-escape-prior"),
        ("2026-05-02", "Current escaped store", 220.00, category, "home-escape-current"),
    ]
    core_conn.execute(
        text("""
        INSERT INTO transactions (tx_date, description, amount, category, category_source, fingerprint)
        VALUES (:p0, :p1, :p2, :p3, 'rule', :p4)
        """),
        [dict(zip(("p0", "p1", "p2", "p3", "p4"), row)) for row in rows],
    )
    core_conn.commit()

    response = client.get("/")

    assert response.status_code == 200
    assert_visible_text(response, "Quick insights", category)
    assert_has_element(response, "a", attrs={"class": "home-insight-item"}, text=category)
    assert_no_element(response, "img", attrs={"src": "x"})


def test_financial_reporting_pages_render_english_and_french_copy(client, core_conn, monkeypatch):
    """Verify reporting pages localize visible labels and explanatory text."""
    monkeypatch.setattr(home_service, "date", FixedDate)
    monkeypatch.setattr(comparison_service, "date", FixedDate)
    core_conn.execute(
        text("""
        INSERT INTO transactions (
            tx_date,
            description,
            amount,
            category,
            category_source,
            fingerprint
        )
        VALUES (:p0, :p1, :p2, :p3, 'rule', :p4)
        """),
        [
            {"p0": "2025-05-02", "p1": "Prior grocery", "p2": 80.00, "p3": "Food", "p4": "route-fr-prior"},
            {"p0": "2026-01-02", "p1": "Utility bill", "p2": 50.00, "p3": "Utilities", "p4": "route-fr-recurring-1"},
            {"p0": "2026-02-02", "p1": "Utility bill", "p2": 50.00, "p3": "Utilities", "p4": "route-fr-recurring-2"},
            {"p0": "2026-03-02", "p1": "Utility bill", "p2": 50.00, "p3": "Utilities", "p4": "route-fr-recurring-3"},
            {"p0": "2026-04-02", "p1": "Utility bill", "p2": 55.00, "p3": "Utilities", "p4": "route-fr-recurring-4"},
            {"p0": "2026-04-04", "p1": "Prior unknown", "p2": 400.00, "p3": "UNKNOWN", "p4": "route-fr-unknown-prior"},
            {"p0": "2026-05-02", "p1": "Current grocery", "p2": 120.00, "p3": "Food", "p4": "route-fr-current"},
            {"p0": "2026-05-03", "p1": "Payroll", "p2": -800.00, "p3": "Income", "p4": "route-fr-income"},
            {
                "p0": "2026-05-04",
                "p1": "Current unknown",
                "p2": 900.00,
                "p3": "UNKNOWN",
                "p4": "route-fr-unknown-current",
            },
        ],
    )
    core_conn.commit()

    english_home_response = client.get("/")
    english_dashboard_response = client.get("/dashboard?period=ytd")
    english_comparison_response = client.get("/comparison")
    english_calendar_response = client.get("/calendar")
    english_recurring_response = client.get("/recurring")

    assert_visible_text(english_home_response, "Needs attention", "Quick insights")
    assert_visible_text(english_dashboard_response, "Dashboard", "Reports", "Open reports")
    assert_visible_text(
        english_comparison_response,
        "Year trends",
        "Monthly spending by year",
        "Period changes",
        "Category comparison may be unreliable",
        "Category insights may be incomplete",
    )
    assert_visible_text(english_calendar_response, "Calendar", "Posted outflows")
    assert_visible_text(english_recurring_response, "Recurring activity", "Frequency")
    assert_not_visible_text(english_dashboard_response, "Tableau de bord", "Ouvrir les rapports")

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

    assert_visible_text(home_response, "Ce qui nécessite votre attention", "À traiter", "Aperçus rapides")
    assert_not_visible_text(
        home_response, "Centre de commande financier", "Financial command center", "Needs attention"
    )

    assert_visible_text(
        dashboard_response,
        "Tableau de bord",
        "Vue actuelle : Depuis le début de l'année.",
        "Rapports",
        "Ouvrir les rapports",
        "Revenus et crédits",
    )
    assert_has_element(
        dashboard_response,
        None,
        attrs={"data-select-preset-label": "Sélectionner les catégories d’analyse"},
    )
    assert_has_element(
        dashboard_response,
        None,
        attrs={"data-select-preset-summary-label": "Catégories d’analyse"},
    )
    assert_not_visible_text(dashboard_response, "year to date", "Open reports")

    assert_visible_text(
        comparison_response,
        "Tendances annuelles",
        "Analyse mensuelle par année : dépenses",
        "Changements de période",
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
    assert_visible_text(response, "Categorized by", "AI", "72%")


def test_comparison_route_renders_visual_key_insights(client, core_conn, monkeypatch):
    """Verify comparison insights render as visual cards when period data exists."""
    monkeypatch.setattr(comparison_service, "date", FixedDate)
    core_conn.execute(
        text("""
        INSERT INTO transactions (tx_date, description, amount, category, category_source, fingerprint)
        VALUES (:p0, :p1, :p2, 'Food', 'rule', :p3)
        """),
        [
            {"p0": "2026-04-02", "p1": "Prior Grocery", "p2": 100.00, "p3": "comparison-route-prior"},
            {"p0": "2026-05-02", "p1": "Current Grocery", "p2": 240.00, "p3": "comparison-route-current"},
        ],
    )
    core_conn.commit()

    response = client.get("/comparison")

    assert response.status_code == 200
    assert_markup(
        response,
        "comparisonInsightCarousel",
        'data-insights-per-slide="3"',
        "insight-grid",
        "insight-card-danger",
        "insight-summary text-danger",
        "insight-current-value text-danger",
        "insight-bar-fill",
    )
    assert_visible_text(response, "Key insights", "Largest category increase", "Food", "+140.00 $")
    assert_not_markup(
        response,
        "insight_type",
        "rank_reason",
        "selection_metrics",
        "robust_anomaly",
        "mix_shift",
        "merchant_behavior",
        "category_increase",
    )
    assert_has_element(
        response,
        "div",
        attrs={"id": "comparisonInsightCarousel", "data-insights-per-slide": "3"},
    )


def test_comparison_route_renders_ranked_anomaly_insights(client, core_conn, monkeypatch):
    """Verify comparison page opts into ranked historical insight candidates."""
    monkeypatch.setattr(comparison_service, "date", FixedDate)
    rows = [
        ("2025-12-02", "Metro Grocery", 48.00, "Food", "route-anomaly-history-1"),
        ("2026-01-02", "Metro Grocery", 50.00, "Food", "route-anomaly-history-2"),
        ("2026-02-02", "Metro Grocery", 52.00, "Food", "route-anomaly-history-3"),
        ("2026-03-02", "Metro Grocery", 49.00, "Food", "route-anomaly-history-4"),
        ("2026-04-02", "Metro Grocery", 51.00, "Food", "route-anomaly-history-5"),
        ("2026-05-02", "Metro Grocery", 220.00, "Food", "route-anomaly-current"),
    ]
    core_conn.execute(
        text("""
        INSERT INTO transactions (tx_date, description, amount, category, category_source, fingerprint)
        VALUES (:p0, :p1, :p2, :p3, 'rule', :p4)
        """),
        [dict(zip(("p0", "p1", "p2", "p3", "p4"), row)) for row in rows],
    )
    core_conn.commit()

    response = client.get("/comparison?period_comparison=month_previous")

    assert response.status_code == 200
    assert_visible_text(
        response,
        "Key insights",
        "Unusually high category spending",
        "Food: higher than usual",
        "+170.00 $",
    )
    assert_not_markup(response, "robust_anomaly", "merchant_behavior", "rank_reason")


def test_comparison_route_renders_year_chart_type_toggle(client, core_conn):
    """Verify the year comparison chart exposes line, bar, and table display modes."""
    core_conn.execute(
        text("""
        INSERT INTO transactions (tx_date, description, amount, category, category_source, fingerprint)
        VALUES (:p0, :p1, :p2, 'Food', 'rule', :p3)
        """),
        [
            {"p0": "2025-01-02", "p1": "Prior Grocery", "p2": 100.00, "p3": "comparison-toggle-prior"},
            {"p0": "2026-01-02", "p1": "Current Grocery", "p2": 120.00, "p3": "comparison-toggle-current"},
        ],
    )
    core_conn.commit()

    response = client.get("/comparison")

    assert response.status_code == 200
    assert_markup(response, "comparison_chart_line", "comparison_chart_bar", "comparison_chart_table")
    assert_has_element(response, "div", attrs={"role": "group", "aria-label": "Monthly spending visualization"})
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
