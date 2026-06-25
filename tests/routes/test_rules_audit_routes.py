"""Route tests for the rules audit feature."""

from sqlalchemy import text
from tests.support.database import insert_rule
from tests.support.html import (
    assert_form,
    assert_has_element,
    assert_input,
    assert_link,
    assert_no_element,
    assert_not_visible_text,
    assert_visible_text,
    parse_html,
    visible_html,
)
from tests.support.rules import (
    html_fragment_after,
    rule_by_id,
    set_default_table_page_size,
    set_rule_audit_transaction_limit,
)
from tests.support.web import set_csrf_token

from finance_app.core.csrf import CSRF_FIELD_NAME
from finance_app.modules.categories.taxonomy import get_rule_tags_by_rule_id, set_rule_tags


def test_rules_audit_route_renders_summary_and_findings(client, core_conn):
    """Verify the rule audit route renders overlap, shadowed, and unused findings."""
    broad_rule_id = insert_rule(core_conn, keyword="METRO", category="Food")
    specific_rule_id = insert_rule(core_conn, keyword="METRO GROCERY", category="Utilities")
    insert_rule(core_conn, keyword="UNUSED SHOP", category="Food")
    insert_rule(core_conn, keyword="AI SUGGESTED SHOP", category="Food", source="automatic")
    insert_rule(core_conn, keyword="AI APPROVED SHOP", category="Food", source="automatic", ai_approved=1)
    core_conn.execute(
        text("""
        INSERT INTO transactions (
            tx_date, description, amount, category, category_source,
            category_rule_id, needs_review, fingerprint
        )
        VALUES (
            '2026-01-02', 'Metro Grocery #123', 14.25, 'Food', 'rule',
            :p0, 0, 'audit-route-match'
        )
        """),
        {"p0": specific_rule_id},
    )
    core_conn.commit()

    response = client.get("/rules/audit")
    body = response.get_data(as_text=True)
    document = parse_html(response)

    assert response.status_code == 200
    assert_visible_text(
        response,
        "Rule health check",
        "Rule overlap findings",
        "Conflict type",
    )
    assert_has_element(response, "div", attrs={"role": "group", "aria-label": "Filter overlap findings"})
    assert_has_element(
        response,
        "input",
        attrs={"placeholder": "Search rule, category, tag, scope, or action"},
    )
    assert_has_element(
        response,
        None,
        attrs={"class": "rule-audit-search-row", "data-ajax-refresh-form": True},
    )
    assert_has_element(response, None, attrs={"data-busy-message": "Preparing rule health check preview..."})
    assert_has_element(response, None, attrs={"data-busy-message": "Loading rule health check details..."})
    assert_has_element(response, None, attrs={"data-collapse-panel-header-toggle": True})
    assert_has_element(response, None, attrs={"data-collapse-panel-heading-toggle": True})
    assert_has_element(response, None, attrs={"data-row-drilldown": "dblclick"})
    assert 'data-row-href="/rules/audit/overlap/' in body
    row_fragment = html_fragment_after(body, 'data-row-href="/rules/audit/overlap/')
    assert "data-busy-overlay" in row_fragment
    assert 'data-busy-delay-ms="0"' in row_fragment
    assert_visible_text(
        response,
        "Category conflict",
        "Rules skipped by priority",
        "Stale and unused rules",
        "Scope",
        "METRO",
        "METRO GROCERY",
        "UNUSED SHOP",
        "Manual",
        "Suggested",
    )
    assert "Suggested" in html_fragment_after(body, "AI SUGGESTED SHOP")
    assert "Approved" in html_fragment_after(body, "AI APPROVED SHOP")
    assert "Showing 1-1 of 1 findings" in body
    assert "overlap_sort=shared" in body
    assert_visible_text(
        response,
        "Delete rule",
        "Rule detail",
        "Recommended next step",
        "Recommended action",
        "Review category conflict",
    )
    assert "Rule A" in visible_html(response) or "Rule B" in visible_html(response)
    assert document.has_element(
        "a", attrs={"href": f"/rules/audit/overlap/{broad_rule_id}/{specific_rule_id}"}
    ) or document.has_element("a", attrs={"href": f"/rules/audit/overlap/{specific_rule_id}/{broad_rule_id}"})
    assert_link(response, f"/rules/audit/rule/{broad_rule_id}")
    assert_visible_text(response, "These rules assign different categories:")
    assert broad_rule_id is not None


