"""Tests for recurring pattern routes and persistence helpers."""

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import text
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
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert 'aria-label="Status filter"' in body
    assert 'class="recurring-tabs page-tabs nav nav-tabs mb-4"' in body
    assert 'id="recurring-list-tab"' in body
    assert 'role="tab"' in body
    assert 'href="/recurring?month=' in body
    assert "view=list" in body
    assert 'id="recurring-calendar-tab"' in body
    assert "view=calendar" in body
    assert 'aria-selected="true"' in body
    assert 'data-recurring-status-filter="overdue"' in body
    assert "data-recurring-ajax-link" in body
    assert 'name="statuses" value="overdue"' in body
    assert 'name="account_id" value="12"' in body
    assert 'name="merchant_id" value="34"' in body
    assert 'name="merchant_query" value="NETFLIX"' in body
    assert "account_id=12" in body
    assert "merchant_id=34" in body
    assert "merchant_query=NETFLIX" in body
    assert "Merchant: NETFLIX" in body
    assert 'aria-pressed="true"' in body
    assert 'id="recurring-status"' not in body
    assert "data-recurring-activity-filter" not in body


def test_recurring_page_exposes_compact_table_and_export_status_details(owner_client):
    """Verify recurring list and export columns expose compact status context."""
    response = owner_client.get("/recurring?view=list")
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "data-recurring-dynamic" in body
    assert "recurring-summary-layout" in body
    assert "recurring-metric-carousel" in body
    assert 'id="recurring-month"' in body
    assert "data-flatpickr-month" in body
    assert "data-flatpickr-submit-on-change" in body
    assert "Repeating merchants detected for the selected month." in body
    assert "No recurring activity detected for this month." in body
    assert "Confidence level: High" in body
    assert '<option value="High" selected>High</option>' in body
    assert 'data-sort-column="8" data-sort-type="number"' in body
    assert "data-paginated-table" in body
    assert 'data-pagination-label="Recurring activity pages"' in body
    assert 'data-export-visible-source="#recurring-activity-table"' in body
    assert 'data-export-excel-extension="xlsx"' not in body
    assert "data-recurring-batch-table" in body
    assert "data-all-recurring-ids" in body
    assert "data-recurring-select-all" in body
    assert "Confirm selected" in body
    assert "Remove selected" in body
    assert 'colspan="10"' in body
    assert "Status detail" in body
    assert "Matched date" in body
    assert "Actual amount" in body


def test_recurring_page_all_confidence_filter_is_explicit(owner_client):
    """Verify All confidence is opt-in now that High confidence is the default."""
    response = owner_client.get("/recurring?view=list&confidence=all")
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Confidence level: All confidence" in body
    assert '<option value="all" selected>All confidence</option>' in body
    assert 'name="confidence" value="all"' in body
    assert "confidence=all" in body
    assert "No recurring activity matches the current filters." in body


def test_table_export_script_prompts_for_displayed_or_entire_table():
    """Verify shared table export asks for row scope only when pages exist."""
    body = (PROJECT_ROOT / "src" / "finance_app" / "static" / "js" / "exports.js").read_text(encoding="utf-8")

    assert "tableHasMultipleExportPages" in body
    assert "tableExportScope(table)" in body
    assert 'return "all"' in body
    assert "chooseTableExportScope" in body
    assert "Displayed rows" in body
    assert "Entire table" in body
    assert "Export rows" in body


def test_table_export_script_fetches_all_server_pages_for_entire_table():
    """Verify entire-table exports can combine server-rendered pagination pages."""
    body = (PROJECT_ROOT / "src" / "finance_app" / "static" / "js" / "exports.js").read_text(encoding="utf-8")

    assert "serverPaginationPlan" in body
    assert "numericPaginationLinks" in body
    assert "inferPaginationPageParameter" in body
    assert "fetchExportTablePage" in body
    assert "DOMParser" in body
    assert "tableExportTablesForScope" in body
    assert "tableRowsForExportTables" in body
    assert "transactions" not in body
    assert "dashboard" not in body
    assert "taxonomy" not in body


def test_table_export_script_builds_real_xlsx_tables_with_totals():
    """Verify shared Excel exports build real workbooks with typed table totals."""
    body = (PROJECT_ROOT / "src" / "finance_app" / "static" / "js" / "exports.js").read_text(encoding="utf-8")

    assert "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" in body
    assert "TableStyleLight1" in body
    assert 'totalsRowFunction="sum"' in body
    assert "SUBTOTAL(109," in body
    assert "${filenameBase}.xlsx" in body
    assert "application/vnd.ms-excel" not in body
    assert "<?mso-application" not in body


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
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "No recurring activity matches the current filters." in body


def test_recurring_calendar_exposes_empty_state_context(owner_client):
    """Verify an empty recurring calendar explains why no chips are visible."""
    response = owner_client.get("/recurring?view=calendar")
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "data-recurring-ajax-form" in body
    assert "Matched items use the transaction date; unmatched items stay on the expected date." in body
    assert "No recurring activity detected for this month." in body


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
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "data-recurring-detail-status-pill" in body
    assert "data-recurring-detail-status-detail" in body
    assert "data-recurring-detail-user-status" in body
    assert "data-recurring-detail-recommendation" in body
    assert "Your decision" in body
    assert "Why detected" in body
    assert "Current-month evidence" in body


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
