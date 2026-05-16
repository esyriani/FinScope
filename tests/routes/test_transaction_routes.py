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
