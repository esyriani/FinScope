"""Tests for recurring pattern routes and persistence helpers."""

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from flask import render_template
from sqlalchemy import text
from tests.support.html import (
    assert_has_element,
    assert_input,
    assert_markup,
    assert_no_element,
    assert_not_markup,
    assert_option,
    assert_visible_text,
    parse_html,
)
from tests.support.web import set_csrf_token

from finance_app.core.csrf import CSRF_HEADER_NAME
from finance_app.modules.merchants.repository import get_or_create_merchant_for_name
from finance_app.modules.recurring.forms import parse_expected_day, recurring_pattern_payload
from finance_app.modules.recurring.patterns import (
    get_recurring_pattern,
    get_recurring_pattern_by_merchant_type,
    get_recurring_pattern_metadata,
    normalize_active,
    normalize_frequency,
    normalize_optional_int,
    normalize_optional_money,
    normalize_user_status,
    recurring_pattern_key,
    upsert_recurring_pattern,
)
from finance_app.modules.recurring.presenter import build_recurring_activity_json
from finance_app.modules.recurring.service import (
    build_recurring_calendar_days,
    recurring_empty_state_message,
    recurring_status_detail,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def recurring_json(client, path, payload):
    """POST a JSON recurring-pattern payload with CSRF protection."""
    token = set_csrf_token(client)
    return client.post(
        path,
        json=payload,
        headers={CSRF_HEADER_NAME: token},
    )


def valid_payload(**overrides):
    """Build a valid recurring route payload."""
    payload = {
        "patternKey": "NETFLIX::spending",
        "merchant": "NETFLIX",
        "type": "spending",
    }
    payload.update(overrides)
    return payload


def test_recurring_confirm_ignore_and_edit_routes_persist_metadata(owner_client, core_conn):
    """Verify recurring pattern mutation routes persist user metadata."""
    merchant_id = get_or_create_merchant_for_name(core_conn, "NETFLIX")["id"]
    core_conn.commit()
    payload = valid_payload(merchantId=merchant_id, matchType="merchant")

    confirm = recurring_json(owner_client, "/recurring/patterns/confirm", payload)
    confirmed = get_recurring_pattern_by_merchant_type(core_conn, merchant_id, "spending")

    ignore = recurring_json(owner_client, "/recurring/patterns/ignore", payload)
    ignored = get_recurring_pattern_by_merchant_type(core_conn, merchant_id, "spending")

    edit = recurring_json(
        owner_client,
        "/recurring/patterns/edit",
        valid_payload(
            merchantId=merchant_id,
            matchType="merchant",
            frequency="Monthly-like",
            expectedDate="2026-05-14",
            typicalAmount="18.99",
            dateToleranceDays="3",
            amountTolerance="2.50",
            active="0",
        ),
    )
    edited = get_recurring_pattern_by_merchant_type(core_conn, merchant_id, "spending")

    assert confirm.status_code == 200
    assert confirm.get_json() == {"ok": True, "userStatus": "confirmed", "active": 1}
    assert confirmed["user_status"] == "confirmed"
    assert confirmed["merchant_id"] == merchant_id
    assert confirmed["match_type"] == "merchant"
    assert confirmed["active"] == 1

    assert ignore.status_code == 200
    assert ignore.get_json() == {"ok": True, "userStatus": "ignored", "active": 0}
    assert ignored["user_status"] == "ignored"
    assert ignored["active"] == 0

    assert edit.status_code == 200
    assert edit.get_json() == {"ok": True, "userStatus": "edited", "active": 0}
    assert edited["user_status"] == "edited"
    assert edited["frequency"] == "Monthly-like"
    assert edited["expected_day"] == 14
    assert edited["typical_amount"] == 18.99
    assert edited["date_tolerance_days"] == 3
    assert edited["amount_tolerance"] == 2.50
    assert edited["active"] == 0


def test_recurring_routes_preserve_keyword_fuzzy_patterns(owner_client, core_conn):
    """Verify recurring routes keep keyword-fuzzy patterns unbound."""
    confirm = recurring_json(
        owner_client,
        "/recurring/patterns/confirm",
        valid_payload(matchType="keyword"),
    )
    pattern = get_recurring_pattern(core_conn, "NETFLIX::spending")

    assert confirm.status_code == 200
    assert pattern["merchant_id"] is None
    assert pattern["match_type"] == "keyword"
    assert core_conn.execute(text("SELECT COUNT(*) AS count FROM merchants")).fetchone()._mapping["count"] == 0


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"patternKey": "A::spending", "merchant": "", "type": "spending"},
        {"patternKey": "A::neutral", "merchant": "A", "type": "neutral"},
    ],
)
def test_recurring_routes_reject_incomplete_payloads(owner_client, payload):
    """Verify recurring route payload validation is surfaced as JSON."""
    response = recurring_json(owner_client, "/recurring/patterns/confirm", payload)

    assert response.status_code == 400
    assert response.get_json() == {
        "ok": False,
        "message": "Recurring pattern payload is incomplete.",
    }


