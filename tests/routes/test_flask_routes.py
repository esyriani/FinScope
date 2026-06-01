"""Route-level tests for the Flask application."""

from datetime import date as real_date
import io
import re

import pytest

from finance_app.core.csrf import CSRF_FIELD_NAME, CSRF_SESSION_KEY
from finance_app.modules.comparison import service as comparison_service


class FixedDate(real_date):
    """Fixed replacement for date.today in comparison route tests."""

    @classmethod
    def today(cls):
        """Return a deterministic current date."""
        return cls(2026, 5, 11)


def set_csrf_token(client, token="test-csrf-token"):
    """Store a CSRF token in the test client's session."""
    with client.session_transaction() as session:
        session[CSRF_SESSION_KEY] = token
    return token


def strip_script_blocks(html):
    """Remove script blocks so visible-copy assertions ignore i18n keys."""
    return re.sub(r"<script\b[^>]*>.*?</script>", "", html, flags=re.IGNORECASE | re.DOTALL)


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
    assert b"<html" in response.data.lower()


def test_base_template_uses_local_hashed_assets(client):
    """Verify that shared browser assets are served locally with content hashes."""
    response = client.get("/")

    assert response.status_code == 200
    assert b"cdn.jsdelivr.net" not in response.data
    assert b'?v=1"' not in response.data
    assert re.search(
        rb"/static/vendor/bootstrap/5\.3\.3/css/bootstrap\.min\.css\?v=[0-9a-f]{12}",
        response.data,
    )
    assert re.search(rb"/static/js/core\.js\?v=[0-9a-f]{12}", response.data)


def test_taxonomy_category_create_and_delete_routes_persist_changes(client, db_conn):
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

    category = db_conn.execute(
        """
        SELECT id, description, instruction
        FROM categories
        WHERE name = 'Subscriptions'
        """
    ).fetchone()
    assert create_response.status_code == 200
    assert category is not None
    assert category["description"] == "Recurring paid services"

    delete_response = client.post(
        "/taxonomy/categories/delete",
        data={
            CSRF_FIELD_NAME: token,
            "category_id": category["id"],
        },
        follow_redirects=True,
    )

    remaining = db_conn.execute(
        """
        SELECT COUNT(*) AS count
        FROM categories
        WHERE name = 'Subscriptions'
        """
    ).fetchone()["count"]
    assert delete_response.status_code == 200
    assert remaining == 0


def test_taxonomy_category_delete_route_refuses_in_use_category(client, db_conn):
    """Verify that the category delete route keeps categories used by transactions."""
    category_id = db_conn.execute(
        """
        INSERT INTO categories (name)
        VALUES ('Transit')
        """
    ).lastrowid
    db_conn.execute(
        """
        INSERT INTO transactions (
            tx_date,
            description,
            amount,
            category_id,
            fingerprint
        )
        VALUES ('2026-01-02', 'METRO PASS', 91.25, ?, 'route-delete-guard')
        """,
        (category_id,),
    )
    db_conn.commit()

    response = client.post(
        "/taxonomy/categories/delete",
        data={
            CSRF_FIELD_NAME: set_csrf_token(client),
            "category_id": category_id,
        },
        follow_redirects=True,
    )

    category_count = db_conn.execute(
        """
        SELECT COUNT(*) AS count
        FROM categories
        WHERE id = ?
        """,
        (category_id,),
    ).fetchone()["count"]
    assert response.status_code == 200
    assert b"Only unused categories can be deleted." in response.data
    assert b"Category Transit cannot be deleted because it is in use" not in response.data
    assert b"bi-lock" not in response.data
    assert category_count == 1