def test_rules_audit_route_paginates_and_sorts_overlap_table(client, core_conn):
    """Verify the audit overlap table has independent sorting and pagination."""
    set_default_table_page_size(core_conn, 1)
    insert_rule(core_conn, keyword="CAFE", category="Food")
    insert_rule(core_conn, keyword="CAFE TAX", category="Utilities")
    insert_rule(core_conn, keyword="METRO", category="Food")
    insert_rule(core_conn, keyword="METRO GROCERY", category="Utilities")
    core_conn.execute(text("""
        INSERT INTO transactions (
            tx_date, description, amount, category, category_source,
            needs_review, fingerprint
        )
        VALUES
            ('2026-01-02', 'Cafe Tax Membership', 8.50, 'Food', 'manual', 0, 'audit-page-cafe'),
            ('2026-01-03', 'Metro Grocery #123', 14.25, 'Food', 'manual', 0, 'audit-page-metro')
        """))
    core_conn.commit()

    response = client.get("/rules/audit?overlap_sort=rule_a&overlap_direction=asc&overlap_page=2")
    body = response.get_data(as_text=True)
    overlap_section = body.split("Rule overlap findings", 1)[1].split(
        "Precision and priority warnings",
        1,
    )[0]

    assert response.status_code == 200
    assert "Showing 2-2 of 2 findings" in overlap_section
    assert "METRO" in overlap_section
    assert "CAFE" not in overlap_section
    assert (
        'href="/rules/audit?overlap_sort=rule_a&amp;'
        'overlap_direction=asc&amp;overlap_page=1&amp;open=rule-overlap-findings"'
    ) in overlap_section
    assert (
        'href="/rules/audit?overlap_sort=shared&amp;'
        'overlap_direction=asc&amp;overlap_page=1&amp;open=rule-overlap-findings"'
    ) in overlap_section


def test_rules_audit_route_filters_overlap_findings(client, core_conn):
    """Verify the main audit table can be filtered by overlap severity."""
    insert_rule(core_conn, keyword="CAFE", category="Food")
    tagged_rule_id = insert_rule(core_conn, keyword="CAFE TAX", category="Food")
    set_rule_tags(core_conn, tagged_rule_id, ["Tax"])
    insert_rule(core_conn, keyword="METRO", category="Food")
    insert_rule(core_conn, keyword="METRO GROCERY", category="Utilities")
    insert_rule(core_conn, keyword="UNUSED AUDIT", category="Utilities")
    core_conn.execute(text("""
        INSERT INTO transactions (
            tx_date, description, amount, category, category_source,
            needs_review, fingerprint
        )
        VALUES
            ('2026-01-02', 'Cafe Tax Membership', 8.50, 'Food', 'manual', 0, 'audit-filter-cafe'),
            ('2026-01-03', 'Metro Grocery #123', 14.25, 'Food', 'manual', 0, 'audit-filter-metro')
        """))
    core_conn.commit()

    response = client.get("/rules/audit?overlap_filter=tag_difference")
    body = response.get_data(as_text=True)
    overlap_section = body.split("Rule overlap findings", 1)[1].split(
        "Precision and priority warnings",
        1,
    )[0]

    assert response.status_code == 200
    assert "Showing 1-1 of 1 findings" in overlap_section
    assert "CAFE" in overlap_section
    assert "METRO" not in overlap_section
    assert "Hide table" not in overlap_section
    assert "Show table" not in overlap_section
    assert 'aria-expanded="true"' in body
    assert "data-collapse-panel-header-toggle" in overlap_section
    assert 'value="category_conflict"' in body
    assert 'class="btn-check"' in body
    assert 'type="radio"' in body
    assert 'data-ajax-refresh-target="rule-audit"' in body
    assert "data-ajax-refresh-form" in body
    assert "data-ajax-refresh-link" in overlap_section

    search_response = client.get("/rules/audit?overlap_q=cafe&overlap_filter=all")
    search_body = search_response.get_data(as_text=True)
    search_overlap_section = search_body.split("Rule overlap findings", 1)[1].split(
        "Precision and priority warnings",
        1,
    )[0]

    assert search_response.status_code == 200
    assert 'value="cafe"' in search_body
    assert "Showing 1-1 of 1 findings" in search_overlap_section
    assert "CAFE" in search_overlap_section
    assert "METRO" not in search_overlap_section
    assert "UNUSED AUDIT" not in search_body

    unused_response = client.get("/rules/audit?overlap_q=unused&overlap_filter=all")
    unused_body = unused_response.get_data(as_text=True)
    unused_overlap_section = unused_body.split("Rule overlap findings", 1)[1].split(
        "Precision and priority warnings",
        1,
    )[0]
    unused_stale_section = unused_body.split("Stale and unused rules", 1)[1]

    assert unused_response.status_code == 200
    assert "No overlapping rules found." in unused_overlap_section
    assert "UNUSED AUDIT" in unused_stale_section


