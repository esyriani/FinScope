"""Route tests for transaction mutation endpoints."""

from finance_app.core.csrf import CSRF_FIELD_NAME, CSRF_SESSION_KEY
from finance_app.modules.categories.taxonomy import get_rule_tags_by_rule_id, get_transaction_tag_names
from finance_app.modules.merchants.repository import get_or_create_merchant_for_description


def set_csrf_token(client, token="test-csrf-token"):
    """Store a CSRF token in the test client's session."""
    with client.session_transaction() as session:
        session[CSRF_SESSION_KEY] = token
    return token


def insert_transaction(conn, fingerprint="route-tx", category="UNKNOWN", needs_review=1):
    """Insert a transaction and return its id."""
    merchant_id = get_or_create_merchant_for_description(conn, "Metro Grocery #123")["id"]
    tx_id = conn.execute(
        """
        INSERT INTO transactions (
            merchant_id,
            tx_date,
            description,
            amount,
            category,
            needs_review,
            fingerprint
        )
        VALUES (?, '2026-01-02', 'Metro Grocery #123', 12.34, ?, ?, ?)
        """,
        (merchant_id, category, needs_review, fingerprint),
    ).lastrowid
    conn.commit()
    return tx_id


def transaction_state(conn, tx_id):
    """Return selected transaction state."""
    return conn.execute(
        """
        SELECT category, needs_review, category_source, category_confidence,
               category_rule_id, categorized_at, reviewed_at, ignored
        FROM transactions
        WHERE id = ?
        """,
        (tx_id,),
    ).fetchone()


def test_update_transaction_category_route_saves_manual_category_rule_and_tags(client, db_conn):
    """Verify category route updates transaction and saves an optional rule."""
    tx_id = insert_transaction(db_conn)

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

    tx = transaction_state(db_conn, tx_id)
    rule = db_conn.execute(
        """
        SELECT id, merchant_id, keyword, category, amount_min, amount_max, source
        FROM category_rules
        WHERE keyword = 'METRO GROCERY'
        """
    ).fetchone()
    assert response.status_code == 200
    assert b"Category updated. Rule saved for: METRO GROCERY from 10.00 to 20.00" in response.data
    assert tx["category"] == "Food"
    assert tx["needs_review"] == 0
    assert tx["category_source"] == "manual"
    assert tx["category_confidence"] == 1.0
    assert tx["category_rule_id"] is None
    assert tx["categorized_at"] is not None
    assert tx["reviewed_at"] is not None
    assert get_transaction_tag_names(db_conn, tx_id) == ["Tax"]
    assert rule["merchant_id"] is not None
    assert tuple(rule[2:]) == ("METRO GROCERY", "Food", 10.0, 20.0, "manual")
    assert get_rule_tags_by_rule_id(db_conn, [rule["id"]])[rule["id"]] == ["Tax"]


def test_update_transaction_category_route_can_update_transaction_only(client, db_conn):
    """Verify category route can update one transaction without creating a rule."""
    tx_id = insert_transaction(db_conn, fingerprint="route-tx-only")

    response = client.post(
        f"/transactions/{tx_id}/category",
        data={
            CSRF_FIELD_NAME: set_csrf_token(client),
            "category": "Food",
            "rule_action": "transaction_only",
        },
        follow_redirects=True,
    )

    rule_count = db_conn.execute("SELECT COUNT(*) AS count FROM category_rules").fetchone()["count"]
    assert response.status_code == 200
    assert b"Category updated for this transaction only." in response.data
    assert transaction_state(db_conn, tx_id)["category"] == "Food"
    assert rule_count == 0