def test_recurring_page_uses_shared_status_filter_links(owner_client):
    """Verify recurring status filters are URL-driven instead of client-only buttons."""
    response = owner_client.get(
        "/recurring?view=list&statuses=overdue&account_id=12&merchant_id=34&merchant_query=NETFLIX"
    )

    assert response.status_code == 200
    assert_has_element(response, None, attrs={"aria-label": "Status filter"})
    assert_has_element(response, None, attrs={"class": "recurring-tabs"})
    assert_has_element(response, "a", attrs={"id": "recurring-list-tab", "role": "tab"})
    assert_has_element(response, "a", attrs={"id": "recurring-calendar-tab", "role": "tab"})
    assert_has_element(response, "a", attrs={"data-recurring-status-filter": "overdue", "aria-pressed": "true"})
    assert_has_element(response, "a", attrs={"data-recurring-ajax-link": True})
    assert_input(response, name="statuses", value="overdue")
    assert_input(response, name="account_id", value="12")
    assert_input(response, name="merchant_id", value="34")
    assert_input(response, name="merchant_query", value="NETFLIX")
    assert_markup(response, 'href="/recurring?month=', "view=list", "view=calendar")
    assert_markup(response, "account_id=12", "merchant_id=34", "merchant_query=NETFLIX")
    assert_visible_text(response, "Merchant: NETFLIX")
    assert_no_element(response, None, attrs={"id": "recurring-status"})
    assert_not_markup(response, "data-recurring-activity-filter")


def test_recurring_page_exposes_compact_table_and_export_status_details(owner_client):
    """Verify recurring list and export columns expose compact status context."""
    response = owner_client.get("/recurring?view=list")
    document = parse_html(response)
    activity_table = document.find_all("table", attrs={"id": "recurring-activity-table"})[0]

    assert response.status_code == 200
    assert_has_element(response, None, attrs={"data-recurring-dynamic": True})
    assert document.has_element(
        "div",
        attrs={
            "id": "recurring-dynamic",
            "data-recurring-url": "/recurring",
            "data-recurring-confirm-url": "/recurring/patterns/confirm",
            "data-recurring-ignore-url": "/recurring/patterns/ignore",
            "data-recurring-edit-url": "/recurring/patterns/edit",
        },
    )
    assert_has_element(response, None, attrs={"class": "recurring-summary-layout"})
    assert_has_element(response, None, attrs={"class": "recurring-metric-carousel"})
    assert_has_element(
        response,
        "input",
        attrs={
            "id": "recurring-month",
            "data-flatpickr-month": True,
            "data-flatpickr-submit-on-change": True,
        },
    )
    assert_visible_text(
        response,
        "Repeating merchants detected for the selected month.",
        "No recurring activity detected for this month.",
        "Confidence level: High",
    )
    assert_option(response, value="High", text="High", selected=True)
    assert document.has_element(
        "button",
        attrs={"data-sort-column": "8", "data-sort-type": "number"},
        text="Observed months",
    )
    assert_has_element(
        response,
        None,
        attrs={
            "data-paginated-table": True,
            "data-pagination-label": "Recurring activity pages",
        },
    )
    assert_has_element(response, None, attrs={"data-export-visible-source": "#recurring-activity-table"})
    assert_no_element(response, None, attrs={"data-export-excel-extension": "xlsx"})
    assert_has_element(response, None, attrs={"data-recurring-batch-table": True})
    assert json.loads(activity_table.attrs["data-all-recurring-ids"]) == []
    assert_has_element(response, None, attrs={"data-recurring-select-all": True})
    assert_has_element(response, "td", attrs={"colspan": "10"})
    assert_visible_text(
        response,
        "Confirm selected",
        "Remove selected",
        "Status detail",
        "Matched date",
        "Actual amount",
    )