def test_rules_audit_route_limits_historical_transactions(client, core_conn):
    """Verify the audit route uses the configured historical transaction cap."""
    set_rule_audit_transaction_limit(core_conn, 1)
    insert_rule(core_conn, keyword="METRO", category="Food")
    core_conn.execute(text("""
        INSERT INTO transactions (
            tx_date, description, amount, category, category_source,
            needs_review, fingerprint
        )
        VALUES
            ('2026-01-02', 'Metro Pharmacy old', 8.50, 'Food', 'manual', 0, 'audit-limit-old'),
            ('2026-01-03', 'Metro Pharmacy new', 14.25, 'Food', 'manual', 0, 'audit-limit-new')
        """))
    core_conn.commit()

    response = client.get("/rules/audit")

    assert response.status_code == 200
    assert_visible_text(response, "Audit is limited to recent transactions.")
    assert_not_visible_text(response, "Metro Pharmacy old")


def test_rules_audit_overlap_route_renders_shared_transactions(client, core_conn):
    """Verify overlap detail shows shared transactions and match explanations."""
    broad_rule_id = insert_rule(core_conn, keyword="METRO", category="Food", source="automatic")
    specific_rule_id = insert_rule(
        core_conn,
        keyword="METRO GROCERY",
        category="Utilities",
        source="automatic",
        ai_approved=1,
    )
    core_conn.execute(
        text("""
        INSERT INTO transactions (
            tx_date, description, amount, category, category_source,
            category_rule_id, needs_review, fingerprint
        )
        VALUES (
            '2026-01-02', 'Metro Grocery #123', 14.25, 'Food', 'rule',
            :p0, 0, 'audit-overlap-detail'
        )
        """),
        {"p0": broad_rule_id},
    )
    core_conn.commit()

    response = client.get(f"/rules/audit/overlap/{broad_rule_id}/{specific_rule_id}")
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert_visible_text(
        response,
        "Shared matching transactions",
        "Every row below is matched by both rules.",
        "Metro Grocery #123",
        "Applied rule result",
        "Rule not applied result",
        "Rule not applied",
        "Why this rule is applied",
        "Applied rule",
        "Recommended action",
        "Confidence level",
        "Match score",
        "Precision",
        "More precise",
        "Less precise",
        "Suggested",
        "Approved",
    )
    assert "Suggested" in html_fragment_after(body, '<div class="rule-keyword">METRO</div>')
    assert "Approved" in html_fragment_after(body, '<div class="rule-keyword">METRO GROCERY</div>')
    assert_not_visible_text(response, "Winner agrees")
    assert ">No<" not in body
    assert_link(response, f"/rules/audit/rule/{broad_rule_id}")
    assert_link(response, f"/rules/audit/rule/{specific_rule_id}")
    assert_form(response, "/rules/audit/preview", method="post")
    assert_input(response, name="action", value="apply_where_wins")
    assert body.count('name="action" value="delete_rule"') == 2
    assert body.count("Preview delete") == 2
    assert_input(response, name="rule_id", value=str(broad_rule_id))
    assert_input(response, name="rule_id", value=str(specific_rule_id))