def test_upload_route_rejects_missing_file_without_statement_insert(client, db_conn):
    """Verify that upload validation exits before creating a statement row."""
    statement_type_id = db_conn.execute(
        """
        SELECT id
        FROM statement_types
        WHERE active = 1
        ORDER BY id
        LIMIT 1
        """
    ).fetchone()["id"]

    response = client.post(
        "/upload",
        data={
            CSRF_FIELD_NAME: set_csrf_token(client),
            "account_name": "Personal",
            "statement_type_id": str(statement_type_id),
        },
        follow_redirects=True,
    )

    statement_count = db_conn.execute(
        "SELECT COUNT(*) AS count FROM statements"
    ).fetchone()["count"]
    assert response.status_code == 200
    assert b"Please choose a statement file." in response.data
    assert statement_count == 0


def test_upload_route_renders_statement_detail_modal(client, db_conn):
    """Verify uploaded statement rows open processed details by double-click target."""
    paid_from_account_id = db_conn.execute(
        """
        INSERT INTO accounts (name, account_type)
        VALUES ('Main checking', 'checking')
        """
    ).lastrowid
    account_id = db_conn.execute(
        """
        INSERT INTO accounts (name, account_type, paid_from_account_id)
        VALUES ('RBC Visa', 'credit_card', ?)
        """,
        (paid_from_account_id,),
    ).lastrowid
    statement_type_id = db_conn.execute(
        """
        SELECT id
        FROM statement_types
        WHERE parser_type = 'credit_card'
        LIMIT 1
        """
    ).fetchone()["id"]
    statement_id = db_conn.execute(
        """
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
            ?, ?, 'visa.csv', 'statement-detail-route',
            'csv', 'Date,Description,Amount\n2026-01-02,Corner store,12.34',
            'completed', '2026-05-11T10:00:00Z', '2026-05-11T10:00:02Z',
            2, 1, 3, 4, '2026-05-11T09:59:59Z'
        )
        """,
        (account_id, statement_type_id),
    ).lastrowid
    db_conn.executemany(
        """
        INSERT INTO transactions (
            statement_id,
            account_id,
            tx_date,
            description,
            amount,
            category,
            fingerprint
        )
        VALUES (?, ?, ?, ?, ?, 'Food', ?)
        """,
        [
            (statement_id, account_id, "2026-01-02", "Corner store", 12.34, "statement-detail-1"),
            (statement_id, account_id, "2026-01-03", "Cafe", 4.56, "statement-detail-2"),
        ],
    )
    db_conn.commit()

    response = client.get("/upload")

    assert response.status_code == 200
    assert f'data-row-edit-target="#statement-details-{statement_id}"'.encode() in response.data
    assert f'id="statement-details-{statement_id}"'.encode() in response.data
    assert b"Statement details" in response.data
    assert b"Processing summary" in response.data
    assert b"Current statement transactions" in response.data
    assert b"Main checking" in response.data
    assert b"Date,Description,Amount" in response.data
    assert b"data-row-action" in response.data


def test_upload_route_renders_interac_import_guidance(client, db_conn):
    """Verify Interac uploads explain ordering and skipped or ignored rows."""
    account_id = db_conn.execute(
        """
        INSERT INTO accounts (name, account_type)
        VALUES ('TD Interac Sent', 'checking')
        """
    ).lastrowid
    statement_type_id = db_conn.execute(
        """
        SELECT id
        FROM statement_types
        WHERE parser_type = 'interac_etransfer'
        LIMIT 1
        """
    ).fetchone()["id"]
    db_conn.execute(
        """
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
            ?, ?, 'interac-sent.csv', 'interac-guidance-route',
            'csv', 'Date Sent,Recipient,Amount,Method,Status',
            'completed', 29, 1, 76, '2026-05-14T17:41:24Z'
        )
        """,
        (account_id, statement_type_id),
    )
    db_conn.commit()

    response = client.get("/upload")

    assert response.status_code == 200
    assert b"Import matching checking statements first" in response.data
    assert b"Interac history only enriches existing checking rows." in response.data
    assert b"skipped rows are ambiguous matches" in response.data
    assert b"no matching checking transaction yet" in response.data


