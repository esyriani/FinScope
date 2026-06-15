"""Route tests for transaction mutation endpoints."""

from sqlalchemy import text
from tests.support.database import insert_transaction as insert_test_transaction
from tests.support.database import set_owner_setting
from tests.support.html import assert_has_element, assert_visible_text
from tests.support.web import set_csrf_token

from finance_app.core.csrf import CSRF_FIELD_NAME
from finance_app.modules.categories.taxonomy import get_rule_tags_by_rule_id, get_transaction_tag_names


def insert_route_transaction(conn, fingerprint="route-tx", category="UNKNOWN", needs_review=1):
    """Insert a transaction and return its id."""
    return insert_test_transaction(
        conn,
        description="Metro Grocery #123",
        amount=12.34,
        category=category,
        needs_review=needs_review,
        fingerprint=fingerprint,
        merchant_from_description=True,
    )


def transaction_state(conn, tx_id):
    """Return selected transaction state."""
    return (
        conn.execute(
            text("""
        SELECT category, needs_review, category_source, category_confidence,
               category_rule_id, categorized_at, reviewed_at, ignored
        FROM transactions
        WHERE id = :p0
        """),
            {"p0": tx_id},
        )
        .mappings()
        .fetchone()
    )


def test_transactions_table_exports_category_method_and_score_separately(client, core_conn):
    """Verify transaction category cells export category, method, and score fields."""
    insert_test_transaction(
        core_conn,
        description="AI matched cafe",
        amount=12.34,
        category="Food",
        category_source="ai",
        category_confidence=0.96,
        needs_review=0,
        fingerprint="route-tx-export-category-parts",
    )

    response = client.get("/transactions?period=all")

    assert response.status_code == 200
    assert_has_element(
        response, "span", attrs={"class": "transaction-category-name", "data-export-part": True}, text="Food"
    )
    assert_has_element(
        response,
        "span",
        attrs={
            "data-export-part": True,
            "data-export-label": "Method",
            "data-export-header": "Method",
        },
        text="AI",
    )
    assert_has_element(
        response,
        "span",
        attrs={
            "data-export-part": True,
            "data-export-label": "Score",
            "data-export-header": "Score",
            "data-export-type": "percent",
            "data-export-value": "0.96",
        },
        text="96%",
    )


def test_update_transaction_category_route_saves_manual_category_rule_and_tags(client, core_conn):
    """Verify category route updates transaction and saves an optional rule."""
    tx_id = insert_route_transaction(core_conn)

    response = client.post(
        f"/transactions/{tx_id}/category",
        data={
            CSRF_FIELD_NAME: set_csrf_token(client),
            "category": "Food",
            "tags": ["Tax"],
            "rule_action": "save",
            "keyword": "Metro Grocery",
            "amount_min": "10",
            "amount_max": "20",
        },
        follow_redirects=True,
    )

    tx = transaction_state(core_conn, tx_id)
    rule = core_conn.execute(text("""
        SELECT id, merchant_id, keyword, category, amount_min, amount_max, source
        FROM category_rules
        WHERE keyword = 'METRO GROCERY'
        """)).fetchone()
    assert response.status_code == 200
    assert_visible_text(response, "Category updated. Rule saved for: METRO GROCERY from 10.00 to 20.00")
    assert tx["category"] == "Food"
    assert tx["needs_review"] == 0
    assert tx["category_source"] == "manual"
    assert tx["category_confidence"] == 1.0
    assert tx["category_rule_id"] is None
    assert tx["categorized_at"] is not None
    assert tx["reviewed_at"] is not None
    assert get_transaction_tag_names(core_conn, tx_id) == ["Tax"]
    assert rule._mapping["merchant_id"] is not None
    assert tuple(rule[2:]) == ("METRO GROCERY", "Food", 10.0, 20.0, "manual")
    assert get_rule_tags_by_rule_id(core_conn, [rule._mapping["id"]])[rule._mapping["id"]] == ["Tax"]


def test_update_transaction_category_route_can_update_transaction_only(client, core_conn):
    """Verify category route can update one transaction without creating a rule."""
    tx_id = insert_route_transaction(core_conn, fingerprint="route-tx-only")

    response = client.post(
        f"/transactions/{tx_id}/category",
        data={
            CSRF_FIELD_NAME: set_csrf_token(client),
            "category": "Food",
            "rule_action": "transaction_only",
        },
        follow_redirects=True,
    )

    rule_count = core_conn.execute(text("SELECT COUNT(*) AS count FROM category_rules")).fetchone()._mapping["count"]
    assert response.status_code == 200
    assert_visible_text(response, "Category updated for this transaction only.")
    assert transaction_state(core_conn, tx_id)["category"] == "Food"
    assert rule_count == 0


