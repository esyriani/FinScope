"""Route tests for user-pinned report views."""

from sqlalchemy import insert, select
from tests.support.context_services import seed_reporting_data
from tests.support.database import set_owner_setting
from tests.support.html import assert_visible_text, response_html
from tests.support.web import csrf_enabled_client

from finance_app.database.tables import categories as categories_table
from finance_app.database.tables import pinned_reports as pinned_reports_table
from finance_app.modules.reports.constants import REPORT_BASIS_CASH_FLOW, REPORT_MEASURE_INCOME, REPORT_MEASURE_SPENDING
from finance_app.modules.reports.definitions import REPORT_INCOME, REPORT_OVERVIEW, REPORT_TAXONOMY
from finance_app.modules.reports.pins import REPORT_TYPE_ACCOUNT


def overview_pin_payload(**overrides):
    """Return a normalized overview pin payload."""
    payload = {
        "report_type": REPORT_OVERVIEW,
        "period": "custom",
        "date_from": "2026-01-01",
        "date_to": "2026-01-31",
        "measure": REPORT_MEASURE_SPENDING,
        "basis": REPORT_BASIS_CASH_FLOW,
        "classification_scope": "categorized",
        "category_filters": [],
        "tag_filters": [],
    }
    payload.update(overrides)
    return payload


def list_pins(conn, user_id):
    """Return pinned reports for a user."""
    return (
        conn.execute(
            select(pinned_reports_table)
            .where(pinned_reports_table.c.user_id == user_id)
            .order_by(pinned_reports_table.c.sort_order, pinned_reports_table.c.id)
        )
        .mappings()
        .fetchall()
    )


def test_reports_overview_renders_empty_pinned_reports_state(client, core_conn):
    """Verify Reports overview owns the pinned report section."""
    seed_reporting_data(core_conn)

    response = client.get("/reports?period=custom&date_from=2026-01-01&date_to=2026-01-31")
    body = response_html(response)

    assert response.status_code == 200
    assert_visible_text(
        response,
        "Pinned reports",
        "No pinned reports yet. Open any report and use Pin report to save it here.",
        "Pin report",
    )
    assert "data-report-pin-button" in body
    assert body.index("reports-pinned-section") < body.index("reports-overview-filters")


def test_pin_report_endpoint_persists_exact_view_once(csrf_client, core_conn):
    """Verify pinning a report creates one user-owned exact view."""
    seed_reporting_data(core_conn)
    user_id = int(csrf_client.client.test_user["id"])
    payload = overview_pin_payload()

    response = csrf_client.post("/reports/pins", json=payload)
    duplicate = csrf_client.post("/reports/pins", json=payload)

    rows = list_pins(core_conn, user_id)
    assert response.status_code == 200
    assert response.get_json()["message"] == "Report pinned."
    assert duplicate.status_code == 200
    assert duplicate.get_json()["already_pinned"] is True
    assert len(rows) == 1
    assert rows[0]["report_type"] == REPORT_OVERVIEW
    assert rows[0]["period"] == "custom"
    assert rows[0]["date_from"] == "2026-01-01"
    assert rows[0]["classification_scope"] == "categorized"


def test_reports_overview_renders_live_pinned_report_card(client, csrf_client, core_conn):
    """Verify overview cards render saved views with current report values."""
    seed_reporting_data(core_conn)
    csrf_client.post("/reports/pins", json=overview_pin_payload())

    response = client.get("/reports?period=custom&date_from=2026-01-01&date_to=2026-01-31")
    body = response_html(response)

    assert response.status_code == 200
    assert_visible_text(response, "Pinned reports", "Saved report views with current values.", "Overview")
    assert "data-pinned-card" in body
    assert "data-pinned-edit-toggle" in body
    assert 'href="/reports?period=custom' in body
    pinned_section = body.split('id="reports-pinned-reports"', 1)[1].split("</section>", 1)[0]
    assert "reports-pinned-card-link" in pinned_section
    assert "badge text-bg-secondary" not in pinned_section
    assert "reports-pinned-card-actions" not in pinned_section
    assert "reports-pinned-drag-handle" not in pinned_section
    assert "reports-pinned-remove-toggle" not in pinned_section
    assert "data-pinned-drag-handle" not in pinned_section
    assert "draggable=" not in pinned_section
    assert 'data-pinned-move="up"' in pinned_section
    assert 'data-pinned-move="down"' in pinned_section
    assert "data-pinned-remove-toggle" in pinned_section
    assert "Report target no longer exists." not in body