def test_recurring_activity_template_serializes_string_batch_ids_as_json(app):
    """Verify string recurring ids survive HTML attribute parsing as JSON."""
    recurring_ids = ["recurring-0", "recurring-1"]

    with app.test_request_context("/recurring"):
        body = render_template(
            "_recurring_activity.html",
            can_edit_recurring=True,
            all_recurring_ids=recurring_ids,
            recurring_empty_state_message="No recurring activity detected for this month.",
            recurring_items=[],
            table_page_size=25,
        )

    document = parse_html(body)
    activity_table = document.find_all("table", attrs={"id": "recurring-activity-table"})[0]

    assert json.loads(activity_table.attrs["data-all-recurring-ids"]) == recurring_ids


def test_recurring_page_all_confidence_filter_is_explicit(owner_client):
    """Verify All confidence is opt-in now that High confidence is the default."""
    response = owner_client.get("/recurring?view=list&confidence=all")

    assert response.status_code == 200
    assert_visible_text(
        response,
        "Confidence level: All confidence",
        "No recurring activity matches the current filters.",
    )
    assert_option(response, value="all", text="All confidence", selected=True)
    assert_input(response, name="confidence", value="all")
    assert_markup(response, "confidence=all")


def test_table_export_script_uses_displayed_rows_without_scope_prompt():
    """Verify shared table export is limited to rows already displayed in the DOM."""
    body = (PROJECT_ROOT / "src" / "finance_app" / "static" / "js" / "exports.js").read_text(encoding="utf-8")

    assert "function tableRowsForExport(table)" in body
    assert "visibleExportSourceIds(table)" in body
    assert "isDisplayedExportRow(row)" in body
    assert "chooseTableExportScope" not in body
    assert "Displayed rows" not in body
    assert "Entire table" not in body
    assert "Export rows" not in body


def test_table_export_script_does_not_scrape_server_pagination_pages():
    """Verify generic table export does not crawl server-rendered pagination pages."""
    body = (PROJECT_ROOT / "src" / "finance_app" / "static" / "js" / "exports.js").read_text(encoding="utf-8")

    assert "serverPaginationPlan" not in body
    assert "numericPaginationLinks" not in body
    assert "inferPaginationPageParameter" not in body
    assert "fetchExportTablePage" not in body
    assert "fetch(" not in body
    assert "DOMParser" not in body
    assert "tableExportTablesForScope" not in body
    assert "tableRowsForExportTables" not in body
    assert "page-link[href]" not in body