def test_update_transaction_category_route_does_not_verify_unchanged_transaction(client, core_conn):
    """Verify unchanged category submissions do not mark a transaction verified."""
    tx_id = insert_route_transaction(
        core_conn,
        fingerprint="route-tx-unchanged",
        category="Food",
        needs_review=0,
    )

    response = client.post(
        f"/transactions/{tx_id}/category",
        data={
            CSRF_FIELD_NAME: set_csrf_token(client),
            "category": "Food",
            "rule_action": "transaction_only",
        },
        follow_redirects=True,
    )

    tx = transaction_state(core_conn, tx_id)
    assert response.status_code == 200
    assert_visible_text(response, "No transaction changes to save.")
    assert tx["category"] == "Food"
    assert tx["needs_review"] == 0
    assert tx["reviewed_at"] is None


def test_update_transaction_category_route_approves_unchanged_transaction_when_saving_rule(client, core_conn):
    """Verify saving a rule counts as explicit approval for the current transaction."""
    tx_id = insert_route_transaction(
        core_conn,
        fingerprint="route-tx-unchanged-rule",
        category="Food",
        needs_review=0,
    )

    response = client.post(
        f"/transactions/{tx_id}/category",
        data={
            CSRF_FIELD_NAME: set_csrf_token(client),
            "category": "Food",
            "rule_action": "save",
            "keyword": "Metro Grocery",
            "amount_min": "12.34",
            "amount_max": "12.34",
        },
        follow_redirects=True,
    )

    tx = transaction_state(core_conn, tx_id)
    rule = core_conn.execute(text("""
        SELECT keyword, category, amount_min, amount_max
        FROM category_rules
        WHERE keyword = 'METRO GROCERY'
        """)).fetchone()
    assert response.status_code == 200
    assert_visible_text(response, "Rule saved for: METRO GROCERY at amount 12.34")
    assert tx["category"] == "Food"
    assert tx["needs_review"] == 0
    assert tx["reviewed_at"] is not None
    assert tuple(rule) == ("METRO GROCERY", "Food", 12.34, 12.34)


def test_update_transaction_category_route_validates_missing_transaction_and_amounts(client, core_conn):
    """Verify category route handles missing rows and invalid amount bounds."""
    tx_id = insert_route_transaction(core_conn, fingerprint="route-invalid-amount")
    token = set_csrf_token(client)

    missing = client.post(
        "/transactions/9999/category",
        data={
            CSRF_FIELD_NAME: token,
            "category": "Food",
            "rule_action": "transaction_only",
        },
        follow_redirects=True,
    )
    invalid_amount = client.post(
        f"/transactions/{tx_id}/category",
        data={
            CSRF_FIELD_NAME: token,
            "category": "Food",
            "rule_action": "save",
            "keyword": "Metro",
            "amount_min": "abc",
        },
        follow_redirects=True,
    )

    assert_visible_text(missing, "Transaction not found.")
    assert_visible_text(invalid_amount, "Amount bounds must be valid numbers.")
    assert transaction_state(core_conn, tx_id)["category"] == "UNKNOWN"


def test_verify_transaction_route_marks_transaction_reviewed(client, core_conn):
    """Verify verify route marks a transaction as no longer needing review."""
    tx_id = insert_route_transaction(core_conn, fingerprint="route-verify")

    response = client.post(
        f"/transactions/{tx_id}/verify",
        data={CSRF_FIELD_NAME: set_csrf_token(client)},
        follow_redirects=True,
    )

    tx = transaction_state(core_conn, tx_id)
    assert response.status_code == 200
    assert_visible_text(response, "Transaction approved.")
    assert tx["needs_review"] == 0
    assert tx["reviewed_at"] is not None