def test_rules_audit_overlap_route_paginates_and_sorts_shared_transactions(client, core_conn):
    """Verify shared transaction detail rows have stable sort and pagination."""
    set_default_table_page_size(core_conn, 1)
    broad_rule_id = insert_rule(core_conn, keyword="METRO", category="Food")
    specific_rule_id = insert_rule(core_conn, keyword="METRO GROCERY", category="Utilities")
    core_conn.execute(
        text("""
        INSERT INTO transactions (
            tx_date, description, amount, category, category_source,
            category_rule_id, needs_review, fingerprint
        )
        VALUES
            ('2026-01-02', 'Metro Grocery A', 14.25, 'Food', 'rule', :p0, 0, 'audit-overlap-page-a'),
            ('2026-01-03', 'Metro Grocery Z', 16.25, 'Food', 'rule', :p1, 0, 'audit-overlap-page-z')
        """),
        {"p0": broad_rule_id, "p1": broad_rule_id},
    )
    core_conn.commit()

    response = client.get(
        f"/rules/audit/overlap/{broad_rule_id}/{specific_rule_id}"
        "?shared_sort=description&shared_direction=asc&shared_page=2"
    )
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Showing 2-2 of 2 transactions" in body
    assert "Metro Grocery Z" in body
    assert "Metro Grocery A" not in body
    assert (
        f'href="/rules/audit/overlap/{broad_rule_id}/{specific_rule_id}?'
        'shared_sort=description&amp;shared_direction=asc&amp;shared_page=1"'
    ) in body
    assert (
        f'href="/rules/audit/overlap/{broad_rule_id}/{specific_rule_id}?'
        'shared_sort=amount&amp;shared_direction=desc&amp;shared_page=1"'
    ) in body


def test_rules_audit_route_renders_specificity_warnings(client, core_conn):
    """Verify the audit page shows broad winners over more constrained rules."""
    broad_rule_id = insert_rule(core_conn, keyword="METRO GROCERY", category="Food")
    specific_rule_id = insert_rule(core_conn, keyword="METRO", category="Utilities", amount_min=10)
    core_conn.execute(
        text("""
        INSERT INTO transactions (
            tx_date, description, amount, category, category_source,
            category_rule_id, needs_review, fingerprint
        )
        VALUES (
            '2026-01-02', 'Metro Grocery', 12.34, 'Food', 'rule',
            :p0, 0, 'audit-route-specificity'
        )
        """),
        {"p0": broad_rule_id},
    )
    core_conn.commit()

    response = client.get("/rules/audit")

    assert response.status_code == 200
    assert_visible_text(
        response,
        "Precision and priority warnings",
        "A broader applied rule can hide a more constrained overlapping rule.",
        "Broad applied rule",
        "More precise rule",
        "Higher confidence",
    )
    assert_link(response, f"/rules/audit/overlap/{broad_rule_id}/{specific_rule_id}")


def test_rules_audit_preview_route_renders_remove_rule_impact(client, core_conn):
    """Verify the audit preview route renders read-only delete-rule impact."""
    broad_rule_id = insert_rule(core_conn, keyword="METRO", category="Food")
    specific_rule_id = insert_rule(core_conn, keyword="METRO GROCERY", category="Utilities")
    set_rule_tags(core_conn, broad_rule_id, ["Grocery"])
    set_rule_tags(core_conn, specific_rule_id, ["Tax"])
    core_conn.execute(
        text("""
        INSERT INTO transactions (
            tx_date, description, amount, category, category_source,
            category_rule_id, needs_review, fingerprint
        )
        VALUES (
            '2026-01-02', 'Metro Grocery #123', 12.34, 'Utilities', 'rule',
            :p0, 0, 'audit-route-preview'
        )
        """),
        {"p0": specific_rule_id},
    )
    core_conn.commit()

    response = client.post(
        "/rules/audit/preview",
        data={
            CSRF_FIELD_NAME: set_csrf_token(client),
            "action": "delete_rule",
            "rule_id": str(specific_rule_id),
        },
    )
    stored = core_conn.execute(text("""
        SELECT category, category_rule_id
        FROM transactions
        WHERE fingerprint = 'audit-route-preview'
        """)).fetchone()

    assert response.status_code == 200
    assert_visible_text(
        response,
        "Impact preview",
        "Preview is read-only and does not modify rules or transactions.",
        "Affected transactions",
        "Applied rule changes",
        "Category would change",
        "Confirm delete",
        "Deleting this rule keeps existing transaction categories and tags",
        "Metro Grocery #123",
        "METRO GROCERY",
        "METRO",
    )
    assert_form(response, f"/rules/{specific_rule_id}/delete", method="post")
    assert_input(response, name="confirm_preview", value="1")
    assert tuple(stored) == ("Utilities", specific_rule_id)