def test_table_export_script_builds_real_xlsx_tables_with_totals():
    """Verify shared Excel exports build real workbooks with typed table totals."""
    body = (PROJECT_ROOT / "src" / "finance_app" / "static" / "js" / "exports.js").read_text(encoding="utf-8")
    writer = (PROJECT_ROOT / "src" / "finance_app" / "static" / "js" / "xlsx-writer.js").read_text(encoding="utf-8")

    assert "createTableExportXlsxBlob(buildTableExportWorkbookSource(table), sheetName)" in body
    assert "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" in writer
    assert "TableStyleLight1" in writer
    assert 'totalsRowFunction="sum"' in writer
    assert "SUBTOTAL(109," in writer
    assert "${filenameBase}.xlsx" in body
    assert "application/vnd.ms-excel" not in body
    assert "<?mso-application" not in body
    assert "application/vnd.ms-excel" not in writer
    assert "<?mso-application" not in writer


def test_table_export_script_removes_action_columns_from_downloads():
    """Verify shared table exports drop action columns before CSV or Excel generation."""
    body = (PROJECT_ROOT / "src" / "finance_app" / "static" / "js" / "exports.js").read_text(encoding="utf-8")

    assert "tableExportColumnPlan" in body
    assert "ACTION_HEADER_RE" in body
    assert "[data-row-action]" in body
    assert "hasActionBodyCell" in body


def test_table_export_script_splits_multi_value_cells_into_export_columns():
    """Verify shared table exports expand declared cell parts into separate columns."""
    body = (PROJECT_ROOT / "src" / "finance_app" / "static" / "js" / "exports.js").read_text(encoding="utf-8")

    assert "cellExportParts" in body
    assert "[data-export-part]" in body
    assert "exportLabel" in body
    assert "exportHeader" in body
    assert "exportHeaderName" in body
    assert "const headers = tableHeaderNames" in body


def test_recurring_page_explains_filtered_empty_states(owner_client):
    """Verify recurring empty states distinguish filtered views from no detections."""
    response = owner_client.get("/recurring?view=list&statuses=overdue")

    assert response.status_code == 200
    assert_visible_text(response, "No recurring activity matches the current filters.")


def test_recurring_calendar_exposes_empty_state_context(owner_client):
    """Verify an empty recurring calendar explains why no chips are visible."""
    response = owner_client.get("/recurring?view=calendar")

    assert response.status_code == 200
    assert_has_element(response, None, attrs={"data-recurring-ajax-form": True})
    assert_visible_text(
        response,
        "Matched items use the transaction date; unmatched items stay on the expected date.",
        "No recurring activity detected for this month.",
    )


def test_recurring_activity_template_keeps_post_action_state_hooks():
    """Verify recurring list rows keep the client hooks updated after actions."""
    body = (PROJECT_ROOT / "src" / "finance_app" / "templates" / "_recurring_activity.html").read_text(encoding="utf-8")

    assert "data-recurring-user-status" in body
    assert "data-recurring-active" in body
    assert "data-recurring-pattern-key" in body
    assert "data-recurring-row-state" in body
    assert "data-recurring-batch-table" in body
    assert "data-recurring-batch-action" in body
    assert "data-recurring-row-checkbox" in body
    assert "data-recurring-row-confirm" in body
    assert "data-recurring-row-edit" in body
    assert "data-recurring-row-remove" in body


def test_recurring_calendar_template_places_amount_on_its_own_chip_line():
    """Verify recurring calendar chips keep the amount separate from merchant text."""
    body = (PROJECT_ROOT / "src" / "finance_app" / "templates" / "_recurring_calendar.html").read_text(encoding="utf-8")

    assert "recurring-calendar-chip-amount" in body
    assert "data-recurring-pattern-key" in body
    assert "<strong>{{ item.merchant_label }}</strong>" in body
    assert 'title="{{ _(item.status_label) }} - {{ item.merchant }} - {{ item.amount_label }}"' in body


def test_recurring_detail_modal_exposes_decision_summary_hooks(owner_client):
    """Verify recurring details surface status, evidence, and recommendation hooks."""
    response = owner_client.get("/recurring?view=list")

    assert response.status_code == 200
    assert_has_element(response, None, attrs={"data-recurring-detail-status-pill": True})
    assert_has_element(response, None, attrs={"data-recurring-detail-status-detail": True})
    assert_has_element(response, None, attrs={"data-recurring-detail-user-status": True})
    assert_has_element(response, None, attrs={"data-recurring-detail-recommendation": True})
    assert_visible_text(response, "Your decision", "Why detected", "Current-month evidence")