def test_verify_transaction_route_reports_missing_transaction(client):
    """Verify verify route reports missing transaction ids."""
    response = client.post(
        "/transactions/9999/verify",
        data={CSRF_FIELD_NAME: set_csrf_token(client)},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert_visible_text(response, "Transaction not found.")


def test_update_transaction_ignored_route_ignores_and_restores(client, core_conn):
    """Verify ignored route toggles ignored state and review status."""
    tx_id = insert_route_transaction(core_conn, fingerprint="route-ignore")
    token = set_csrf_token(client)

    ignored = client.post(
        f"/transactions/{tx_id}/ignored",
        data={CSRF_FIELD_NAME: token, "ignored": "1"},
        follow_redirects=True,
    )
    restored = client.post(
        f"/transactions/{tx_id}/ignored",
        data={CSRF_FIELD_NAME: token, "ignored": "0"},
        follow_redirects=True,
    )

    tx = transaction_state(core_conn, tx_id)
    assert_visible_text(ignored, "Transaction ignored.")
    assert_visible_text(restored, "Transaction restored.")
    assert tx["ignored"] == 0
    assert tx["needs_review"] == 0


def test_update_transaction_ignored_route_reports_missing_transaction(client):
    """Verify ignored route reports missing transaction ids."""
    response = client.post(
        "/transactions/9999/ignored",
        data={CSRF_FIELD_NAME: set_csrf_token(client), "ignored": "1"},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert_visible_text(response, "Transaction not found.")


def test_batch_transactions_route_approves_only_selected_ids(client, core_conn):
    """Verify batch approval mutates only explicitly selected transactions."""
    first_id = insert_route_transaction(core_conn, fingerprint="route-batch-approve-1")
    second_id = insert_route_transaction(core_conn, fingerprint="route-batch-approve-2")
    other_id = insert_route_transaction(core_conn, fingerprint="route-batch-approve-other")

    response = client.post(
        "/transactions/batch",
        data={
            CSRF_FIELD_NAME: set_csrf_token(client),
            "transaction_ids": [str(first_id), str(second_id), str(second_id), "bad"],
            "batch_action": "approve",
        },
        follow_redirects=True,
    )

    first = transaction_state(core_conn, first_id)
    second = transaction_state(core_conn, second_id)
    other = transaction_state(core_conn, other_id)
    assert response.status_code == 200
    assert_visible_text(response, "Approved selected transactions.")
    assert first["needs_review"] == 0
    assert first["reviewed_at"] is not None
    assert second["needs_review"] == 0
    assert second["reviewed_at"] is not None
    assert other["needs_review"] == 1
    assert other["reviewed_at"] is None


def test_batch_transactions_route_ignores_only_selected_ids(client, core_conn):
    """Verify batch ignore mutates only explicitly selected transactions."""
    ignored_id = insert_route_transaction(core_conn, fingerprint="route-batch-ignore")
    other_id = insert_route_transaction(core_conn, fingerprint="route-batch-ignore-other")

    response = client.post(
        "/transactions/batch",
        data={
            CSRF_FIELD_NAME: set_csrf_token(client),
            "transaction_ids": [str(ignored_id)],
            "batch_action": "ignore",
        },
        follow_redirects=True,
    )

    ignored = transaction_state(core_conn, ignored_id)
    other = transaction_state(core_conn, other_id)
    assert response.status_code == 200
    assert_visible_text(response, "Ignored selected transaction.")
    assert ignored["ignored"] == 1
    assert ignored["needs_review"] == 0
    assert other["ignored"] == 0
    assert other["needs_review"] == 1


def test_batch_transactions_route_queues_selected_recategorization(client, monkeypatch):
    """Verify batch recategorization queues a job for the selected IDs only."""
    from finance_app.modules.transactions import controller as transaction_controller

    captured = {}

    def queue_for_test(transaction_ids):
        """Capture the transaction IDs submitted to the queue helper."""
        captured["transaction_ids"] = list(transaction_ids)
        return {"selected_count": 2, "job_id": "abcdef123456"}

    monkeypatch.setattr(
        transaction_controller.transactions_service,
        "queue_selected_transaction_recategorization",
        queue_for_test,
    )

    response = client.post(
        "/transactions/batch",
        data={
            CSRF_FIELD_NAME: set_csrf_token(client),
            "transaction_ids": ["11", "22"],
            "batch_action": "recategorize",
            "ai_token_estimate_confirmed": "1",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert captured["transaction_ids"] == ["11", "22"]
    assert_visible_text(response, "Recategorization queued for 2 selected transactions. Job: abcdef12")


def test_batch_recategorization_requires_token_estimate_confirmation(client, monkeypatch):
    """Verify selected recategorization does not queue without estimate confirmation."""
    from finance_app.modules.transactions import controller as transaction_controller

    captured = []

    def queue_for_test(transaction_ids):
        """Capture accidental recategorization queue requests."""
        captured.append(list(transaction_ids))
        return {"selected_count": 2, "job_id": "abcdef123456"}

    monkeypatch.setattr(
        transaction_controller.transactions_service,
        "queue_selected_transaction_recategorization",
        queue_for_test,
    )

    response = client.post(
        "/transactions/batch",
        data={
            CSRF_FIELD_NAME: set_csrf_token(client),
            "transaction_ids": ["11", "22"],
            "batch_action": "recategorize",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert captured == []
    assert_visible_text(response, "Review the token estimate before running AI.")


def test_batch_recategorization_runs_without_confirmation_when_setting_disabled(client, core_conn, monkeypatch):
    """Verify selected recategorization can proceed without modal confirmation when disabled."""
    from finance_app.modules.transactions import controller as transaction_controller

    set_owner_setting(core_conn, "confirm_ai_token_usage_enabled", "0")
    captured = []

    def queue_for_test(transaction_ids):
        """Capture the recategorization queue request."""
        captured.append(list(transaction_ids))
        return {"selected_count": 2, "job_id": "abcdef123456"}

    monkeypatch.setattr(
        transaction_controller.transactions_service,
        "queue_selected_transaction_recategorization",
        queue_for_test,
    )

    response = client.post(
        "/transactions/batch",
        data={
            CSRF_FIELD_NAME: set_csrf_token(client),
            "transaction_ids": ["11", "22"],
            "batch_action": "recategorize",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert captured == [["11", "22"]]
    assert_visible_text(response, "Recategorization queued for 2 selected transactions. Job: abcdef12")


def test_estimate_batch_transaction_ai_route_returns_json(client, monkeypatch):
    """Verify selected recategorization estimates return JSON."""
    from finance_app.modules.transactions import controller as transaction_controller

    captured = {}

    def estimate_for_test(transaction_ids):
        """Capture selected ids and return a deterministic estimate."""
        captured["transaction_ids"] = list(transaction_ids)
        return {
            "ok": True,
            "message": "AI token estimate ready.",
            "estimate": {"request_count": 2, "input_tokens": 123, "total_tokens": 456},
        }

    monkeypatch.setattr(
        transaction_controller.transactions_service,
        "estimate_selected_transaction_recategorization",
        estimate_for_test,
    )

    response = client.post(
        "/transactions/batch/ai-estimate",
        data={
            CSRF_FIELD_NAME: set_csrf_token(client),
            "transaction_ids": ["11", "22"],
        },
    )

    assert response.status_code == 200
    assert captured["transaction_ids"] == ["11", "22"]
    assert response.get_json()["estimate"]["input_tokens"] == 123
    assert response.get_json()["message"] == "AI token estimate ready."


def test_batch_transactions_route_requires_selection(client):
    """Verify batch actions reject empty selections before mutating anything."""
    response = client.post(
        "/transactions/batch",
        data={
            CSRF_FIELD_NAME: set_csrf_token(client),
            "batch_action": "approve",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert_visible_text(response, "Select at least one transaction.")


def test_suggest_transaction_category_route_shows_result_modal(client, core_conn, monkeypatch):
    """Verify the one-off AI route redirects back with suggestion modal content."""
    from finance_app.modules.transactions import controller as transaction_controller

    tx_id = insert_route_transaction(core_conn, fingerprint="route-suggest-ai")

    def suggest_ai_for_test(transaction_id):
        """Return deterministic modal content without calling OpenAI."""
        assert transaction_id == tx_id
        return {
            "ok": True,
            "applied": False,
            "can_apply": True,
            "message": "AI suggestion ready.",
            "transaction_id": tx_id,
            "description": "TVA SPORTS DIRECT",
            "account_name": "TD Visa",
            "tx_date": "2026-05-04",
            "amount": 20.68,
            "transaction_kind_label": "Expense",
            "previous_category": "UNKNOWN",
            "previous_tag_pills": [],
            "category": "Entertainment",
            "tags": ["Service"],
            "tag_pills": [{"name": "Service", "color": "#64748b"}],
            "needs_review": False,
            "review_required": False,
            "category_source_label": "AI",
            "category_source_badge_class": "text-bg-info",
            "category_confidence_label": "96%",
            "model": "gpt-test",
            "request_status": {"status": "ok", "requested_count": 1, "result_count": 1},
            "request_status_label": "ok",
            "llm_confidence": 0.96,
            "final_confidence": 0.96,
            "supported_by_similar_transactions": False,
            "llm_reason": "TVA Sports is a streaming sports service.",
            "rule_evidence": {},
            "retrieval_evidence": {},
            "metadata_pretty": '{"final_category":"Entertainment"}',
            "rule_keyword": "TVA SPORTS DIRECT",
            "rule_exact_amount": "20.68",
            "persistence": {"category": "Entertainment"},
        }

    monkeypatch.setattr(
        transaction_controller.transactions_service,
        "suggest_transaction_ai_category",
        suggest_ai_for_test,
    )

    response = client.post(
        f"/transactions/{tx_id}/suggest-category",
        data={
            CSRF_FIELD_NAME: set_csrf_token(client),
            "ai_token_estimate_confirmed": "1",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert_visible_text(
        response,
        "AI category suggestion",
        "TVA SPORTS DIRECT",
        "Entertainment",
        "TVA Sports is a streaming sports service.",
        "Apply to transaction",
        "Apply and create rule",
    )


def test_suggest_transaction_category_requires_token_estimate_confirmation(client, monkeypatch):
    """Verify one-off AI suggestions do not run without estimate confirmation."""
    from finance_app.modules.transactions import controller as transaction_controller

    captured = []

    def suggest_ai_for_test(transaction_id):
        """Capture accidental one-off AI suggestion requests."""
        captured.append(transaction_id)
        return {"ok": True, "message": "AI suggestion ready."}

    monkeypatch.setattr(
        transaction_controller.transactions_service,
        "suggest_transaction_ai_category",
        suggest_ai_for_test,
    )

    response = client.post(
        "/transactions/123/suggest-category",
        data={CSRF_FIELD_NAME: set_csrf_token(client)},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert captured == []
    assert_visible_text(response, "Review the token estimate before running AI.")


def test_estimate_transaction_category_suggestion_route_returns_json(client, monkeypatch):
    """Verify one-transaction AI estimates return JSON."""
    from finance_app.modules.transactions import controller as transaction_controller

    def estimate_for_test(transaction_id):
        """Return a deterministic single-transaction estimate."""
        assert transaction_id == 123
        return {
            "ok": True,
            "message": "AI token estimate ready.",
            "estimate": {"request_count": 1, "input_tokens": 77, "total_tokens": 333},
        }

    monkeypatch.setattr(
        transaction_controller.transactions_service,
        "estimate_transaction_ai_category",
        estimate_for_test,
    )

    response = client.post(
        "/transactions/123/suggest-category/estimate",
        data={CSRF_FIELD_NAME: set_csrf_token(client)},
    )

    assert response.status_code == 200
    assert response.get_json()["estimate"]["request_count"] == 1
    assert response.get_json()["message"] == "AI token estimate ready."


def test_apply_transaction_ai_suggestion_route_applies_pending_suggestion(client, core_conn, monkeypatch):
    """Verify the AI suggestion apply route delegates to the transaction service."""
    from finance_app.modules.transactions import controller as transaction_controller

    tx_id = insert_route_transaction(core_conn, fingerprint="route-apply-ai-suggestion")
    pending_suggestion = {
        "transaction_id": tx_id,
        "can_apply": True,
        "persistence": {"category": "Entertainment"},
    }
    captured = {}

    with client.session_transaction() as session:
        session["transaction_ai_suggestion"] = pending_suggestion

    def apply_for_test(transaction_id, suggestion, action, rule_keyword="", amount_min=None, amount_max=None):
        """Capture the submitted explicit apply action."""
        captured.update(
            {
                "transaction_id": transaction_id,
                "suggestion": suggestion,
                "action": action,
                "rule_keyword": rule_keyword,
                "amount_min": amount_min,
                "amount_max": amount_max,
            }
        )
        return {"updated": True, "message": "AI suggestion applied. Rule saved."}

    monkeypatch.setattr(
        transaction_controller.transactions_service,
        "apply_transaction_ai_suggestion",
        apply_for_test,
    )

    response = client.post(
        f"/transactions/{tx_id}/ai-suggestion",
        data={
            CSRF_FIELD_NAME: set_csrf_token(client),
            "suggestion_action": "apply_and_create_rule",
            "keyword": "TVA SPORTS DIRECT",
            "amount_min": "",
            "amount_max": "",
        },
        follow_redirects=True,
    )

    with client.session_transaction() as session:
        stored_suggestion = session.get("transaction_ai_suggestion")

    assert response.status_code == 200
    assert_visible_text(response, "AI suggestion applied. Rule saved.")
    assert captured["transaction_id"] == tx_id
    assert captured["suggestion"] == pending_suggestion
    assert captured["action"] == "apply_and_create_rule"
    assert captured["rule_keyword"] == "TVA SPORTS DIRECT"
    assert captured["amount_min"] is None
    assert captured["amount_max"] is None
    assert stored_suggestion is None