def test_pin_report_limit_is_enforced(csrf_client, core_conn):
    """Verify user settings limit how many exact report views can be pinned."""
    seed_reporting_data(core_conn)
    user_id = int(csrf_client.client.test_user["id"])
    core_conn.execute(
        insert(pinned_reports_table).values(
            user_id=user_id,
            report_type=REPORT_OVERVIEW,
            period="custom",
            date_from="2026-01-01",
            date_to="2026-01-31",
            measure=REPORT_MEASURE_SPENDING,
            basis=REPORT_BASIS_CASH_FLOW,
            classification_scope="categorized",
            fingerprint="already-at-limit",
            sort_order=0,
        )
    )
    set_owner_setting(core_conn, "pinned_report_limit", "1")

    response = csrf_client.post(
        "/reports/pins",
        json=overview_pin_payload(report_type=REPORT_INCOME, measure=REPORT_MEASURE_INCOME),
    )
    data = response.get_json()

    assert response.status_code == 400
    assert data["message"] == "Pinned report limit reached."
    assert data["overview_url"] == "/reports"
    assert data["settings_url"] == "/settings"


def test_save_pinned_reports_edits_order_title_and_removal(csrf_client, core_conn):
    """Verify edit mode persists order, titles, and unpin choices."""
    seed_reporting_data(core_conn)
    user_id = int(csrf_client.client.test_user["id"])
    csrf_client.post("/reports/pins", json=overview_pin_payload())
    csrf_client.post(
        "/reports/pins",
        json=overview_pin_payload(report_type=REPORT_INCOME, measure=REPORT_MEASURE_INCOME),
    )
    rows = list_pins(core_conn, user_id)

    response = csrf_client.post(
        "/reports/pins/edit",
        json={
            "pins": [
                {"id": rows[1]["id"], "short_title": "Credits", "remove": False},
                {"id": rows[0]["id"], "short_title": "", "remove": True},
            ]
        },
    )
    remaining = list_pins(core_conn, user_id)

    assert response.status_code == 200
    assert response.get_json()["message"] == "Pinned reports saved."
    assert "reports-pinned-section" in response.get_json()["html"]
    assert len(remaining) == 1
    assert remaining[0]["report_type"] == REPORT_INCOME
    assert remaining[0]["short_title"] == "Credits"
    assert remaining[0]["sort_order"] == 0


def test_viewer_can_manage_only_their_own_pinned_reports(client, viewer_client, core_conn):
    """Verify viewer pin mutations are allowed but remain user-scoped."""
    seed_reporting_data(core_conn)
    viewer_csrf = csrf_enabled_client(viewer_client)

    response = viewer_csrf.post("/reports/pins", json=overview_pin_payload())
    owner_overview = client.get("/reports?period=custom&date_from=2026-01-01&date_to=2026-01-31")
    viewer_overview = viewer_client.get("/reports?period=custom&date_from=2026-01-01&date_to=2026-01-31")

    assert response.status_code == 200
    assert "data-pinned-card" not in response_html(owner_overview)
    assert "data-pinned-card" in response_html(viewer_overview)


def test_missing_pinned_report_target_renders_degraded_card(client, core_conn):
    """Verify missing targets remain removable from overview edit mode."""
    seed_reporting_data(core_conn)
    user_id = int(client.test_user["id"])
    core_conn.execute(
        insert(pinned_reports_table).values(
            user_id=user_id,
            report_type=REPORT_TYPE_ACCOUNT,
            target_kind="account",
            period="custom",
            date_from="2026-01-01",
            date_to="2026-01-31",
            measure=REPORT_MEASURE_SPENDING,
            basis=REPORT_BASIS_CASH_FLOW,
            classification_scope="categorized",
            fingerprint="missing-account-target",
            sort_order=0,
        )
    )
    core_conn.commit()

    response = client.get("/reports?period=custom&date_from=2026-01-01&date_to=2026-01-31")

    assert response.status_code == 200
    assert_visible_text(response, "Missing report target", "Report target no longer exists.", "Edit pins")


def test_report_detail_pages_expose_pin_report_action(client, core_conn):
    """Verify detail reports expose the current-view pin action."""
    seed_reporting_data(core_conn)
    category_id = core_conn.execute(select(categories_table.c.id).where(categories_table.c.name == "Food")).scalar_one()

    response = client.get(f"/reports/categories/{category_id}?period=custom&date_from=2026-01-01&date_to=2026-01-31")

    assert response.status_code == 200
    assert_visible_text(response, "Pin report")
    assert "data-report-pin-button" in response_html(response)
    assert f'"report_type": "{REPORT_TAXONOMY}"' in response_html(response)