def test_recurring_status_detail_explains_list_statuses():
    """Verify recurring row status details explain why each row needs attention."""
    assert (
        recurring_status_detail(
            {"status": "occurred", "match_details": {}},
            date(2026, 5, 5),
        )
        == "Date and amount matched."
    )
    assert (
        recurring_status_detail(
            {"status": "overdue", "date": "2026-05-01", "match_details": {}},
            date(2026, 5, 5),
        )
        == "4 days overdue"
    )
    assert (
        recurring_status_detail(
            {"status": "possibly_inactive", "match_details": {"missed_cycles": 2}},
            date(2026, 5, 5),
        )
        == "Missed 2 expected cycles."
    )


def test_recurring_empty_state_message_mentions_filters_when_applied(app):
    """Verify recurring empty-state text matches the active filter context."""
    with app.app_context():
        assert recurring_empty_state_message(False) == "No recurring activity detected for this month."
        assert recurring_empty_state_message(True) == "No recurring activity matches the current filters."
        assert (
            recurring_empty_state_message(True, has_account_filter=True)
            == "No recurring activity matches this account."
        )
        assert (
            recurring_empty_state_message(True, has_merchant_filter=True)
            == "No recurring activity matches this merchant."
        )
        assert (
            recurring_empty_state_message(True, has_account_filter=True, has_merchant_filter=True)
            == "No recurring activity matches this account and merchant."
        )


def test_recurring_calendar_days_prioritize_dense_day_attention_items():
    """Verify recurring calendar days expose compact counts and priority ordering."""
    items = [
        recurring_calendar_item("expected", "GYM"),
        recurring_calendar_item("overdue", "HYDRO"),
        recurring_calendar_item("amount_changed", "RENT"),
        recurring_calendar_item("occurred", "NETFLIX"),
    ]

    days = build_recurring_calendar_days(date(2026, 5, 1), items)
    day = next(item for item in days if item["date"] == "2026-05-10")

    assert day["item_count"] == 4
    assert day["attention_count"] == 2
    assert day["more_count"] == 1
    assert [item["status"] for item in day["recurring_items"]] == [
        "overdue",
        "amount_changed",
        "expected",
    ]
    assert day["all_recurring_items"][0]["status_detail"] == "Needs payment."
    assert day["all_recurring_items"][0]["category"] == "Utilities"
    assert day["all_recurring_items"][0]["pattern_key"] == "HYDRO::spending"
    assert day["all_recurring_items"][0]["user_status"] == "detected"
    assert day["all_recurring_items"][0]["active"] == 1


def test_recurring_calendar_days_truncate_long_chip_merchants():
    """Verify calendar chip labels stay compact while full merchant details remain available."""
    merchant = "COSTCO WHOLESALE W527 MONTREAL"
    days = build_recurring_calendar_days(
        date(2026, 5, 1),
        [recurring_calendar_item("expected", merchant)],
    )
    chip = next(item for item in days if item["date"] == "2026-05-10")["recurring_items"][0]

    assert chip["merchant"] == merchant
    assert chip["merchant_label"] == "COSTCO WHOLESALE W52..."
    assert len(chip["merchant_label"]) == 23
    assert merchant in chip["aria_label"]


def test_recurring_activity_json_exposes_detail_modal_status_context():
    """Verify recurring detail JSON includes status labels and explanations."""
    item = recurring_calendar_item("overdue", "HYDRO")

    payload = build_recurring_activity_json([item])[item["id"]]

    assert payload["statusLabel"] == "Overdue"
    assert payload["statusDetail"] == "Needs payment."
    assert payload["matchDetails"] == {}


