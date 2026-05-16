"""Route tests for the rules feature."""

import csv
import io

import pytest

from finance_app.core.csrf import CSRF_FIELD_NAME, CSRF_SESSION_KEY
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


def test_rules_create_route_persists_rule_and_tags(client, db_conn):
    """Verify that the create route stores a manual rule."""
    response = client.post(
        "/rules/create",
        data={
            CSRF_FIELD_NAME: set_csrf_token(client),
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


def test_rules_route_filters_by_category(client, db_conn):
    """Verify the rules page can be filtered by category."""
    insert_rule(db_conn, keyword="METRO FOOD", category="Food")
    insert_rule(db_conn, keyword="HYDRO UTILITIES", category="Utilities")

    response = client.get("/rules?category=Utilities")

    assert response.status_code == 200
    assert b"HYDRO UTILITIES" in response.data
    assert b"METRO FOOD" not in response.data
    assert b'value="Utilities" selected' in response.data


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
    assert b">Default<" in response.data
    assert b">Import<" not in response.data
    assert b">System<" not in response.data


def test_rules_create_route_rejects_invalid_form(client, db_conn):
    """Verify that the create route flashes validation errors without writing."""
    response = client.post(
        "/rules/create",
        data={
            CSRF_FIELD_NAME: set_csrf_token(client),
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


def test_rules_update_route_can_change_merchant_bound_rule_to_fuzzy(client, db_conn):
    """Verify posting an empty merchant scope clears merchant-bound matching."""
    merchant_id = insert_merchant(db_conn, "COSTCO RENEWAL")
    rule_id = insert_rule(db_conn, keyword="COSTCO RENEWAL", category="Food", merchant_id=merchant_id)

    response = client.post(
        f"/rules/{rule_id}/update",
        data={
            CSRF_FIELD_NAME: set_csrf_token(client),
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
        data={CSRF_FIELD_NAME: set_csrf_token(client)},
        follow_redirects=True,
    )

    rule = rule_by_id(db_conn, rule_id)
    assert response.status_code == 200
    assert b"Rule approved: AUTO STORE" in response.data
    assert rule["source"] == "automatic"
    assert rule["ai_approved"] == 1


def test_rules_approve_route_returns_json_for_table_action(client, db_conn):
    """Verify AJAX approval returns a payload without redirecting."""
    rule_id = insert_rule(db_conn, keyword="AUTO STORE", category="Food", source="automatic")

    response = client.post(
        f"/rules/{rule_id}/approve",
        data={CSRF_FIELD_NAME: set_csrf_token(client)},
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
        data={CSRF_FIELD_NAME: set_csrf_token(client)},
        follow_redirects=True,
    )

    rule = rule_by_id(db_conn, rule_id)
    assert response.status_code == 200
    assert b"Only automatic rules can be approved." in response.data
    assert rule["source"] == "manual"
    assert rule["ai_approved"] == 0


def test_rules_apply_route_returns_json_for_table_action(client, db_conn):
    """Verify AJAX apply returns updated counts without refreshing the page."""
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
        data={CSRF_FIELD_NAME: set_csrf_token(client)},
        headers={"X-Requested-With": "fetch"},
    )

    payload = response.get_json()
    assert response.status_code == 200
    assert payload["ok"] is True
    assert payload["action"] == "apply"
    assert payload["rule_id"] == rule_id
    assert payload["updated_count"] == 1


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


def test_rules_delete_route_removes_rule(client, db_conn):
    """Verify that the delete route removes an existing category rule."""
    rule_id = insert_rule(db_conn)

    response = client.post(
        f"/rules/{rule_id}/delete",
        data={CSRF_FIELD_NAME: set_csrf_token(client)},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"Rule deleted." in response.data
    assert rule_by_id(db_conn, rule_id) is None


def test_rules_delete_route_returns_json_for_table_action(client, db_conn):
    """Verify AJAX delete returns a payload the table can apply in place."""
    rule_id = insert_rule(db_conn)

    response = client.post(
        f"/rules/{rule_id}/delete",
        data={CSRF_FIELD_NAME: set_csrf_token(client)},
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
    assert rule_by_id(db_conn, rule_id) is None


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


def test_rules_import_route_queues_background_job(client, monkeypatch):
    """Verify that valid imports are queued with undo metadata."""
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

    response = client.post(
        "/rules/import",
        data={
            CSRF_FIELD_NAME: set_csrf_token(client),
            "mode": "add",
            "rules_file": (io.BytesIO(b"keyword,category\nMetro,Food\n"), "rules.csv"),
        },
        content_type="multipart/form-data",
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


def test_rules_import_route_queues_malformed_csv_for_background_failure(client, monkeypatch):
    """Verify that malformed CSV payloads fail in the import job, not the route."""
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

    _, func, args, _ = submitted_jobs[0]
    assert response.status_code == 200
    assert b"Rules import queued in the background." in response.data
    with pytest.raises(ValueError, match="Row 2: keyword or merchant_name is required."):
        func(*args)
