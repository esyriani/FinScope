"""Route tests for the rules feature."""

import csv
import io

from sqlalchemy import text
from tests.support.database import insert_merchant, insert_rule
from tests.support.html import (
    assert_form,
    assert_has_element,
    assert_input,
    assert_link,
    assert_no_asset_reference,
    assert_no_element,
    assert_not_visible_text,
    assert_option,
    assert_visible_text,
)
from tests.support.jobs import capture_background_jobs
from tests.support.rules import rule_by_id
from tests.support.web import set_csrf_token

from finance_app.core.csrf import CSRF_FIELD_NAME
from finance_app.core.filters import format_datetime
from finance_app.modules.categories.tag_filters import UNTAGGED_TAG_FILTER
from finance_app.modules.categories.taxonomy import get_rule_tags_by_rule_id, set_rule_tags
from finance_app.modules.rules import workflow as rules_workflow
from finance_app.modules.rules.import_export import import_rules_job, undo_import_rules_job


def test_rules_create_route_persists_rule_and_tags(owner_client, core_conn):
    """Verify that the create route stores a manual rule."""
    response = owner_client.post(
        "/rules/create",
        data={
            CSRF_FIELD_NAME: set_csrf_token(owner_client),
            "confirm_preview": "1",
            "keyword": "Metro Grocery",
            "category": "Food",
            "tags": ["Tax"],
            "amount_min": "10",
            "amount_max": "20",
        },
        follow_redirects=True,
    )

    rule = core_conn.execute(text("""
        SELECT id, keyword, category, amount_min, amount_max, source
        FROM category_rules
        WHERE keyword = 'METRO GROCERY'
        """)).fetchone()
    assert response.status_code == 200
    assert_visible_text(response, "Rule saved for: METRO GROCERY")
    assert_visible_text(response, "Historical transactions were not changed.", "Review apply", "Rule detail")
    assert tuple(rule[1:]) == ("METRO GROCERY", "Food", 10.0, 20.0, "manual")
    assert get_rule_tags_by_rule_id(core_conn, [rule._mapping["id"]])[rule._mapping["id"]] == ["Tax"]


def test_rules_create_route_allows_direct_save_without_preview_confirmation(owner_client, core_conn):
    """Verify direct rule creation stores the rule without audit preview."""
    response = owner_client.post(
        "/rules/create",
        data={
            CSRF_FIELD_NAME: set_csrf_token(owner_client),
            "keyword": "Metro Grocery",
            "category": "Food",
        },
        follow_redirects=True,
    )
    rule = core_conn.execute(text("SELECT id FROM category_rules WHERE keyword = 'METRO GROCERY'")).fetchone()

    assert response.status_code == 200
    assert_visible_text(response, "Rule saved for: METRO GROCERY", "Historical transactions were not changed.")
    assert rule is not None


def test_rules_route_renders_automatic_source_badge(owner_client, core_conn):
    """Verify that automatic rules show the automatic source badge."""
    rule_id = insert_rule(core_conn, keyword="METRO GROCERY", category="Food", source="automatic")

    response = owner_client.get("/rules")

    assert response.status_code == 200
    assert_has_element(response, "span", attrs={"class": "text-bg-info"}, text="Auto")
    assert_visible_text(response, "Automatic", "Suggested", "Approve")
    assert_not_visible_text(response, "Preview approve")
    assert_form(response, f"/rules/{rule_id}/approve")


def test_rules_route_formats_created_timestamp(owner_client, core_conn):
    """Verify that the rules table uses the shared timestamp display format."""
    created_at = "2026-05-13T03:38:00Z"
    rule_id = insert_rule(core_conn, keyword="TIMESTAMP RULE", category="Food")
    core_conn.execute(
        text("UPDATE category_rules SET created_at = :p0 WHERE id = :p1"), {"p0": created_at, "p1": rule_id}
    )
    core_conn.commit()

    response = owner_client.get("/rules")
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert format_datetime(created_at) in body
    assert created_at not in body