def test_update_transaction_category_route_does_not_verify_unchanged_transaction(client, db_conn):
    """Verify unchanged category submissions do not mark a transaction verified."""
    tx_id = insert_transaction(
        db_conn,
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

    tx = transaction_state(db_conn, tx_id)
    assert response.status_code == 200
    assert b"No transaction changes to save." in response.data
    assert tx["category"] == "Food"
    assert tx["needs_review"] == 0
    assert tx["reviewed_at"] is None


def test_update_transaction_category_route_approves_unchanged_transaction_when_saving_rule(client, db_conn):
    """Verify saving a rule counts as explicit approval for the current transaction."""
    tx_id = insert_transaction(
        db_conn,
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

    tx = transaction_state(db_conn, tx_id)
    rule = db_conn.execute(
        """
        SELECT keyword, category, amount_min, amount_max
        FROM category_rules
        WHERE keyword = 'METRO GROCERY'
        """
    ).fetchone()
    assert response.status_code == 200
    assert b"Rule saved for: METRO GROCERY at amount 12.34" in response.data
    assert tx["category"] == "Food"
    assert tx["needs_review"] == 0
    assert tx["reviewed_at"] is not None
    assert tuple(rule) == ("METRO GROCERY", "Food", 12.34, 12.34)


def test_update_transaction_category_route_validates_missing_transaction_and_amounts(client, db_conn):
    """Verify category route handles missing rows and invalid amount bounds."""
    tx_id = insert_transaction(db_conn, fingerprint="route-invalid-amount")
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

    assert b"Transaction not found." in missing.data
    assert b"Amount bounds must be valid numbers." in invalid_amount.data
    assert transaction_state(db_conn, tx_id)["category"] == "UNKNOWN"


def test_verify_transaction_route_marks_transaction_reviewed(client, db_conn):
    """Verify verify route marks a transaction as no longer needing review."""
    tx_id = insert_transaction(db_conn, fingerprint="route-verify")

    response = client.post(
        f"/transactions/{tx_id}/verify",
        data={CSRF_FIELD_NAME: set_csrf_token(client)},
        follow_redirects=True,
    )

    tx = transaction_state(db_conn, tx_id)
    assert response.status_code == 200
    assert b"Transaction approved." in response.data
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
    assert b"Transaction not found." in response.data


def test_update_transaction_ignored_route_ignores_and_restores(client, db_conn):
    """Verify ignored route toggles ignored state and review status."""
    tx_id = insert_transaction(db_conn, fingerprint="route-ignore")
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

    tx = transaction_state(db_conn, tx_id)
    assert b"Transaction ignored." in ignored.data
    assert b"Transaction restored." in restored.data
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
    assert b"Transaction not found." in response.data


def test_batch_transactions_route_approves_only_selected_ids(client, db_conn):
    """Verify batch approval mutates only explicitly selected transactions."""
    first_id = insert_transaction(db_conn, fingerprint="route-batch-approve-1")
    second_id = insert_transaction(db_conn, fingerprint="route-batch-approve-2")
    other_id = insert_transaction(db_conn, fingerprint="route-batch-approve-other")

    response = client.post(
        "/transactions/batch",
        data={
            CSRF_FIELD_NAME: set_csrf_token(client),
            "transaction_ids": [str(first_id), str(second_id), str(second_id), "bad"],
            "batch_action": "approve",
        },
        follow_redirects=True,
    )

    first = transaction_state(db_conn, first_id)
    second = transaction_state(db_conn, second_id)
    other = transaction_state(db_conn, other_id)
    assert response.status_code == 200
    assert b"Approved selected transactions." in response.data
    assert first["needs_review"] == 0
    assert first["reviewed_at"] is not None
    assert second["needs_review"] == 0
    assert second["reviewed_at"] is not None
    assert other["needs_review"] == 1
    assert other["reviewed_at"] is None


def test_batch_transactions_route_ignores_only_selected_ids(client, db_conn):
    """Verify batch ignore mutates only explicitly selected transactions."""
    ignored_id = insert_transaction(db_conn, fingerprint="route-batch-ignore")
    other_id = insert_transaction(db_conn, fingerprint="route-batch-ignore-other")

    response = client.post(
        "/transactions/batch",
        data={
            CSRF_FIELD_NAME: set_csrf_token(client),
            "transaction_ids": [str(ignored_id)],
            "batch_action": "ignore",
        },
        follow_redirects=True,
    )

    ignored = transaction_state(db_conn, ignored_id)
    other = transaction_state(db_conn, other_id)
    assert response.status_code == 200
    assert b"Ignored selected transaction." in response.data
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
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert captured["transaction_ids"] == ["11", "22"]
    assert b"Recategorization queued for 2 selected transactions. Job: abcdef12" in response.data


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
    assert b"Select at least one transaction." in response.data


def test_suggest_transaction_category_route_shows_result_modal(client, db_conn, monkeypatch):
    """Verify the one-off AI route redirects back with suggestion modal content."""
    from finance_app.modules.transactions import controller as transaction_controller

    tx_id = insert_transaction(db_conn, fingerprint="route-suggest-ai")

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
        data={CSRF_FIELD_NAME: set_csrf_token(client)},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"AI category suggestion" in response.data
    assert b"TVA SPORTS DIRECT" in response.data
    assert b"Entertainment" in response.data
    assert b"TVA Sports is a streaming sports service." in response.data
    assert b"Apply to transaction" in response.data
    assert b"Apply and create rule" in response.data


def test_apply_transaction_ai_suggestion_route_applies_pending_suggestion(client, db_conn, monkeypatch):
    """Verify the AI suggestion apply route delegates to the transaction service."""
    from finance_app.modules.transactions import controller as transaction_controller

    tx_id = insert_transaction(db_conn, fingerprint="route-apply-ai-suggestion")
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
    assert b"AI suggestion applied. Rule saved." in response.data
    assert captured["transaction_id"] == tx_id
    assert captured["suggestion"] == pending_suggestion
    assert captured["action"] == "apply_and_create_rule"
    assert captured["rule_keyword"] == "TVA SPORTS DIRECT"
    assert captured["amount_min"] is None
    assert captured["amount_max"] is None
    assert stored_suggestion is None