def test_rules_audit_preview_route_renders_no_historical_change_state(client, core_conn):
    """Verify previews with no historical impacts explain future-only changes."""
    rule_id = insert_rule(core_conn, keyword="UNUSED STORE", category="Food")

    response = client.post(
        "/rules/audit/preview",
        data={
            CSRF_FIELD_NAME: set_csrf_token(client),
            "action": "delete_rule",
            "rule_id": str(rule_id),
        },
    )

    assert response.status_code == 200
    assert_visible_text(
        response,
        "No historical transactions would change.",
        "No transaction-level changes found.",
        "Confirm delete",
    )


def test_rules_audit_preview_route_renders_no_material_change_state(client, core_conn):
    """Verify previews with only winning-rule changes hide empty impact groups."""
    broad_rule_id = insert_rule(core_conn, keyword="METRO", category="Food")
    specific_rule_id = insert_rule(core_conn, keyword="METRO GROCERY", category="Food")
    set_rule_tags(core_conn, broad_rule_id, ["Tax"])
    set_rule_tags(core_conn, specific_rule_id, ["Tax"])
    core_conn.execute(
        text("""
        INSERT INTO transactions (
            tx_date, description, amount, category, category_source,
            category_rule_id, needs_review, fingerprint
        )
        VALUES (
            '2026-01-02', 'Metro Grocery #123', 12.34, 'Food', 'rule',
            :p0, 0, 'audit-route-preview-no-material'
        )
        """),
        {"p0": specific_rule_id},
    )
    core_conn.commit()

    response = client.post(
        "/rules/audit/preview",
        data={
            CSRF_FIELD_NAME: set_csrf_token(client),
            "action": "delete_rule",
            "rule_id": str(specific_rule_id),
        },
    )

    assert response.status_code == 200
    assert_visible_text(
        response,
        "Historical categories and tags would stay the same.",
        "Applied rule would change",
    )
    assert_not_visible_text(response, "No transactions in this group.")


def test_rules_audit_preview_route_renders_create_rule_impact(client, core_conn):
    """Verify create preview renders a confirm form without mutating rules."""
    core_conn.execute(text("""
        INSERT INTO transactions (
            tx_date, description, amount, category, category_source,
            needs_review, fingerprint
        )
        VALUES (
            '2026-01-02', 'Metro Pharmacy', 8.50, 'UNKNOWN', 'unknown',
            1, 'audit-route-preview-create'
        )
        """))
    core_conn.commit()

    response = client.post(
        "/rules/audit/preview",
        data={
            CSRF_FIELD_NAME: set_csrf_token(client),
            "action": "create_rule",
            "keyword": "Metro",
            "category": "Food",
            "tags": ["Grocery"],
        },
    )
    stored_rule = core_conn.execute(text("SELECT id FROM category_rules WHERE keyword = 'METRO'")).fetchone()

    assert response.status_code == 200
    assert_visible_text(response, "Preview creating rule", "Confirm create", "Category would change")
    assert_form(response, "/rules/create", method="post")
    assert_input(response, name="confirm_preview", value="1")
    assert_input(response, name="tags", value="Grocery")
    assert stored_rule is None