def test_transactions_route_renders_category_source_badges_and_filter(client, db_conn):
    """Verify transaction source provenance is visible on the transaction page."""
    db_conn.execute(
        """
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
        """
    )
    db_conn.commit()

    response = client.get("/transactions?period=all")
    body = response.get_data(as_text=True)
    compact_body = " ".join(body.split())

    assert response.status_code == 200
    assert b"Categorization method" in response.data
    assert b"All methods" in response.data
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
    assert 'class="transaction-date text-nowrap"' in body
    assert "transaction-action-menu" in body
    assert "Edit category" in body
    assert "Ignore transaction" in body
    assert "View evidence" not in body
    assert b"data-category-description-select" in response.data
    assert b'value="transaction_only" data-rule-save-mode checked' in response.data
    assert b"data-rule-save-only" in response.data
    assert b"modal-dialog-fit-content" in response.data
    assert b'value="12.34"' in response.data
    assert b"data-rule-exact-amount" in response.data
    assert b"Food and drink, including groceries" in response.data
    assert b"Marks transactions that may be useful for tax preparation" in response.data
    assert body.index("This transaction only") < body.index("Save rule")


def test_dashboard_route_does_not_render_assignment_tooltips(client, db_conn):
    """Verify category assignment tooltips stay out of dashboard filters."""
    db_conn.execute(
        """
        INSERT INTO transactions (tx_date, description, amount, category, fingerprint)
        VALUES ('2026-01-02', 'Dashboard Store', 12.34, 'Food', 'route-dashboard-tooltip')
        """
    )
    db_conn.commit()

    response = client.get("/dashboard?period=all")
    tag_response = client.get("/dashboard?period=all&breakdown=tag")
    untagged_response = client.get("/dashboard?period=all&breakdown=tag&show_untagged=1")

    assert response.status_code == 200
    assert tag_response.status_code == 200
    assert untagged_response.status_code == 200
    assert b"data-category-description-select" not in response.data
    assert b"Breakdown" in response.data
    assert b"Spending by category" in response.data
    assert b"Category detail" in response.data
    assert b"Spending by tag" in tag_response.data
    assert b"Tag detail" in tag_response.data
    assert b"Tagged spending can count the same transaction more than once." in tag_response.data
    assert b"Show untagged" in tag_response.data
    assert b"Show income" in response.data
    assert b"Select all categories" in response.data
    assert b"Select all tags" in response.data
    assert b"data-ajax-refresh-link" in response.data
    assert b'"categoryLabels": []' in tag_response.data
    assert b"Hide untagged" in untagged_response.data
    assert b'"categoryLabels": ["Untagged"]' in untagged_response.data


def test_category_filters_offer_analysis_category_preset(client, db_conn):
    """Verify category filters can bulk-select categories used for analysis."""
    db_conn.execute(
        """
        INSERT INTO categories (name, builtin_key, description, instruction)
        VALUES ('System adjustment', 'system_adjustment', '', '')
        """
    )
    db_conn.execute(
        """
        INSERT INTO transactions (tx_date, description, amount, category, category_source, fingerprint)
        VALUES ('2026-01-02', 'Analysis category store', 12.34, 'Food', 'rule', 'route-analysis-category-filter')
        """
    )
    db_conn.commit()

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
        body = response.get_data(as_text=True)

        assert response.status_code == 200
        assert body.count('data-select-preset-label="Select analysis categories"') == expected_count
        preset_values = re.findall(r"data-select-preset-exclude-values='([^']+)'", body)
        assert len(preset_values) == expected_count
        assert all("System adjustment" in value for value in preset_values)
        assert "Transfers" in body
        assert "UNKNOWN" in body