def test_rules_route_links_to_rule_audit(owner_client, core_conn):
    """Verify the rules page links bulk actions through the audit page."""
    insert_rule(core_conn, keyword="EXPORT PAGE", category="Food")

    response = owner_client.get("/rules")

    assert response.status_code == 200
    assert_link(response, "/rules/audit", text="Rule health check")
    assert_link(response, "/rules/export.csv", text="Export rules")
    assert_form(response, "/rules/audit/preview")
    assert_has_element(response, "a", attrs={"data-busy-message": "Loading rule health check..."})
    assert_has_element(response, "form", attrs={"data-busy-message": "Preparing rule health check preview..."})
    assert_has_element(response, "table", attrs={"data-rules-table": True, "data-no-export": True})
    assert_no_asset_reference(response, "js/exports.js")
    assert_no_asset_reference(response, "css/exports.css")
    assert_form(response, "/rules/create")
    assert_input(response, name="action", value="apply_all_rules")
    assert_input(response, name="action", value="create_rule")
    assert_visible_text(response, "Preview apply all", "Preview import", "Review impact", "Save rule")


def test_rules_route_uses_direct_delete_for_unapplied_rules(owner_client, core_conn):
    """Verify unapplied rules show a direct delete confirmation."""
    rule_id = insert_rule(core_conn, keyword="UNUSED STORE", category="Food")

    response = owner_client.get("/rules")
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert f'action="/rules/{rule_id}/delete"' in body
    assert "This rule is not applied to any transactions." in body


def test_rules_route_keeps_delete_preview_for_applied_rules(owner_client, core_conn):
    """Verify applied rules keep the delete preview action."""
    rule_id = insert_rule(core_conn, keyword="APPLIED STORE", category="Food")
    core_conn.execute(
        text("""
        INSERT INTO transactions (
            tx_date, description, amount, category, category_source,
            category_rule_id, needs_review, fingerprint
        )
        VALUES (
            '2026-01-02', 'Applied Store', 12.34, 'Food', 'rule',
            :p0, 0, 'rules-page-applied-delete'
        )
        """),
        {"p0": rule_id},
    )
    core_conn.commit()

    response = owner_client.get("/rules")
    body = response.get_data(as_text=True)
    delete_modal = body.split(f'id="delete-rule-{rule_id}"', 1)[1]

    assert response.status_code == 200
    assert 'action="/rules/audit/preview"' in delete_modal
    assert 'name="action" value="delete_rule"' in delete_modal
    assert "Preview delete" in delete_modal


def test_rules_route_renders_scope_selector_for_merchant_bound_rule(owner_client, core_conn):
    """Verify merchant-bound rules expose a control for switching to fuzzy scope."""
    merchant_id = insert_merchant(core_conn, "COSTCO RENEWAL")
    insert_rule(core_conn, keyword="COSTCO RENEWAL", category="Food", merchant_id=merchant_id)

    response = owner_client.get("/rules")

    assert response.status_code == 200
    assert_visible_text(response, "Match scope", "Merchant only", "Approximate keyword", "COSTCO RENEWAL")


def test_rules_modals_render_category_and_tag_description_tooltips(owner_client):
    """Verify rule editor category and tag choices expose taxonomy descriptions."""
    response = owner_client.get("/rules")

    assert response.status_code == 200
    assert_has_element(response, "select", attrs={"data-category-description-select": True})
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


def test_rules_route_filters_suggested_automatic_rules(owner_client, core_conn):
    """Verify the Suggested filter isolates automatic rules that still need approval."""
    insert_rule(core_conn, keyword="AUTO SUGGESTED", category="Food", source="automatic", ai_approved=0)
    insert_rule(core_conn, keyword="AUTO APPROVED", category="Food", source="automatic", ai_approved=1)
    insert_rule(core_conn, keyword="MANUAL RULE", category="Food", source="manual", ai_approved=0)

    response = owner_client.get("/rules?approval=suggested")

    assert response.status_code == 200
    assert_visible_text(response, "AUTO SUGGESTED")
    assert_not_visible_text(response, "AUTO APPROVED", "MANUAL RULE")
    assert_option(response, value="suggested", selected=True)


