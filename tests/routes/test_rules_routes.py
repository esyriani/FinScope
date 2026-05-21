"""Route tests for the rules feature."""

import csv
import io

import pytest

from finance_app.core.csrf import CSRF_FIELD_NAME, CSRF_SESSION_KEY
from finance_app.modules.categories.tag_filters import UNTAGGED_TAG_FILTER
from finance_app.modules.categories.taxonomy import get_rule_tags_by_rule_id, set_rule_tags
from finance_app.modules.rules import controller as rules_controller
from finance_app.modules.rules.import_export import import_rules_job, undo_import_rules_job


def set_csrf_token(client, token="test-csrf-token"):
    """Store a CSRF token in the test client's session."""
    with client.session_transaction() as session:
        session[CSRF_SESSION_KEY] = token
    return token


def insert_rule(
    conn,
    keyword="METRO",
    category="Transportation",
    amount_min=None,
    amount_max=None,
    source="manual",
    ai_approved=0,
    merchant_id=None,
):
    """Insert a category rule for route tests."""
    rule_id = conn.execute(
        """
        INSERT INTO category_rules (merchant_id, keyword, category, amount_min, amount_max, source, ai_approved)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (merchant_id, keyword, category, amount_min, amount_max, source, ai_approved),
    ).lastrowid
    conn.commit()
    return rule_id


def insert_merchant(conn, name="TEST MERCHANT"):
    """Insert a merchant row for route tests."""
    merchant_id = conn.execute(
        """
        INSERT INTO merchants (canonical_key, system_name, display_name)
        VALUES (?, ?, ?)
        """,
        (name, name, name),
    ).lastrowid
    conn.commit()
    return merchant_id


def rule_by_id(conn, rule_id):
    """Return a category rule row by id."""
    return conn.execute(
        """
        SELECT id, keyword, category, amount_min, amount_max, source, ai_approved
        FROM category_rules
        WHERE id = ?
        """,
        (rule_id,),
    ).fetchone()


def set_default_table_page_size(conn, size):
    """Set the owner's default table page size for route rendering tests."""
    set_owner_setting(conn, "default_table_page_size", size)


def set_rule_audit_transaction_limit(conn, size):
    """Set the owner's Rule Audit transaction limit for route rendering tests."""
    set_owner_setting(conn, "rule_audit_transaction_limit", size)


def set_owner_setting(conn, key, value):
    """Set one owner-bound runtime setting for route rendering tests."""
    result = conn.execute(
        """
        UPDATE user_settings
        SET value = ?
        WHERE key = ?
          AND user_id = (SELECT id FROM users WHERE username = 'owner')
        """,
        (str(value), key),
    )
    if result.rowcount == 0:
        conn.execute(
            """
            INSERT INTO user_settings (user_id, key, value)
            SELECT id, ?, ?
            FROM users
            WHERE username = 'owner'
            """,
            (key, str(value)),
        )
    conn.commit()


def test_rules_create_route_persists_rule_and_tags(client, db_conn):
    """Verify that the create route stores a manual rule."""
    response = client.post(
        "/rules/create",
        data={
            CSRF_FIELD_NAME: set_csrf_token(client),
            "confirm_preview": "1",
            "keyword": "Metro Grocery",
            "category": "Food",
            "tags": ["Tax"],
            "amount_min": "10",
            "amount_max": "20",
        },
        follow_redirects=True,
    )

    rule = db_conn.execute(
        """
        SELECT id, keyword, category, amount_min, amount_max, source
        FROM category_rules
        WHERE keyword = 'METRO GROCERY'
        """
    ).fetchone()
    assert response.status_code == 200
    assert b"Rule saved for: METRO GROCERY" in response.data
    assert tuple(rule[1:]) == ("METRO GROCERY", "Food", 10.0, 20.0, "manual")
    assert get_rule_tags_by_rule_id(db_conn, [rule["id"]])[rule["id"]] == ["Tax"]


def test_rules_create_route_requires_preview_confirmation(client, db_conn):
    """Verify direct rule creation is blocked until preview confirmation."""
    response = client.post(
        "/rules/create",
        data={
            CSRF_FIELD_NAME: set_csrf_token(client),
            "keyword": "Metro Grocery",
            "category": "Food",
        },
        follow_redirects=True,
    )
    rule = db_conn.execute(
        "SELECT id FROM category_rules WHERE keyword = 'METRO GROCERY'"
    ).fetchone()

    assert response.status_code == 200
    assert b"Preview creation before saving a rule." in response.data
    assert rule is None


def test_rules_route_renders_automatic_source_badge(client, db_conn):
    """Verify that automatic rules show the automatic source badge."""
    db_conn.execute(
        """
        INSERT INTO category_rules (keyword, category, source)
        VALUES ('METRO GROCERY', 'Food', 'automatic')
        """
    )
    db_conn.commit()

    response = client.get("/rules")

    assert response.status_code == 200
    assert b"text-bg-info" in response.data
    assert b"Auto" in response.data
    assert b"Automatic" in response.data
    assert b"Suggested" in response.data
    assert b"Approve" in response.data


def test_rules_route_links_to_rule_audit(client):
    """Verify the rules page links bulk actions through the audit page."""
    response = client.get("/rules")

    assert response.status_code == 200
    assert b"Rule audit" in response.data
    assert b'href="/rules/audit"' in response.data
    assert b'action="/rules/audit/preview"' in response.data
    assert b'name="action" value="apply_all_rules"' in response.data
    assert b"Preview apply all" in response.data
    assert b"Preview import" in response.data
    assert b'name="action" value="create_rule"' in response.data
    assert b"Preview create" in response.data


def test_rules_audit_route_renders_summary_and_findings(client, db_conn):
    """Verify the rule audit route renders overlap, shadowed, and unused findings."""
    broad_rule_id = insert_rule(db_conn, keyword="METRO", category="Food")
    specific_rule_id = insert_rule(db_conn, keyword="METRO GROCERY", category="Utilities")
    insert_rule(db_conn, keyword="UNUSED SHOP", category="Food")
    db_conn.execute(
        """
        INSERT INTO transactions (
            tx_date, description, amount, category, category_source,
            category_rule_id, needs_review, fingerprint
        )
        VALUES (
            '2026-01-02', 'Metro Grocery #123', 14.25, 'Food', 'rule',
            ?, 0, 'audit-route-match'
        )
        """,
        (specific_rule_id,),
    )
    db_conn.commit()

    response = client.get("/rules/audit")
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert b"Rule audit" in response.data
    assert b"Rule overlap findings" in response.data
    assert b"Filter overlap findings" in response.data
    assert b"Search rule, category, tag, scope, or action" in response.data
    assert b"Conflict type" in response.data
    assert b"Category conflict" in response.data
    assert b"Shadowed rules" in response.data
    assert b"Stale and unused rules" in response.data
    assert b"Scope" in response.data
    assert b"METRO" in response.data
    assert b"METRO GROCERY" in response.data
    assert b"UNUSED SHOP" in response.data
    assert "Showing 1-1 of 1 findings" in body
    assert "overlap_sort=shared" in body
    assert b"Preview delete" in response.data
    assert b"Rule A" in response.data or b"Rule B" in response.data
    assert b"Rule detail" in response.data
    assert (
        f"/rules/audit/overlap/{broad_rule_id}/{specific_rule_id}".encode() in response.data
        or f"/rules/audit/overlap/{specific_rule_id}/{broad_rule_id}".encode() in response.data
    )
    assert f"/rules/audit/rule/{broad_rule_id}".encode() in response.data
    assert b"This is a category conflict. FinScope currently applies the winning rule silently." in response.data
    assert broad_rule_id is not None


def test_rules_audit_route_paginates_and_sorts_overlap_table(client, db_conn):
    """Verify the audit overlap table has independent sorting and pagination."""
    set_default_table_page_size(db_conn, 1)
    insert_rule(db_conn, keyword="CAFE", category="Food")
    insert_rule(db_conn, keyword="CAFE TAX", category="Utilities")
    insert_rule(db_conn, keyword="METRO", category="Food")
    insert_rule(db_conn, keyword="METRO GROCERY", category="Utilities")
    db_conn.execute(
        """
        INSERT INTO transactions (
            tx_date, description, amount, category, category_source,
            needs_review, fingerprint
        )
        VALUES
            ('2026-01-02', 'Cafe Tax Membership', 8.50, 'Food', 'manual', 0, 'audit-page-cafe'),
            ('2026-01-03', 'Metro Grocery #123', 14.25, 'Food', 'manual', 0, 'audit-page-metro')
        """
    )
    db_conn.commit()

    response = client.get(
        "/rules/audit?overlap_sort=rule_a&overlap_direction=asc&overlap_page=2"
    )
    body = response.get_data(as_text=True)
    overlap_section = body.split("Rule overlap findings", 1)[1].split(
        "Specificity and precedence warnings",
        1,
    )[0]

    assert response.status_code == 200
    assert "Showing 2-2 of 2 findings" in overlap_section
    assert "METRO" in overlap_section
    assert "CAFE" not in overlap_section
    assert (
        'href="/rules/audit?overlap_sort=rule_a&amp;'
        'overlap_direction=asc&amp;overlap_page=1"'
    ) in overlap_section
    assert (
        'href="/rules/audit?overlap_sort=shared&amp;'
        'overlap_direction=asc&amp;overlap_page=1"'
    ) in overlap_section


def test_rules_audit_route_filters_overlap_findings(client, db_conn):
    """Verify the main audit table can be filtered by overlap severity."""
    insert_rule(db_conn, keyword="CAFE", category="Food")
    tagged_rule_id = insert_rule(db_conn, keyword="CAFE TAX", category="Food")
    set_rule_tags(db_conn, tagged_rule_id, ["Tax"])
    insert_rule(db_conn, keyword="METRO", category="Food")
    insert_rule(db_conn, keyword="METRO GROCERY", category="Utilities")
    insert_rule(db_conn, keyword="UNUSED AUDIT", category="Utilities")
    db_conn.execute(
        """
        INSERT INTO transactions (
            tx_date, description, amount, category, category_source,
            needs_review, fingerprint
        )
        VALUES
            ('2026-01-02', 'Cafe Tax Membership', 8.50, 'Food', 'manual', 0, 'audit-filter-cafe'),
            ('2026-01-03', 'Metro Grocery #123', 14.25, 'Food', 'manual', 0, 'audit-filter-metro')
        """
    )
    db_conn.commit()

    response = client.get("/rules/audit?overlap_filter=tag_difference")
    body = response.get_data(as_text=True)
    overlap_section = body.split("Rule overlap findings", 1)[1].split(
        "Specificity and precedence warnings",
        1,
    )[0]

    assert response.status_code == 200
    assert "Showing 1-1 of 1 findings" in overlap_section
    assert "CAFE" in overlap_section
    assert "METRO" not in overlap_section
    assert "Hide table" in overlap_section
    assert "bi-chevron-up" in overlap_section
    assert 'value="category_conflict"' in body
    assert 'class="btn-check"' in body
    assert 'type="radio"' in body
    assert 'data-ajax-refresh-target="rule-audit"' in body
    assert 'data-ajax-refresh-form' in body
    assert 'data-ajax-refresh-link' in overlap_section

    search_response = client.get("/rules/audit?overlap_q=cafe&overlap_filter=all")
    search_body = search_response.get_data(as_text=True)
    search_overlap_section = search_body.split("Rule overlap findings", 1)[1].split(
        "Specificity and precedence warnings",
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
        "Specificity and precedence warnings",
        1,
    )[0]
    unused_stale_section = unused_body.split("Stale and unused rules", 1)[1]

    assert unused_response.status_code == 200
    assert "No overlapping rules found." in unused_overlap_section
    assert "UNUSED AUDIT" in unused_stale_section


def test_rules_audit_route_limits_historical_transactions(client, db_conn):
    """Verify the audit route uses the configured historical transaction cap."""
    set_rule_audit_transaction_limit(db_conn, 1)
    insert_rule(db_conn, keyword="METRO", category="Food")
    db_conn.execute(
        """
        INSERT INTO transactions (
            tx_date, description, amount, category, category_source,
            needs_review, fingerprint
        )
        VALUES
            ('2026-01-02', 'Metro Pharmacy old', 8.50, 'Food', 'manual', 0, 'audit-limit-old'),
            ('2026-01-03', 'Metro Pharmacy new', 14.25, 'Food', 'manual', 0, 'audit-limit-new')
        """
    )
    db_conn.commit()

    response = client.get("/rules/audit")

    assert response.status_code == 200
    assert b"Audit is limited to recent transactions." in response.data
    assert b"Metro Pharmacy old" not in response.data


def test_rules_audit_overlap_route_renders_shared_transactions(client, db_conn):
    """Verify overlap detail shows shared transactions and match explanations."""
    broad_rule_id = insert_rule(db_conn, keyword="METRO", category="Food")
    specific_rule_id = insert_rule(db_conn, keyword="METRO GROCERY", category="Utilities")
    db_conn.execute(
        """
        INSERT INTO transactions (
            tx_date, description, amount, category, category_source,
            category_rule_id, needs_review, fingerprint
        )
        VALUES (
            '2026-01-02', 'Metro Grocery #123', 14.25, 'Food', 'rule',
            ?, 0, 'audit-overlap-detail'
        )
        """,
        (broad_rule_id,),
    )
    db_conn.commit()

    response = client.get(f"/rules/audit/overlap/{broad_rule_id}/{specific_rule_id}")

    assert response.status_code == 200
    assert b"Shared matching transactions" in response.data
    assert b"Every row below is matched by both rules." in response.data
    assert b"Metro Grocery #123" in response.data
    assert b"Winning rule result" in response.data
    assert b"Losing rule result" in response.data
    assert b"Losing rule" in response.data
    assert b"Confidence" in response.data
    assert b"Match score" in response.data
    assert b"Specificity" in response.data
    assert b"More specific" in response.data
    assert b"Less specific" in response.data
    assert b"Winner agrees" not in response.data
    assert b">No<" not in response.data
    assert f'href="/rules/audit/rule/{broad_rule_id}"'.encode() in response.data
    assert f'href="/rules/audit/rule/{specific_rule_id}"'.encode() in response.data
    assert b'action="/rules/audit/preview"' in response.data
    assert b'name="action" value="apply_where_wins"' in response.data
    assert f'name="rule_id" value="{broad_rule_id}"'.encode() in response.data
    assert f'name="rule_id" value="{specific_rule_id}"'.encode() in response.data


def test_rules_audit_overlap_route_paginates_and_sorts_shared_transactions(client, db_conn):
    """Verify shared transaction detail rows have stable sort and pagination."""
    set_default_table_page_size(db_conn, 1)
    broad_rule_id = insert_rule(db_conn, keyword="METRO", category="Food")
    specific_rule_id = insert_rule(db_conn, keyword="METRO GROCERY", category="Utilities")
    db_conn.execute(
        """
        INSERT INTO transactions (
            tx_date, description, amount, category, category_source,
            category_rule_id, needs_review, fingerprint
        )
        VALUES
            ('2026-01-02', 'Metro Grocery A', 14.25, 'Food', 'rule', ?, 0, 'audit-overlap-page-a'),
            ('2026-01-03', 'Metro Grocery Z', 16.25, 'Food', 'rule', ?, 0, 'audit-overlap-page-z')
        """,
        (broad_rule_id, broad_rule_id),
    )
    db_conn.commit()

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


def test_rules_audit_route_renders_specificity_warnings(client, db_conn):
    """Verify the audit page shows broad winners over more constrained rules."""
    broad_rule_id = insert_rule(db_conn, keyword="METRO GROCERY", category="Food")
    specific_rule_id = insert_rule(db_conn, keyword="METRO", category="Utilities", amount_min=10)
    db_conn.execute(
        """
        INSERT INTO transactions (
            tx_date, description, amount, category, category_source,
            category_rule_id, needs_review, fingerprint
        )
        VALUES (
            '2026-01-02', 'Metro Grocery', 12.34, 'Food', 'rule',
            ?, 0, 'audit-route-specificity'
        )
        """,
        (broad_rule_id,),
    )
    db_conn.commit()

    response = client.get("/rules/audit")

    assert response.status_code == 200
    assert b"Specificity and precedence warnings" in response.data
    assert b"A broader winning rule can hide a more constrained overlapping rule." in response.data
    assert b"Broad winning rule" in response.data
    assert b"More specific rule" in response.data
    assert b"Higher confidence" in response.data
    assert f"/rules/audit/overlap/{broad_rule_id}/{specific_rule_id}".encode() in response.data


def test_rules_audit_preview_route_renders_remove_rule_impact(client, db_conn):
    """Verify the audit preview route renders read-only delete-rule impact."""
    broad_rule_id = insert_rule(db_conn, keyword="METRO", category="Food")
    specific_rule_id = insert_rule(db_conn, keyword="METRO GROCERY", category="Utilities")
    set_rule_tags(db_conn, broad_rule_id, ["Grocery"])
    set_rule_tags(db_conn, specific_rule_id, ["Tax"])
    db_conn.execute(
        """
        INSERT INTO transactions (
            tx_date, description, amount, category, category_source,
            category_rule_id, needs_review, fingerprint
        )
        VALUES (
            '2026-01-02', 'Metro Grocery #123', 12.34, 'Utilities', 'rule',
            ?, 0, 'audit-route-preview'
        )
        """,
        (specific_rule_id,),
    )
    db_conn.commit()

    response = client.post(
        "/rules/audit/preview",
        data={
            CSRF_FIELD_NAME: set_csrf_token(client),
            "action": "delete_rule",
            "rule_id": str(specific_rule_id),
        },
    )
    stored = db_conn.execute(
        """
        SELECT category, category_rule_id
        FROM transactions
        WHERE fingerprint = 'audit-route-preview'
        """
    ).fetchone()

    assert response.status_code == 200
    assert b"Impact preview" in response.data
    assert b"Preview is read-only and does not modify rules or transactions." in response.data
    assert b"Affected transactions" in response.data
    assert b"Winning rule changes" in response.data
    assert b"Category would change" in response.data
    assert b"Confirm delete" in response.data
    assert b"Deleting this rule keeps existing transaction categories and tags" in response.data
    assert f'action="/rules/{specific_rule_id}/delete"'.encode() in response.data
    assert b'name="confirm_preview" value="1"' in response.data
    assert b"Metro Grocery #123" in response.data
    assert b"METRO GROCERY" in response.data
    assert b"METRO" in response.data
    assert tuple(stored) == ("Utilities", specific_rule_id)


def test_rules_audit_preview_route_renders_no_historical_change_state(client, db_conn):
    """Verify previews with no historical impacts explain future-only changes."""
    rule_id = insert_rule(db_conn, keyword="UNUSED STORE", category="Food")

    response = client.post(
        "/rules/audit/preview",
        data={
            CSRF_FIELD_NAME: set_csrf_token(client),
            "action": "delete_rule",
            "rule_id": str(rule_id),
        },
    )

    assert response.status_code == 200
    assert b"No historical transactions would change." in response.data
    assert b"No transaction-level changes found." in response.data
    assert b"Confirm delete" in response.data


def test_rules_audit_preview_route_renders_no_material_change_state(client, db_conn):
    """Verify previews with only winning-rule changes hide empty impact groups."""
    broad_rule_id = insert_rule(db_conn, keyword="METRO", category="Food")
    specific_rule_id = insert_rule(db_conn, keyword="METRO GROCERY", category="Food")
    set_rule_tags(db_conn, broad_rule_id, ["Tax"])
    set_rule_tags(db_conn, specific_rule_id, ["Tax"])
    db_conn.execute(
        """
        INSERT INTO transactions (
            tx_date, description, amount, category, category_source,
            category_rule_id, needs_review, fingerprint
        )
        VALUES (
            '2026-01-02', 'Metro Grocery #123', 12.34, 'Food', 'rule',
            ?, 0, 'audit-route-preview-no-material'
        )
        """,
        (specific_rule_id,),
    )
    db_conn.commit()

    response = client.post(
        "/rules/audit/preview",
        data={
            CSRF_FIELD_NAME: set_csrf_token(client),
            "action": "delete_rule",
            "rule_id": str(specific_rule_id),
        },
    )

    assert response.status_code == 200
    assert b"Historical categories and tags would stay the same." in response.data
    assert b"Winning rule would change" in response.data
    assert b"No transactions in this group." not in response.data


def test_rules_audit_preview_route_renders_create_rule_impact(client, db_conn):
    """Verify create preview renders a confirm form without mutating rules."""
    db_conn.execute(
        """
        INSERT INTO transactions (
            tx_date, description, amount, category, category_source,
            needs_review, fingerprint
        )
        VALUES (
            '2026-01-02', 'Metro Pharmacy', 8.50, 'UNKNOWN', 'unknown',
            1, 'audit-route-preview-create'
        )
        """
    )
    db_conn.commit()

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
    stored_rule = db_conn.execute(
        "SELECT id FROM category_rules WHERE keyword = 'METRO'"
    ).fetchone()

    assert response.status_code == 200
    assert b"Preview creating rule" in response.data
    assert b"Confirm create" in response.data
    assert b'action="/rules/create"' in response.data
    assert b'name="confirm_preview" value="1"' in response.data
    assert b'name="tags" value="Grocery"' in response.data
    assert b"Category would change" in response.data
    assert stored_rule is None


def test_rules_audit_preview_route_renders_approve_rule_confirmation(client, db_conn):
    """Verify approve preview renders a confirmation without mutating approval."""
    rule_id = insert_rule(db_conn, keyword="AUTO STORE", category="Food", source="automatic")

    response = client.post(
        "/rules/audit/preview",
        data={
            CSRF_FIELD_NAME: set_csrf_token(client),
            "action": "approve_rule",
            "rule_id": rule_id,
        },
    )
    rule = rule_by_id(db_conn, rule_id)

    assert response.status_code == 200
    assert b"Preview approving rule" in response.data
    assert b"Confirm approve" in response.data
    assert f'action="/rules/{rule_id}/approve"'.encode() in response.data
    assert b'name="confirm_preview" value="1"' in response.data
    assert b"No historical transactions would change." in response.data
    assert rule["ai_approved"] == 0


def test_rules_audit_preview_route_renders_edit_rule_impact(client, db_conn):
    """Verify edit preview renders a confirm form without mutating the rule."""
    rule_id = insert_rule(db_conn, keyword="METRO", category="Food")
    set_rule_tags(db_conn, rule_id, ["Grocery"])
    db_conn.execute(
        """
        INSERT INTO transactions (
            tx_date, description, amount, category, category_source,
            category_rule_id, needs_review, fingerprint
        )
        VALUES (
            '2026-01-02', 'Metro Pharmacy', 12.34, 'Food', 'rule',
            ?, 0, 'audit-route-preview-edit'
        )
        """,
        (rule_id,),
    )
    db_conn.commit()

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
    stored_rule = rule_by_id(db_conn, rule_id)

    assert response.status_code == 200
    assert b"Preview editing rule" in response.data
    assert b"Confirm update" in response.data
    assert b"This updates the rule only after reviewing the impact below." in response.data
    assert f'action="/rules/{rule_id}/update"'.encode() in response.data
    assert b'name="confirm_preview" value="1"' in response.data
    assert b'name="category" value="Utilities"' in response.data
    assert b'name="tags" value="Tax"' in response.data
    assert b"Category would change" in response.data
    assert tuple(stored_rule[1:]) == ("METRO", "Food", None, None, "manual", 0)
    assert get_rule_tags_by_rule_id(db_conn, [rule_id])[rule_id] == ["Grocery"]


def test_rules_audit_preview_route_renders_apply_all_impact(client, db_conn):
    """Verify the audit preview route supports apply-all impact without a rule ID."""
    rule_id = insert_rule(db_conn, keyword="METRO", category="Food")
    db_conn.execute(
        """
        INSERT INTO transactions (
            tx_date, description, amount, category, category_source,
            needs_review, fingerprint
        )
        VALUES (
            '2026-01-02', 'Metro Pharmacy', 8.50, 'UNKNOWN', 'unknown',
            1, 'audit-route-preview-apply-all'
        )
        """
    )
    db_conn.commit()

    response = client.post(
        "/rules/audit/preview",
        data={
            CSRF_FIELD_NAME: set_csrf_token(client),
            "action": "apply_all_rules",
        },
    )
    stored = db_conn.execute(
        """
        SELECT category, category_rule_id
        FROM transactions
        WHERE fingerprint = 'audit-route-preview-apply-all'
        """
    ).fetchone()

    assert response.status_code == 200
    assert b"Preview applying all rules" in response.data
    assert b"Confirm apply all" in response.data
    assert b'action="/rules/apply-all"' in response.data
    assert b'name="confirm_preview" value="1"' in response.data
    assert b"Metro Pharmacy" in response.data
    assert f"/rules/audit/rule/{rule_id}".encode() not in response.data
    assert tuple(stored) == ("UNKNOWN", None)


def test_rules_audit_preview_route_marks_impact_tables_paginated_and_sortable(client, db_conn):
    """Verify preview impact tables expose client-side sorting and pagination."""
    set_default_table_page_size(db_conn, 1)
    rule_id = insert_rule(db_conn, keyword="METRO", category="Food")
    set_rule_tags(db_conn, rule_id, ["Grocery"])
    db_conn.execute(
        """
        INSERT INTO transactions (
            tx_date, description, amount, category, category_source,
            category_rule_id, needs_review, fingerprint
        )
        VALUES
            ('2026-01-02', 'Metro Pharmacy B', 12.34, 'Food', 'rule', ?, 0, 'audit-preview-table-b'),
            ('2026-01-03', 'Metro Pharmacy A', 8.50, 'Food', 'rule', ?, 0, 'audit-preview-table-a')
        """,
        (rule_id, rule_id),
    )
    db_conn.commit()

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


def test_rules_audit_rule_route_renders_rule_diagnostics(client, db_conn):
    """Verify the rule detail page shows per-rule audit diagnostics."""
    set_default_table_page_size(db_conn, 1)
    broad_rule_id = insert_rule(db_conn, keyword="METRO", category="Food")
    specific_rule_id = insert_rule(db_conn, keyword="METRO GROCERY", category="Utilities")
    db_conn.execute(
        """
        INSERT INTO transactions (
            tx_date, description, amount, category, category_source,
            category_rule_id, needs_review, fingerprint
        )
        VALUES (
            '2026-01-02', 'Metro Grocery #123', 14.25, 'Utilities', 'rule',
            ?, 0, 'audit-rule-detail'
        )
        """,
        (specific_rule_id,),
    )
    db_conn.commit()

    response = client.get(f"/rules/audit/rule/{broad_rule_id}")

    assert response.status_code == 200
    assert b"Rule detail" in response.data
    assert b"This page is read-only and does not change rule behavior." in response.data
    assert b"Preview delete" in response.data
    assert b"Preview apply where winner" in response.data
    assert b"Preview force apply" in response.data
    assert b"Rule summary" in response.data
    assert b"Historical matches" in response.data
    assert b"Win rate" in response.data
    assert b"Overlapping rules" in response.data
    assert b"Rules shadowing this rule" in response.data
    assert b"data-paginated-table" in response.data
    assert b'data-page-size="1"' in response.data
    assert b'data-pagination-label="Rule interaction pages"' in response.data
    assert b'data-sort-column="1"' in response.data
    assert b'data-sort-type="number"' in response.data
    assert b"METRO GROCERY" in response.data
    assert (
        f"/rules/audit/overlap/{broad_rule_id}/{specific_rule_id}".encode() in response.data
        or f"/rules/audit/overlap/{specific_rule_id}/{broad_rule_id}".encode() in response.data
    )


def test_rules_route_renders_scope_selector_for_merchant_bound_rule(client, db_conn):
    """Verify merchant-bound rules expose a control for switching to fuzzy scope."""
    merchant_id = insert_merchant(db_conn, "COSTCO RENEWAL")
    insert_rule(db_conn, keyword="COSTCO RENEWAL", category="Food", merchant_id=merchant_id)

    response = client.get("/rules")

    assert response.status_code == 200
    assert b"Match scope" in response.data
    assert b"Merchant only" in response.data
    assert b"Fuzzy keyword" in response.data
    assert b"COSTCO RENEWAL" in response.data


def test_rules_modals_render_category_and_tag_description_tooltips(client):
    """Verify rule editor category and tag choices expose taxonomy descriptions."""
    response = client.get("/rules")

    assert response.status_code == 200
    assert b"data-category-description-select" in response.data
    assert b"Food and drink, including groceries" in response.data
    assert b"Marks transactions that may be useful for tax preparation" in response.data


def test_rules_route_filters_suggested_automatic_rules(client, db_conn):
    """Verify the Suggested filter isolates automatic rules that still need approval."""
    insert_rule(db_conn, keyword="AUTO SUGGESTED", category="Food", source="automatic", ai_approved=0)
    insert_rule(db_conn, keyword="AUTO APPROVED", category="Food", source="automatic", ai_approved=1)
    insert_rule(db_conn, keyword="MANUAL RULE", category="Food", source="manual", ai_approved=0)

    response = client.get("/rules?approval=suggested")

    assert response.status_code == 200
    assert b"AUTO SUGGESTED" in response.data
    assert b"AUTO APPROVED" not in response.data
    assert b"MANUAL RULE" not in response.data
    assert b'value="suggested" selected' in response.data


def test_rules_route_filters_approved_automatic_rules(client, db_conn):
    """Verify the Approved filter isolates approved automatic rules."""
    insert_rule(db_conn, keyword="AUTO SUGGESTED", category="Food", source="automatic", ai_approved=0)
    insert_rule(db_conn, keyword="AUTO APPROVED", category="Food", source="automatic", ai_approved=1)
    insert_rule(db_conn, keyword="MANUAL RULE", category="Food", source="manual", ai_approved=0)

    response = client.get("/rules?approval=approved")

    assert response.status_code == 200
    assert b"AUTO APPROVED" in response.data
    assert b"AUTO SUGGESTED" not in response.data
    assert b"MANUAL RULE" not in response.data
    assert b'value="approved" selected' in response.data


def test_rules_route_filters_by_tags(client, db_conn):
    """Verify the rules page can be filtered by attached rule tags."""
    tax_rule_id = insert_rule(db_conn, keyword="METRO TAX", category="Food")
    shared_rule_id = insert_rule(db_conn, keyword="CAFE SHARED", category="Food")
    set_rule_tags(db_conn, tax_rule_id, ["Tax"])
    set_rule_tags(db_conn, shared_rule_id, ["Shared"])
    db_conn.commit()

    response = client.get("/rules?tags=Tax")

    assert response.status_code == 200
    assert b"METRO TAX" in response.data
    assert b"CAFE SHARED" not in response.data


def test_rules_route_filters_by_untagged_rules(client, db_conn):
    """Verify the virtual untagged tag filter finds rules without tags."""
    insert_rule(db_conn, keyword="NO TAG RULE", category="Food")
    tagged_rule_id = insert_rule(db_conn, keyword="WITH TAX RULE", category="Food")
    set_rule_tags(db_conn, tagged_rule_id, ["Tax"])
    db_conn.commit()

    response = client.get(f"/rules?tags={UNTAGGED_TAG_FILTER}")

    assert response.status_code == 200
    assert b"NO TAG RULE" in response.data
    assert b"WITH TAX RULE" not in response.data
    assert b"Untagged" in response.data


def test_rules_route_filters_by_category(client, db_conn):
    """Verify the rules page can be filtered by category."""
    insert_rule(db_conn, keyword="METRO FOOD", category="Food")
    insert_rule(db_conn, keyword="HYDRO UTILITIES", category="Utilities")

    response = client.get("/rules?category=Utilities")

    assert response.status_code == 200
    assert b"HYDRO UTILITIES" in response.data
    assert b"METRO FOOD" not in response.data
    body = response.get_data(as_text=True)
    filter_markup = body[body.index('id="rules-categories-label"'):]
    selected_input = filter_markup[
        filter_markup.index('value="Utilities"'):filter_markup.index('value="Utilities"') + 200
    ]
    assert 'name="categories"' in body
    assert "checked" in selected_input


def test_rules_route_filters_by_source(client, db_conn):
    """Verify the rules page can be filtered by rule source."""
    insert_rule(db_conn, keyword="AUTO RULE", category="Food", source="automatic")
    insert_rule(db_conn, keyword="MANUAL RULE", category="Food", source="manual")

    response = client.get("/rules?source=automatic")

    assert response.status_code == 200
    assert b"AUTO RULE" in response.data
    assert b"MANUAL RULE" not in response.data
    assert b'value="automatic" selected' in response.data
    assert b">Manual<" in response.data
    assert b">Automatic<" in response.data
    assert b">Import<" not in response.data
    assert b">System<" not in response.data


def test_rules_create_route_rejects_invalid_form(client, db_conn):
    """Verify that the create route flashes validation errors without writing."""
    response = client.post(
        "/rules/create",
        data={
            CSRF_FIELD_NAME: set_csrf_token(client),
            "confirm_preview": "1",
            "keyword": "",
            "category": "Food",
        },
        follow_redirects=True,
    )

    count = db_conn.execute("SELECT COUNT(*) AS count FROM category_rules").fetchone()["count"]
    assert response.status_code == 200
    assert b"Keyword and category are required." in response.data
    assert count == 0


def test_rules_update_route_replaces_rule_values_and_tags(client, db_conn):
    """Verify that the update route changes rule fields and associated tags."""
    rule_id = insert_rule(db_conn, keyword="OLD STORE", category="Food")

    response = client.post(
        f"/rules/{rule_id}/update",
        data={
            CSRF_FIELD_NAME: set_csrf_token(client),
            "confirm_preview": "1",
            "keyword": "Hydro Quebec",
            "category": "Utilities",
            "tags": ["Government", "Tax"],
            "amount_min": "25",
            "amount_max": "50",
        },
        follow_redirects=True,
    )

    rule = rule_by_id(db_conn, rule_id)
    assert response.status_code == 200
    assert b"Rule updated." in response.data
    assert tuple(rule[1:]) == ("HYDRO QUEBEC", "Utilities", 25.0, 50.0, "manual", 0)
    assert get_rule_tags_by_rule_id(db_conn, [rule_id])[rule_id] == ["Government", "Tax"]


def test_rules_update_route_requires_preview_confirmation(client, db_conn):
    """Verify direct rule updates are blocked until preview confirmation."""
    rule_id = insert_rule(db_conn, keyword="OLD STORE", category="Food")

    response = client.post(
        f"/rules/{rule_id}/update",
        data={
            CSRF_FIELD_NAME: set_csrf_token(client),
            "keyword": "Hydro Quebec",
            "category": "Utilities",
        },
        follow_redirects=True,
    )

    rule = rule_by_id(db_conn, rule_id)
    assert response.status_code == 200
    assert b"Preview changes before updating a rule." in response.data
    assert tuple(rule[1:]) == ("OLD STORE", "Food", None, None, "manual", 0)


def test_rules_update_route_can_change_merchant_bound_rule_to_fuzzy(client, db_conn):
    """Verify posting an empty merchant scope clears merchant-bound matching."""
    merchant_id = insert_merchant(db_conn, "COSTCO RENEWAL")
    rule_id = insert_rule(db_conn, keyword="COSTCO RENEWAL", category="Food", merchant_id=merchant_id)

    response = client.post(
        f"/rules/{rule_id}/update",
        data={
            CSRF_FIELD_NAME: set_csrf_token(client),
            "confirm_preview": "1",
            "keyword": "Costco Renewal",
            "merchant_id": "",
            "category": "Food",
        },
        follow_redirects=True,
    )

    rule = db_conn.execute(
        """
        SELECT merchant_id, keyword, category
        FROM category_rules
        WHERE id = ?
        """,
        (rule_id,),
    ).fetchone()
    assert response.status_code == 200
    assert b"Rule updated." in response.data
    assert tuple(rule) == (None, "COSTCO RENEWAL", "Food")


def test_rules_update_route_approves_automatic_rule_without_changing_source(client, db_conn):
    """Verify editing an automatic rule preserves provenance and marks it approved."""
    rule_id = insert_rule(db_conn, keyword="AUTO STORE", category="Food", source="automatic")

    response = client.post(
        f"/rules/{rule_id}/update",
        data={
            CSRF_FIELD_NAME: set_csrf_token(client),
            "confirm_preview": "1",
            "keyword": "Auto Store",
            "category": "Utilities",
        },
        follow_redirects=True,
    )

    rule = rule_by_id(db_conn, rule_id)
    assert response.status_code == 200
    assert b"Rule updated." in response.data
    assert tuple(rule[1:]) == ("AUTO STORE", "Utilities", None, None, "automatic", 1)


def test_rules_approve_route_marks_automatic_rule_approved(client, db_conn):
    """Verify the approve route marks only automatic rules approved."""
    rule_id = insert_rule(db_conn, keyword="AUTO STORE", category="Food", source="automatic")

    response = client.post(
        f"/rules/{rule_id}/approve",
        data={
            CSRF_FIELD_NAME: set_csrf_token(client),
            "confirm_preview": "1",
        },
        follow_redirects=True,
    )

    rule = rule_by_id(db_conn, rule_id)
    assert response.status_code == 200
    assert b"Rule approved: AUTO STORE" in response.data
    assert rule["source"] == "automatic"
    assert rule["ai_approved"] == 1


def test_rules_approve_route_requires_preview_confirmation(client, db_conn):
    """Verify direct approval is blocked until preview confirmation."""
    rule_id = insert_rule(db_conn, keyword="AUTO STORE", category="Food", source="automatic")

    response = client.post(
        f"/rules/{rule_id}/approve",
        data={CSRF_FIELD_NAME: set_csrf_token(client)},
        follow_redirects=True,
    )

    rule = rule_by_id(db_conn, rule_id)
    assert response.status_code == 200
    assert b"Preview approval before approving a rule." in response.data
    assert rule["ai_approved"] == 0


def test_rules_approve_route_returns_json_for_table_action(client, db_conn):
    """Verify AJAX approval returns a payload without redirecting."""
    rule_id = insert_rule(db_conn, keyword="AUTO STORE", category="Food", source="automatic")

    response = client.post(
        f"/rules/{rule_id}/approve",
        data={
            CSRF_FIELD_NAME: set_csrf_token(client),
            "confirm_preview": "1",
        },
        headers={"X-Requested-With": "fetch"},
    )

    payload = response.get_json()
    assert response.status_code == 200
    assert payload["ok"] is True
    assert payload["action"] == "approve"
    assert payload["rule_id"] == rule_id
    assert payload["approval_label"] == "Approved"
    assert rule_by_id(db_conn, rule_id)["ai_approved"] == 1


def test_rules_approve_route_rejects_manual_rule(client, db_conn):
    """Verify approval is limited to automatic rules."""
    rule_id = insert_rule(db_conn, keyword="MANUAL STORE", category="Food")

    response = client.post(
        f"/rules/{rule_id}/approve",
        data={
            CSRF_FIELD_NAME: set_csrf_token(client),
            "confirm_preview": "1",
        },
        follow_redirects=True,
    )

    rule = rule_by_id(db_conn, rule_id)
    assert response.status_code == 200
    assert b"Only automatic rules can be approved." in response.data
    assert rule["source"] == "manual"
    assert rule["ai_approved"] == 0


def test_rules_apply_route_returns_json_for_table_action(client, db_conn):
    """Verify confirmed AJAX apply returns updated counts without refreshing the page."""
    rule_id = insert_rule(db_conn, keyword="METRO", category="Food")
    db_conn.execute(
        """
        INSERT INTO transactions (tx_date, description, amount, category, needs_review, fingerprint)
        VALUES ('2026-01-02', 'Metro Grocery #123', 14.25, 'UNKNOWN', 1, 'ajax-apply-match')
        """
    )
    db_conn.commit()

    response = client.post(
        f"/rules/{rule_id}/apply",
        data={
            CSRF_FIELD_NAME: set_csrf_token(client),
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


def test_rules_apply_route_rejects_unconfirmed_json_apply(client, db_conn):
    """Verify AJAX apply also requires preview confirmation."""
    rule_id = insert_rule(db_conn, keyword="METRO", category="Food")

    response = client.post(
        f"/rules/{rule_id}/apply",
        data={CSRF_FIELD_NAME: set_csrf_token(client)},
        headers={"X-Requested-With": "fetch"},
    )

    assert response.status_code == 400
    assert response.get_json() == {"ok": False, "message": "Preview apply before applying a rule."}


def test_rules_preview_route_returns_match_count_and_sample(client, db_conn):
    """Verify that preview returns matching transactions without mutating data."""
    db_conn.execute(
        """
        INSERT INTO transactions (tx_date, description, amount, category, needs_review, fingerprint)
        VALUES ('2026-01-02', 'Metro Grocery #123', 14.25, 'UNKNOWN', 1, 'preview-match')
        """
    )
    db_conn.execute(
        """
        INSERT INTO transactions (tx_date, description, amount, category, needs_review, fingerprint)
        VALUES ('2026-01-03', 'Other Store', 14.25, 'UNKNOWN', 1, 'preview-miss')
        """
    )
    db_conn.commit()

    response = client.post(
        "/rules/preview",
        data={
            CSRF_FIELD_NAME: set_csrf_token(client),
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


def test_rules_preview_route_returns_validation_error_json(client):
    """Verify that invalid previews return JSON validation errors."""
    response = client.post(
        "/rules/preview",
        data={
            CSRF_FIELD_NAME: set_csrf_token(client),
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


def test_rules_delete_route_requires_preview_confirmation(client, db_conn):
    """Verify direct rule deletion is blocked until preview confirmation."""
    rule_id = insert_rule(db_conn)

    response = client.post(
        f"/rules/{rule_id}/delete",
        data={CSRF_FIELD_NAME: set_csrf_token(client)},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"Preview deletion before deleting a rule." in response.data
    assert rule_by_id(db_conn, rule_id) is not None


def test_rules_delete_route_removes_rule_after_preview_confirmation(client, db_conn):
    """Verify confirmed deletion removes an existing category rule."""
    rule_id = insert_rule(db_conn)

    response = client.post(
        f"/rules/{rule_id}/delete",
        data={
            CSRF_FIELD_NAME: set_csrf_token(client),
            "confirm_preview": "1",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"Rule deleted." in response.data
    assert rule_by_id(db_conn, rule_id) is None


def test_rules_delete_route_rejects_unconfirmed_json_delete(client, db_conn):
    """Verify AJAX delete also requires preview confirmation."""
    rule_id = insert_rule(db_conn)

    response = client.post(
        f"/rules/{rule_id}/delete",
        data={CSRF_FIELD_NAME: set_csrf_token(client)},
        headers={"X-Requested-With": "fetch"},
    )

    payload = response.get_json()
    assert response.status_code == 400
    assert payload == {"ok": False, "message": "Preview deletion before deleting a rule."}
    assert rule_by_id(db_conn, rule_id) is not None


def test_rules_export_route_returns_csv(client, db_conn):
    """Verify that rule exports return CSV content with persisted rules."""
    insert_rule(db_conn, keyword="HYDRO QUEBEC", category="Utilities", amount_min=25, amount_max=50)

    response = client.get("/rules/export.csv")
    rows = list(csv.DictReader(io.StringIO(response.get_data(as_text=True))))

    assert response.status_code == 200
    assert response.mimetype == "text/csv"
    assert "category-rules.csv" in response.headers["Content-Disposition"]
    assert rows[0]["keyword"] == "HYDRO QUEBEC"
    assert rows[0]["merchant_name"] == ""
    assert rows[0]["category"] == "Utilities"
    assert rows[0]["amount_min"] == "25.0"
    assert rows[0]["amount_max"] == "50.0"


def test_rules_import_route_rejects_invalid_mode_missing_file_and_bad_file_type(client):
    """Verify import route validation before background job submission."""
    token = set_csrf_token(client)

    invalid_mode = client.post(
        "/rules/import",
        data={
            CSRF_FIELD_NAME: token,
            "mode": "replace-everything",
            "rules_file": (io.BytesIO(b"keyword,category\nMetro,Food\n"), "rules.csv"),
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    missing_file = client.post(
        "/rules/import",
        data={CSRF_FIELD_NAME: token, "mode": "add"},
        follow_redirects=True,
    )
    wrong_type = client.post(
        "/rules/import",
        data={
            CSRF_FIELD_NAME: token,
            "mode": "add",
            "rules_file": (io.BytesIO(b"keyword,category\nMetro,Food\n"), "rules.txt"),
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    empty_file = client.post(
        "/rules/import",
        data={
            CSRF_FIELD_NAME: token,
            "mode": "add",
            "rules_file": (io.BytesIO(b"  \n"), "rules.csv"),
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )

    assert b"Choose whether to add new rules or override existing rules." in invalid_mode.data
    assert b"Choose a CSV file to import." in missing_file.data
    assert b"Rules import currently supports CSV files." in wrong_type.data
    assert b"The selected rules file is empty." in empty_file.data


def test_rules_import_route_previews_then_queues_background_job(client, monkeypatch):
    """Verify that valid imports require preview confirmation before queueing."""
    submitted_jobs = []

    def capture_job(label, func, *args, undo_handler=None, undo_args=None, **kwargs):
        """Capture background import job metadata."""
        submitted_jobs.append(
            {
                "label": label,
                "func": func,
                "args": args,
                "undo_handler": undo_handler,
                "undo_args": undo_args,
                "kwargs": kwargs,
            }
        )
        return "rulesjob123"

    monkeypatch.setattr(rules_controller, "submit_background_job", capture_job)

    preview = client.post(
        "/rules/import",
        data={
            CSRF_FIELD_NAME: set_csrf_token(client),
            "mode": "add",
            "rules_file": (io.BytesIO(b"keyword,category\nMetro,Food\n"), "rules.csv"),
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )

    assert preview.status_code == 200
    assert b"Rule import preview" in preview.data
    assert b"Confirm import" in preview.data
    assert b"name=\"confirm_preview\" value=\"1\"" in preview.data
    assert b"data-paginated-table" in preview.data
    assert b"data-pagination-label=\"Imported rule pages\"" in preview.data
    assert b"data-sort-column=\"0\" data-sort-type=\"text\"" in preview.data
    assert b"METRO" in preview.data
    assert submitted_jobs == []

    response = client.post(
        "/rules/import",
        data={
            CSRF_FIELD_NAME: set_csrf_token(client),
            "confirm_preview": "1",
            "mode": "add",
            "filename": "rules.csv",
            "raw_text": "keyword,category\nMetro,Food\n",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"Rules import queued in the background." in response.data
    assert len(submitted_jobs) == 1
    submitted = submitted_jobs[0]
    assert submitted["label"] == "Import rules from rules.csv"
    assert submitted["func"] is import_rules_job
    assert submitted["args"][0] == "keyword,category\nMetro,Food\n"
    assert submitted["args"][1] == "add"
    assert isinstance(submitted["args"][2], dict)
    assert submitted["undo_handler"] is undo_import_rules_job
    assert submitted["undo_args"] == (submitted["args"][2],)


def test_rules_import_route_rejects_malformed_csv_before_queueing(client, monkeypatch):
    """Verify malformed CSV payloads fail during import preview."""
    submitted_jobs = []
    monkeypatch.setattr(
        rules_controller,
        "submit_background_job",
        lambda label, func, *args, **kwargs: submitted_jobs.append((label, func, args, kwargs)) or "badcsvjob",
    )

    response = client.post(
        "/rules/import",
        data={
            CSRF_FIELD_NAME: set_csrf_token(client),
            "mode": "add",
            "rules_file": (io.BytesIO(b"keyword,category\n,Food\n"), "bad.csv"),
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"Row 2: keyword or merchant_name is required." in response.data
    assert submitted_jobs == []