def test_comparison_route_renders_complete_unknown_warning(client, db_conn, monkeypatch):
    """Verify comparison warning placeholders render with category and share values."""
    monkeypatch.setattr(comparison_service, "date", FixedDate)
    db_conn.executemany(
        """
        INSERT INTO transactions (tx_date, description, amount, category, category_source, fingerprint)
        VALUES (?, ?, ?, ?, 'rule', ?)
        """,
        [
            ("2026-04-02", "Unknown Prior", 40.00, "UNKNOWN", "route-comparison-unknown-prior"),
            ("2026-04-03", "Prior Grocery", 60.00, "Food", "route-comparison-food-prior"),
            ("2026-05-02", "Unknown Current", 70.00, "UNKNOWN", "route-comparison-unknown-current"),
            ("2026-05-03", "Current Grocery", 30.00, "Food", "route-comparison-food-current"),
        ],
    )
    db_conn.commit()

    response = client.get("/comparison?years=2026&period_comparison=month_previous")
    visible_body = strip_script_blocks(response.get_data(as_text=True))

    assert response.status_code == 200
    assert "UNKNOWN accounts for 55.0%" in visible_body
    assert "UNKNOWN accounts for 70.0%" in visible_body
    assert "because accounts for %" not in visible_body


def test_financial_reporting_pages_render_french_copy(client, db_conn):
    """Verify reporting pages localize visible labels and explanatory text."""
    db_conn.execute(
        """
        UPDATE user_settings
        SET value = 'fr'
        WHERE key = 'ui_language'
          AND user_id = (SELECT id FROM users WHERE username = 'owner')
        """
    )
    db_conn.executemany(
        """
        INSERT INTO transactions (
            tx_date,
            description,
            amount,
            category,
            category_source,
            fingerprint
        )
        VALUES (?, ?, ?, ?, 'rule', ?)
        """,
        [
            ("2025-05-02", "Prior grocery", 80.00, "Food", "route-fr-prior"),
            ("2026-01-02", "Utility bill", 50.00, "Utilities", "route-fr-recurring-1"),
            ("2026-02-02", "Utility bill", 50.00, "Utilities", "route-fr-recurring-2"),
            ("2026-03-02", "Utility bill", 50.00, "Utilities", "route-fr-recurring-3"),
            ("2026-04-02", "Utility bill", 55.00, "Utilities", "route-fr-recurring-4"),
            ("2026-04-04", "Prior unknown", 400.00, "UNKNOWN", "route-fr-unknown-prior"),
            ("2026-05-02", "Current grocery", 120.00, "Food", "route-fr-current"),
            ("2026-05-03", "Payroll", -800.00, "Income", "route-fr-income"),
            ("2026-05-04", "Current unknown", 900.00, "UNKNOWN", "route-fr-unknown-current"),
        ],
    )
    db_conn.commit()

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

    home = home_response.get_data(as_text=True)
    dashboard = dashboard_response.get_data(as_text=True)
    comparison = comparison_response.get_data(as_text=True)
    calendar = calendar_response.get_data(as_text=True)
    recurring = recurring_response.get_data(as_text=True)

    visible_home = strip_script_blocks(home)
    visible_dashboard = strip_script_blocks(dashboard)
    visible_comparison = strip_script_blocks(comparison)
    visible_calendar = strip_script_blocks(calendar)
    visible_recurring = strip_script_blocks(recurring)

    assert "Ce qui demande une attention" in visible_home
    assert "À traiter" in visible_home
    assert "Centre de commande financier" not in visible_home
    assert "Financial command center" not in visible_home
    assert "Needs attention" not in visible_home

    assert "Tableau de bord" in visible_dashboard
    assert "Vue actuelle : Depuis le début de l&#39;année." in visible_dashboard
    assert "Dépenses par catégorie" in visible_dashboard
    assert "Analyse des marchands" in visible_dashboard
    assert "Sélectionner les catégories d’analyse" in visible_dashboard
    assert "year to date" not in visible_dashboard
    assert "Merchant analytics" not in visible_dashboard

    assert "Comparaison annuelle" in visible_comparison
    assert "Dépenses mensuelles par année" in visible_comparison
    assert "Comparaison de périodes" in visible_comparison
    assert "La comparaison par catégorie peut être peu fiable" in visible_comparison
    assert "Les constats par catégorie peuvent être incomplets" in visible_comparison
    assert "Category comparison may be unreliable" not in visible_comparison
    assert "Category insights may be incomplete" not in visible_comparison
    assert "Period comparison" not in visible_comparison

    assert "Calendrier" in visible_calendar
    assert "Sorties comptabilisées" in visible_calendar
    assert "Récurrences prévues" in visible_calendar
    assert "Posted outflows" not in visible_calendar

    assert "Récurrences" in visible_recurring
    assert "Activité récurrente" in visible_recurring
    assert "Fréquence" in visible_recurring
    assert "Recurring activity" not in visible_recurring