def test_rules_route_filters_approved_automatic_rules(owner_client, core_conn):
    """Verify the Approved filter isolates approved automatic rules."""
    insert_rule(core_conn, keyword="AUTO SUGGESTED", category="Food", source="automatic", ai_approved=0)
    insert_rule(core_conn, keyword="AUTO APPROVED", category="Food", source="automatic", ai_approved=1)
    insert_rule(core_conn, keyword="MANUAL RULE", category="Food", source="manual", ai_approved=0)

    response = owner_client.get("/rules?approval=approved")

    assert response.status_code == 200
    assert_visible_text(response, "AUTO APPROVED")
    assert_not_visible_text(response, "AUTO SUGGESTED", "MANUAL RULE")
    assert_option(response, value="approved", selected=True)


def test_rules_route_filters_by_tags(owner_client, core_conn):
    """Verify the rules page can be filtered by attached rule tags."""
    tax_rule_id = insert_rule(core_conn, keyword="METRO TAX", category="Food")
    shared_rule_id = insert_rule(core_conn, keyword="CAFE SHARED", category="Food")
    set_rule_tags(core_conn, tax_rule_id, ["Tax"])
    set_rule_tags(core_conn, shared_rule_id, ["Shared"])
    core_conn.commit()

    response = owner_client.get("/rules?tags=Tax")

    assert response.status_code == 200
    assert_visible_text(response, "METRO TAX")
    assert_not_visible_text(response, "CAFE SHARED")


def test_rules_route_filters_by_untagged_rules(owner_client, core_conn):
    """Verify the virtual untagged tag filter finds rules without tags."""
    insert_rule(core_conn, keyword="NO TAG RULE", category="Food")
    tagged_rule_id = insert_rule(core_conn, keyword="WITH TAX RULE", category="Food")
    set_rule_tags(core_conn, tagged_rule_id, ["Tax"])
    core_conn.commit()

    response = owner_client.get(f"/rules?tags={UNTAGGED_TAG_FILTER}")

    assert response.status_code == 200
    assert_visible_text(response, "NO TAG RULE", "Untagged")
    assert_not_visible_text(response, "WITH TAX RULE")


def test_rules_route_filters_by_category(owner_client, core_conn):
    """Verify the rules page can be filtered by category."""
    insert_rule(core_conn, keyword="METRO FOOD", category="Food")
    insert_rule(core_conn, keyword="HYDRO UTILITIES", category="Utilities")

    response = owner_client.get("/rules?categories=Utilities")

    assert response.status_code == 200
    assert_visible_text(response, "HYDRO UTILITIES")
    assert_not_visible_text(response, "METRO FOOD")
    assert_input(response, name="categories", value="Utilities", checked=True)


def test_rules_route_filters_by_source(owner_client, core_conn):
    """Verify the rules page can be filtered by rule source."""
    insert_rule(core_conn, keyword="AUTO RULE", category="Food", source="automatic")
    insert_rule(core_conn, keyword="MANUAL RULE", category="Food", source="manual")

    response = owner_client.get("/rules?source=automatic")

    assert response.status_code == 200
    assert_visible_text(response, "AUTO RULE", "Manual", "Automatic")
    assert_not_visible_text(response, "MANUAL RULE")
    assert_option(response, value="automatic", selected=True)
    assert_no_element(response, "option", text="Import")
    assert_no_element(response, "option", text="System")


def test_rules_create_route_rejects_invalid_form(owner_client, core_conn):
    """Verify that the create route flashes validation errors without writing."""
    response = owner_client.post(
        "/rules/create",
        data={
            CSRF_FIELD_NAME: set_csrf_token(owner_client),
            "confirm_preview": "1",
            "keyword": "",
            "category": "Food",
        },
        follow_redirects=True,
    )

    count = core_conn.execute(text("SELECT COUNT(*) AS count FROM category_rules")).fetchone()._mapping["count"]
    assert response.status_code == 200
    assert_visible_text(response, "Keyword and category are required.")
    assert count == 0


def test_rules_update_route_replaces_rule_values_and_tags(owner_client, core_conn):
    """Verify that the update route changes rule fields and associated tags."""
    rule_id = insert_rule(core_conn, keyword="EXISTING STORE", category="Food")

    response = owner_client.post(
        f"/rules/{rule_id}/update",
        data={
            CSRF_FIELD_NAME: set_csrf_token(owner_client),
            "confirm_preview": "1",
            "keyword": "Hydro Quebec",
            "category": "Utilities",
            "tags": ["Government", "Tax"],
            "amount_min": "25",
            "amount_max": "50",
        },
        follow_redirects=True,
    )

    rule = rule_by_id(core_conn, rule_id)
    assert response.status_code == 200
    assert_visible_text(response, "Rule updated.")
    assert_visible_text(response, "Historical transactions were not changed.", "Review apply", "Rule detail")
    assert tuple(rule[1:]) == ("HYDRO QUEBEC", "Utilities", 25.0, 50.0, "manual", 0)
    assert get_rule_tags_by_rule_id(core_conn, [rule_id])[rule_id] == ["Government", "Tax"]