def test_rules_audit_preview_route_renders_approve_rule_confirmation(client, core_conn):
    """Verify approve preview renders a confirmation without mutating approval."""
    rule_id = insert_rule(core_conn, keyword="AUTO STORE", category="Food", source="automatic")

    response = client.post(
        "/rules/audit/preview",
        data={
            CSRF_FIELD_NAME: set_csrf_token(client),
            "action": "approve_rule",
            "rule_id": rule_id,
        },
    )
    rule = rule_by_id(core_conn, rule_id)

    assert response.status_code == 200
    assert_visible_text(
        response,
        "Preview approving rule",
        "Confirm approve",
        "No historical transactions would change.",
    )
    assert_form(response, f"/rules/{rule_id}/approve", method="post")
    assert_input(response, name="confirm_preview", value="1")
    assert rule._mapping["ai_approved"] == 0


def test_rules_audit_preview_route_renders_edit_rule_impact(client, core_conn):
    """Verify edit preview renders a confirm form without mutating the rule."""
    rule_id = insert_rule(core_conn, keyword="METRO", category="Food")
    set_rule_tags(core_conn, rule_id, ["Grocery"])
    core_conn.execute(
        text("""
        INSERT INTO transactions (
            tx_date, description, amount, category, category_source,
            category_rule_id, needs_review, fingerprint
        )
        VALUES (
            '2026-01-02', 'Metro Pharmacy', 12.34, 'Food', 'rule',
            :p0, 0, 'audit-route-preview-edit'
        )
        """),
        {"p0": rule_id},
    )
    core_conn.commit()

    response = client.post(
        "/rules/audit/preview",
        data={
            CSRF_FIELD_NAME: set_csrf_token(client),
            "action": "edit_rule",
            "rule_id": str(rule_id),
            "keyword": "Metro",
            "category": "Utilities",
            "tags": ["Tax"],
            "direction": "any",
            "amount_min": "10",
            "amount_max": "20",
        },
    )
    stored_rule = rule_by_id(core_conn, rule_id)

    assert response.status_code == 200
    assert_visible_text(
        response,
        "Preview editing rule",
        "Confirm update",
        "This updates the rule only after reviewing the impact below.",
        "Category would change",
    )
    assert_form(response, f"/rules/{rule_id}/update", method="post")
    assert_input(response, name="confirm_preview", value="1")
    assert_input(response, name="category", value="Utilities")
    assert_input(response, name="tags", value="Tax")
    assert tuple(stored_rule[1:]) == ("METRO", "Food", None, None, "manual", 0)
    assert get_rule_tags_by_rule_id(core_conn, [rule_id])[rule_id] == ["Grocery"]


def test_rules_audit_preview_route_renders_apply_all_impact(client, core_conn):
    """Verify the audit preview route supports apply-all impact without a rule ID."""
    rule_id = insert_rule(core_conn, keyword="METRO", category="Food")
    core_conn.execute(text("""
        INSERT INTO transactions (
            tx_date, description, amount, category, category_source,
            needs_review, fingerprint
        )
        VALUES (
            '2026-01-02', 'Metro Pharmacy', 8.50, 'UNKNOWN', 'unknown',
            1, 'audit-route-preview-apply-all'
        )
        """))
    core_conn.commit()

    response = client.post(
        "/rules/audit/preview",
        data={
            CSRF_FIELD_NAME: set_csrf_token(client),
            "action": "apply_all_rules",
        },
    )
    stored = core_conn.execute(text("""
        SELECT category, category_rule_id
        FROM transactions
        WHERE fingerprint = 'audit-route-preview-apply-all'
        """)).fetchone()

    assert response.status_code == 200
    assert_visible_text(response, "Preview applying all rules", "Confirm apply all", "Metro Pharmacy")
    assert_form(response, "/rules/apply-all", method="post")
    assert_input(response, name="confirm_preview", value="1")
    assert_no_element(response, "a", attrs={"href": f"/rules/audit/rule/{rule_id}"})
    assert tuple(stored) == ("UNKNOWN", None)