def test_review_route_renders_category_source_for_review_rows(client, db_conn):
    """Verify review details expose source provenance for rows needing review."""
    db_conn.execute(
        """
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
        """
    )
    db_conn.commit()

    response = client.get("/review")

    assert response.status_code == 200
    assert b"Category source" in response.data
    assert b">AI</span>" in response.data
    assert b"72%" in response.data


def test_comparison_route_renders_visual_key_insights(client, db_conn, monkeypatch):
    """Verify comparison insights render as visual cards when period data exists."""
    monkeypatch.setattr(comparison_service, "date", FixedDate)
    db_conn.executemany(
        """
        INSERT INTO transactions (tx_date, description, amount, category, category_source, fingerprint)
        VALUES (?, ?, ?, 'Food', 'rule', ?)
        """,
        [
            ("2026-04-02", "Prior Grocery", 100.00, "comparison-route-prior"),
            ("2026-05-02", "Current Grocery", 240.00, "comparison-route-current"),
        ],
    )
    db_conn.commit()

    response = client.get("/comparison")

    assert response.status_code == 200
    assert b"comparisonInsightCarousel" in response.data
    assert b"insight-grid" in response.data
    assert b"insight-card-danger" in response.data
    assert b"insight-summary text-danger" in response.data
    assert b"insight-current-value text-danger" in response.data
    assert b"Previous insight group" in response.data
    assert b"Next insight group" in response.data
    assert b"insight-bar-fill" in response.data


def test_comparison_route_renders_year_chart_type_toggle(client, db_conn):
    """Verify the year comparison chart exposes line and bar display modes."""
    db_conn.executemany(
        """
        INSERT INTO transactions (tx_date, description, amount, category, category_source, fingerprint)
        VALUES (?, ?, ?, 'Food', 'rule', ?)
        """,
        [
            ("2025-01-02", "Prior Grocery", 100.00, "comparison-toggle-prior"),
            ("2026-01-02", "Current Grocery", 120.00, "comparison-toggle-current"),
        ],
    )
    db_conn.commit()

    response = client.get("/comparison")

    assert response.status_code == 200
    assert b"comparison_chart_line" in response.data
    assert b"comparison_chart_bar" in response.data
    assert b"Monthly spending chart type" in response.data
    assert b"cdn.jsdelivr.net" not in response.data
    assert b"js/comparison-charts.js?v=40" not in response.data
    assert re.search(
        rb"/static/js/comparison-charts\.js\?v=[0-9a-f]{12}",
        response.data,
    )
    assert re.search(
        rb"/static/vendor/echarts/5\.6\.0/echarts\.min\.js\?v=[0-9a-f]{12}",
        response.data,
    )


def test_upload_route_rejects_duplicate_statement_checksum(client, db_conn, monkeypatch):
    """Verify that duplicate uploads are rejected before queueing background work."""
    statement_type_id = db_conn.execute(
        """
        SELECT id
        FROM statement_types
        WHERE active = 1
        ORDER BY id
        LIMIT 1
        """
    ).fetchone()["id"]
    db_conn.execute(
        """
        INSERT INTO statements (
            statement_type_id,
            filename,
            checksum,
            raw_text,
            uploaded_at
        )
        VALUES (?, 'already.csv', ?, 'Date,Description,Amount', '2026-05-11T12:00:00Z')
        """,
        (statement_type_id, "known-checksum"),
    )
    db_conn.commit()
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

    statement_count = db_conn.execute(
        "SELECT COUNT(*) AS count FROM statements"
    ).fetchone()["count"]
    assert response.status_code == 200
    assert b"This statement was already uploaded as already.csv on 2026-05-11T12:00:00Z" in response.data
    assert statement_count == 1