def test_rules_update_route_allows_direct_save_without_preview_confirmation(owner_client, core_conn):
    """Verify direct rule updates save without audit preview."""
    rule_id = insert_rule(core_conn, keyword="EXISTING STORE", category="Food")

    response = owner_client.post(
        f"/rules/{rule_id}/update",
        data={
            CSRF_FIELD_NAME: set_csrf_token(owner_client),
            "keyword": "Hydro Quebec",
            "category": "Utilities",
        },
        follow_redirects=True,
    )

    rule = rule_by_id(core_conn, rule_id)
    assert response.status_code == 200
    assert_visible_text(response, "Rule updated.", "Historical transactions were not changed.")
    assert tuple(rule[1:]) == ("HYDRO QUEBEC", "Utilities", None, None, "manual", 0)


def test_rules_update_route_can_change_merchant_bound_rule_to_fuzzy(owner_client, core_conn):
    """Verify posting an empty merchant scope clears merchant-bound matching."""
    merchant_id = insert_merchant(core_conn, "COSTCO RENEWAL")
    rule_id = insert_rule(core_conn, keyword="COSTCO RENEWAL", category="Food", merchant_id=merchant_id)

    response = owner_client.post(
        f"/rules/{rule_id}/update",
        data={
            CSRF_FIELD_NAME: set_csrf_token(owner_client),
            "confirm_preview": "1",
            "keyword": "Costco Renewal",
            "merchant_id": "",
            "category": "Food",
        },
        follow_redirects=True,
    )

    rule = core_conn.execute(
        text("""
        SELECT merchant_id, keyword, category
        FROM category_rules
        WHERE id = :p0
        """),
        {"p0": rule_id},
    ).fetchone()
    assert response.status_code == 200
    assert_visible_text(response, "Rule updated.")
    assert tuple(rule) == (None, "COSTCO RENEWAL", "Food")


def test_rules_update_route_approves_automatic_rule_without_changing_source(owner_client, core_conn):
    """Verify editing an automatic rule preserves provenance and marks it approved."""
    rule_id = insert_rule(core_conn, keyword="AUTO STORE", category="Food", source="automatic")

    response = owner_client.post(
        f"/rules/{rule_id}/update",
        data={
            CSRF_FIELD_NAME: set_csrf_token(owner_client),
            "confirm_preview": "1",
            "keyword": "Auto Store",
            "category": "Utilities",
        },
        follow_redirects=True,
    )

    rule = rule_by_id(core_conn, rule_id)
    assert response.status_code == 200
    assert_visible_text(response, "Rule updated.")
    assert tuple(rule[1:]) == ("AUTO STORE", "Utilities", None, None, "automatic", 1)


def test_rules_approve_route_does_not_require_preview_confirmation(owner_client, core_conn):
    """Verify direct approval is allowed because it only changes rule metadata."""
    rule_id = insert_rule(core_conn, keyword="AUTO STORE", category="Food", source="automatic")

    response = owner_client.post(
        f"/rules/{rule_id}/approve",
        data={CSRF_FIELD_NAME: set_csrf_token(owner_client)},
        follow_redirects=True,
    )

    rule = rule_by_id(core_conn, rule_id)
    assert response.status_code == 200
    assert_visible_text(response, "Rule approved: AUTO STORE")
    assert rule._mapping["source"] == "automatic"
    assert rule._mapping["ai_approved"] == 1