def test_rules_audit_preview_route_marks_impact_tables_paginated_and_sortable(client, core_conn):
    """Verify preview impact tables expose client-side sorting and pagination."""
    set_default_table_page_size(core_conn, 1)
    rule_id = insert_rule(core_conn, keyword="METRO", category="Food")
    set_rule_tags(core_conn, rule_id, ["Grocery"])
    core_conn.execute(
        text("""
        INSERT INTO transactions (
            tx_date, description, amount, category, category_source,
            category_rule_id, needs_review, fingerprint
        )
        VALUES
            ('2026-01-02', 'Metro Pharmacy B', 12.34, 'Food', 'rule', :p0, 0, 'audit-preview-table-b'),
            ('2026-01-03', 'Metro Pharmacy A', 8.50, 'Food', 'rule', :p1, 0, 'audit-preview-table-a')
        """),
        {"p0": rule_id, "p1": rule_id},
    )
    core_conn.commit()

    response = client.post(
        "/rules/audit/preview",
        data={
            CSRF_FIELD_NAME: set_csrf_token(client),
            "action": "edit_rule",
            "rule_id": str(rule_id),
            "keyword": "Metro",
            "category": "Utilities",
            "direction": "any",
        },
    )
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "data-paginated-table" in body
    assert 'data-page-size="1"' in body
    assert 'data-pagination-label="Preview impact pages"' in body
    assert 'data-sort-column="0" data-sort-type="text"' in body
    assert 'data-sort-column="3" data-sort-type="number"' in body
    assert "Metro Pharmacy A" in body
    assert "Metro Pharmacy B" in body


def test_rules_audit_rule_route_renders_rule_diagnostics(client, core_conn):
    """Verify the rule detail page shows per-rule audit diagnostics."""
    set_default_table_page_size(core_conn, 1)
    broad_rule_id = insert_rule(core_conn, keyword="METRO", category="Food")
    specific_rule_id = insert_rule(core_conn, keyword="METRO GROCERY", category="Utilities")
    core_conn.execute(
        text("""
        INSERT INTO transactions (
            tx_date, description, amount, category, category_source,
            category_rule_id, needs_review, fingerprint
        )
        VALUES (
            '2026-01-02', 'Metro Grocery #123', 14.25, 'Utilities', 'rule',
            :p0, 0, 'audit-rule-detail'
        )
        """),
        {"p0": specific_rule_id},
    )
    core_conn.commit()

    response = client.get(f"/rules/audit/rule/{broad_rule_id}")

    assert response.status_code == 200
    assert_visible_text(
        response,
        "Rule detail",
        "This page is read-only and does not change rule behavior.",
        "Delete rule",
        "Preview normal apply",
        "Preview force apply",
        "Rule summary",
        "Manual",
        "Assessment",
        "Recommended action",
        "Historical matches",
        "Applied rate",
        "Overlapping rules",
        "Rules taking priority over this rule",
        "METRO GROCERY",
    )
    assert_has_element(
        response,
        None,
        attrs={
            "data-paginated-table": True,
            "data-page-size": "1",
            "data-pagination-label": "Rule interaction pages",
        },
    )
    assert_has_element(response, None, attrs={"data-sort-column": "1", "data-sort-type": "number"})
    document = parse_html(response)
    assert document.has_element(
        "a", attrs={"href": f"/rules/audit/overlap/{broad_rule_id}/{specific_rule_id}"}
    ) or document.has_element("a", attrs={"href": f"/rules/audit/overlap/{specific_rule_id}/{broad_rule_id}"})


def test_rules_audit_rule_route_renders_automatic_approval_status(client, core_conn):
    """Verify automatic rule detail shows suggested or approved status."""
    suggested_rule_id = insert_rule(
        core_conn,
        keyword="AI SUGGESTED DETAIL",
        category="Food",
        source="automatic",
        ai_approved=0,
    )
    approved_rule_id = insert_rule(
        core_conn,
        keyword="AI APPROVED DETAIL",
        category="Food",
        source="automatic",
        ai_approved=1,
    )

    suggested_response = client.get(f"/rules/audit/rule/{suggested_rule_id}")
    approved_response = client.get(f"/rules/audit/rule/{approved_rule_id}")

    assert suggested_response.status_code == 200
    assert approved_response.status_code == 200
    assert_visible_text(suggested_response, "Suggested")
    assert_visible_text(approved_response, "Approved")