def recurring_calendar_item(status, merchant):
    """Build a recurring item fixture for recurring calendar day tests."""
    return {
        "id": f"{status}-{merchant}",
        "pattern_key": f"{merchant}::spending",
        "merchant_id": None,
        "match_type": "keyword",
        "merchant": merchant,
        "category": "Utilities",
        "type": "spending",
        "frequency": "Monthly-like",
        "amount": 42.0,
        "date": "2026-05-10",
        "last_seen": "2026-04-10",
        "observed_months": 4,
        "status": status,
        "status_label": status.replace("_", " ").title(),
        "status_detail": "Needs payment.",
        "confidence": "High",
        "user_status": "detected",
        "active": 1,
        "amount_change": None,
        "match_details": {},
        "occurrences": [],
    }


def test_recurring_pattern_payload_and_expected_day_normalization():
    """Verify recurring form payload normalization and day parsing."""
    assert recurring_pattern_payload(valid_payload(type="income")) == {
        "pattern_key": "NETFLIX::income",
        "merchant_id": None,
        "merchant": "NETFLIX",
        "match_type": "keyword",
        "type": "income",
    }
    with pytest.raises(ValueError, match="payload is incomplete"):
        recurring_pattern_payload({"patternKey": "bad", "merchant": "NETFLIX", "type": "neutral"})

    assert parse_expected_day("2026-05-31") == 31
    assert parse_expected_day("15") == 15
    assert parse_expected_day("0") is None
    assert parse_expected_day("2026-02-31") is None


def test_recurring_pattern_normalizers():
    """Verify recurring metadata normalizers constrain user-editable values."""
    assert recurring_pattern_key(" Netflix ", " spending ") == "Netflix::spending"
    assert normalize_user_status("CONFIRMED") == "confirmed"
    assert normalize_user_status("stale") == "detected"
    assert normalize_frequency("Weekly") == "Weekly"
    assert normalize_frequency("Every 10 days") is None
    assert normalize_active("inactive") == 0
    assert normalize_active("false") == 0
    assert normalize_active("yes") == 1
    assert normalize_optional_int("12", minimum=1, maximum=31) == 12
    assert normalize_optional_int("32", minimum=1, maximum=31) is None
    assert normalize_optional_money("12.345", minimum=0) == Decimal("12.35")
    assert normalize_optional_money("-1", minimum=0) is None


def test_upsert_recurring_pattern_preserves_existing_values_when_not_overridden(core_conn):
    """Verify recurring pattern upserts update only explicit metadata values."""
    upsert_recurring_pattern(
        core_conn,
        "GYM::spending",
        "GYM",
        "spending",
        merchant_id=get_or_create_merchant_for_name(core_conn, "GYM")["id"],
        user_status="edited",
        frequency="Monthly-like",
        expected_day=5,
        typical_amount=49.99,
        date_tolerance_days=2,
        amount_tolerance=3.5,
        active=1,
    )
    core_conn.commit()
    merchant_id = (
        core_conn.execute(text("SELECT id FROM merchants WHERE merchant_key = 'GYM'")).fetchone()._mapping["id"]
    )

    upsert_recurring_pattern(
        core_conn,
        f"merchant:{merchant_id}::spending",
        "GYM",
        "spending",
        merchant_id=merchant_id,
        user_status="confirmed",
    )
    core_conn.commit()

    pattern = get_recurring_pattern_by_merchant_type(core_conn, merchant_id, "spending")
    metadata = get_recurring_pattern_metadata(core_conn)
    assert pattern["merchant"] == "GYM"
    assert pattern["user_status"] == "confirmed"
    assert pattern["frequency"] == "Monthly-like"
    assert pattern["expected_day"] == 5
    assert pattern["typical_amount"] == 49.99
    assert pattern["date_tolerance_days"] == 2
    assert pattern["amount_tolerance"] == 3.5
    assert pattern["active"] == 1
    assert metadata[f"merchant:{merchant_id}::spending"]["merchant"] == "GYM"