def test_rules_approve_route_returns_json_for_table_action(owner_client, core_conn):
    """Verify AJAX approval returns a payload without redirecting."""
    rule_id = insert_rule(core_conn, keyword="AUTO STORE", category="Food", source="automatic")

    response = owner_client.post(
        f"/rules/{rule_id}/approve",
        data={
            CSRF_FIELD_NAME: set_csrf_token(owner_client),
        },
        headers={"X-Requested-With": "fetch"},
    )

    payload = response.get_json()
    assert response.status_code == 200
    assert payload["ok"] is True
    assert payload["action"] == "approve"
    assert payload["rule_id"] == rule_id
    assert payload["approval_label"] == "Approved"
    assert rule_by_id(core_conn, rule_id)._mapping["ai_approved"] == 1


def test_rules_approve_route_rejects_manual_rule(owner_client, core_conn):
    """Verify approval is limited to automatic rules."""
    rule_id = insert_rule(core_conn, keyword="MANUAL STORE", category="Food")

    response = owner_client.post(
        f"/rules/{rule_id}/approve",
        data={
            CSRF_FIELD_NAME: set_csrf_token(owner_client),
        },
        follow_redirects=True,
    )

    rule = rule_by_id(core_conn, rule_id)
    assert response.status_code == 200
    assert_visible_text(response, "Only automatic rules can be approved.")
    assert rule._mapping["source"] == "manual"
    assert rule._mapping["ai_approved"] == 0


def test_rules_apply_route_returns_json_for_table_action(owner_client, core_conn):
    """Verify confirmed AJAX apply returns updated counts without refreshing the page."""
    rule_id = insert_rule(core_conn, keyword="METRO", category="Food")
    core_conn.execute(text("""
        INSERT INTO transactions (tx_date, description, amount, category, needs_review, fingerprint)
        VALUES ('2026-01-02', 'Metro Grocery #123', 14.25, 'UNKNOWN', 1, 'ajax-apply-match')
        """))
    core_conn.commit()

    response = owner_client.post(
        f"/rules/{rule_id}/apply",
        data={
            CSRF_FIELD_NAME: set_csrf_token(owner_client),
            "confirm_preview": "1",
            "mode": "apply_where_wins",
        },
        headers={"X-Requested-With": "fetch"},
    )

    payload = response.get_json()
    assert response.status_code == 200
    assert payload["ok"] is True
    assert payload["action"] == "apply"
    assert payload["rule_id"] == rule_id
    assert payload["mode"] == "apply_where_wins"
    assert payload["updated_count"] == 1


def test_rules_apply_route_rejects_unconfirmed_json_apply(owner_client, core_conn):
    """Verify AJAX apply also requires preview confirmation."""
    rule_id = insert_rule(core_conn, keyword="METRO", category="Food")

    response = owner_client.post(
        f"/rules/{rule_id}/apply",
        data={CSRF_FIELD_NAME: set_csrf_token(owner_client)},
        headers={"X-Requested-With": "fetch"},
    )

    assert response.status_code == 400
    assert response.get_json() == {"ok": False, "message": "Preview apply before applying a rule."}


def test_rules_preview_route_returns_match_count_and_sample(owner_client, core_conn):
    """Verify that preview returns matching transactions without mutating data."""
    core_conn.execute(text("""
        INSERT INTO transactions (tx_date, description, amount, category, needs_review, fingerprint)
        VALUES ('2026-01-02', 'Metro Grocery #123', 14.25, 'UNKNOWN', 1, 'preview-match')
        """))
    core_conn.execute(text("""
        INSERT INTO transactions (tx_date, description, amount, category, needs_review, fingerprint)
        VALUES ('2026-01-03', 'Other Store', 14.25, 'UNKNOWN', 1, 'preview-miss')
        """))
    core_conn.commit()

    response = owner_client.post(
        "/rules/preview",
        data={
            CSRF_FIELD_NAME: set_csrf_token(owner_client),
            "keyword": "Metro",
            "category": "Food",
            "amount_min": "10",
            "amount_max": "20",
        },
    )

    payload = response.get_json()
    assert response.status_code == 200
    assert payload["ok"] is True
    assert payload["keyword"] == "METRO"
    assert payload["category"] == "Food"
    assert payload["match_count"] == 1
    assert payload["transactions"][0]["description"] == "Metro Grocery #123"


def test_rules_preview_route_returns_validation_error_json(owner_client):
    """Verify that invalid previews return JSON validation errors."""
    response = owner_client.post(
        "/rules/preview",
        data={
            CSRF_FIELD_NAME: set_csrf_token(owner_client),
            "keyword": "",
            "category": "Food",
        },
    )

    assert response.status_code == 400
    assert response.get_json() == {
        "ok": False,
        "message": "Enter a keyword and category to preview matching transactions.",
        "match_count": 0,
        "transactions": [],
    }


def test_rules_delete_route_removes_unapplied_rule_without_preview(owner_client, core_conn):
    """Verify direct rule deletion is allowed when no transaction references it."""
    rule_id = insert_rule(core_conn)

    response = owner_client.post(
        f"/rules/{rule_id}/delete",
        data={CSRF_FIELD_NAME: set_csrf_token(owner_client)},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert_visible_text(response, "Rule deleted.")
    assert rule_by_id(core_conn, rule_id) is None


def test_rules_delete_route_requires_preview_when_rule_is_applied(owner_client, core_conn):
    """Verify direct deletion is blocked when a transaction references the rule."""
    rule_id = insert_rule(core_conn)
    core_conn.execute(
        text("""
        INSERT INTO transactions (
            tx_date, description, amount, category, category_source,
            category_rule_id, needs_review, fingerprint
        )
        VALUES (
            '2026-01-02', 'Metro Grocery', 12.34, 'Food', 'rule',
            :p0, 0, 'delete-route-applied'
        )
        """),
        {"p0": rule_id},
    )
    core_conn.commit()

    response = owner_client.post(
        f"/rules/{rule_id}/delete",
        data={CSRF_FIELD_NAME: set_csrf_token(owner_client)},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert_visible_text(response, "Preview deletion before deleting a rule.")
    assert rule_by_id(core_conn, rule_id) is not None


def test_rules_delete_route_removes_rule_after_preview_confirmation(owner_client, core_conn):
    """Verify confirmed deletion removes an existing category rule."""
    rule_id = insert_rule(core_conn)

    response = owner_client.post(
        f"/rules/{rule_id}/delete",
        data={
            CSRF_FIELD_NAME: set_csrf_token(owner_client),
            "confirm_preview": "1",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert_visible_text(response, "Rule deleted.")
    assert rule_by_id(core_conn, rule_id) is None


def test_rules_delete_route_returns_json_for_unapplied_delete(owner_client, core_conn):
    """Verify AJAX deletion succeeds without preview for unapplied rules."""
    rule_id = insert_rule(core_conn)

    response = owner_client.post(
        f"/rules/{rule_id}/delete",
        data={CSRF_FIELD_NAME: set_csrf_token(owner_client)},
        headers={"X-Requested-With": "fetch"},
    )

    payload = response.get_json()
    assert response.status_code == 200
    assert payload == {
        "ok": True,
        "action": "delete",
        "rule_id": rule_id,
        "message": "Rule deleted.",
    }
    assert rule_by_id(core_conn, rule_id) is None


def test_rules_delete_route_rejects_unconfirmed_json_delete_when_applied(owner_client, core_conn):
    """Verify AJAX deletion still requires preview for applied rules."""
    rule_id = insert_rule(core_conn)
    core_conn.execute(
        text("""
        INSERT INTO transactions (
            tx_date, description, amount, category, category_source,
            category_rule_id, needs_review, fingerprint
        )
        VALUES (
            '2026-01-02', 'Metro Grocery', 12.34, 'Food', 'rule',
            :p0, 0, 'delete-route-ajax-applied'
        )
        """),
        {"p0": rule_id},
    )
    core_conn.commit()

    response = owner_client.post(
        f"/rules/{rule_id}/delete",
        data={CSRF_FIELD_NAME: set_csrf_token(owner_client)},
        headers={"X-Requested-With": "fetch"},
    )

    payload = response.get_json()
    assert response.status_code == 400
    assert payload == {"ok": False, "message": "Preview deletion before deleting a rule."}
    assert rule_by_id(core_conn, rule_id) is not None


def test_rules_export_route_returns_csv(owner_client, core_conn):
    """Verify that rule exports return CSV content with persisted rules."""
    insert_rule(core_conn, keyword="HYDRO QUEBEC", category="Utilities", amount_min=25, amount_max=50)

    response = owner_client.get("/rules/export.csv")
    rows = list(csv.DictReader(io.StringIO(response.get_data(as_text=True))))

    assert response.status_code == 200
    assert response.mimetype == "text/csv"
    assert "category-rules.csv" in response.headers["Content-Disposition"]
    assert rows[0]["keyword"] == "HYDRO QUEBEC"
    assert rows[0]["merchant_name"] == ""
    assert rows[0]["category"] == "Utilities"
    assert rows[0]["amount_min"] == "25.0"
    assert rows[0]["amount_max"] == "50.0"


def test_rules_import_route_rejects_invalid_mode_missing_file_and_bad_file_type(owner_client):
    """Verify import route validation before background job submission."""
    token = set_csrf_token(owner_client)

    invalid_mode = owner_client.post(
        "/rules/import",
        data={
            CSRF_FIELD_NAME: token,
            "mode": "replace-everything",
            "rules_file": (io.BytesIO(b"keyword,category\nMetro,Food\n"), "rules.csv"),
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    missing_file = owner_client.post(
        "/rules/import",
        data={CSRF_FIELD_NAME: token, "mode": "add"},
        follow_redirects=True,
    )
    wrong_type = owner_client.post(
        "/rules/import",
        data={
            CSRF_FIELD_NAME: token,
            "mode": "add",
            "rules_file": (io.BytesIO(b"keyword,category\nMetro,Food\n"), "rules.txt"),
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    empty_file = owner_client.post(
        "/rules/import",
        data={
            CSRF_FIELD_NAME: token,
            "mode": "add",
            "rules_file": (io.BytesIO(b"  \n"), "rules.csv"),
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )

    assert_visible_text(invalid_mode, "Choose whether to add new rules or override existing rules.")
    assert_visible_text(missing_file, "Choose a CSV file to import.")
    assert_visible_text(wrong_type, "Rules import currently supports CSV files.")
    assert_visible_text(empty_file, "The selected rules file is empty.")


def test_rules_import_route_previews_then_queues_background_job(owner_client, monkeypatch):
    """Verify that valid imports require preview confirmation before queueing."""
    submitted_jobs = capture_background_jobs(monkeypatch, rules_workflow, job_id="rulesjob123")

    preview = owner_client.post(
        "/rules/import",
        data={
            CSRF_FIELD_NAME: set_csrf_token(owner_client),
            "mode": "add",
            "rules_file": (io.BytesIO(b"keyword,category\nMetro,Food\n"), "rules.csv"),
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )

    assert preview.status_code == 200
    assert_visible_text(preview, "Rule import preview", "Confirm import", "METRO")
    assert_input(preview, name="confirm_preview", value="1")
    assert_has_element(
        preview,
        None,
        attrs={
            "data-paginated-table": True,
            "data-pagination-label": "Imported rule pages",
        },
    )
    assert_has_element(preview, None, attrs={"data-sort-column": "0", "data-sort-type": "text"})
    assert len(submitted_jobs) == 0

    response = owner_client.post(
        "/rules/import",
        data={
            CSRF_FIELD_NAME: set_csrf_token(owner_client),
            "confirm_preview": "1",
            "mode": "add",
            "filename": "rules.csv",
            "raw_text": "keyword,category\nMetro,Food\n",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert_visible_text(response, "Rules import queued in the background.")
    assert len(submitted_jobs) == 1
    submitted = submitted_jobs.single()
    assert submitted.label == "Import rules from rules.csv"
    assert submitted.func is import_rules_job
    assert submitted.args[0] == "keyword,category\nMetro,Food\n"
    assert submitted.args[1] == "add"
    assert isinstance(submitted.args[2], dict)
    assert submitted.undo_handler is undo_import_rules_job
    assert submitted.undo_args == (submitted.args[2],)


def test_rules_import_route_rejects_malformed_csv_before_queueing(owner_client, monkeypatch):
    """Verify malformed CSV payloads fail during import preview."""
    submitted_jobs = capture_background_jobs(monkeypatch, rules_workflow, job_id="badcsvjob")

    response = owner_client.post(
        "/rules/import",
        data={
            CSRF_FIELD_NAME: set_csrf_token(owner_client),
            "mode": "add",
            "rules_file": (io.BytesIO(b"keyword,category\n,Food\n"), "bad.csv"),
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert_visible_text(response, "Row 2: keyword or merchant_name is required.")
    assert len(submitted_jobs) == 0
