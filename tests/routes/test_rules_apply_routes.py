"""Route tests for applying category rules."""

from finance_app.core.csrf import CSRF_FIELD_NAME, CSRF_SESSION_KEY
from finance_app.modules.rules import controller as rules_controller
from finance_app.modules.rules.engine import apply_all_rules_job, undo_apply_all_rules_job


def set_csrf_token(client, token="test-csrf-token"):
    """Store a CSRF token in the test client's session."""
    with client.session_transaction() as session:
        session[CSRF_SESSION_KEY] = token
    return token


def insert_rule(conn, keyword="METRO", category="Food"):
    """Insert a category rule and return its id."""
    rule_id = conn.execute(
        """
        INSERT INTO category_rules (keyword, category, source)
        VALUES (?, ?, 'manual')
        """,
        (keyword, category),
    ).lastrowid
    conn.commit()
    return rule_id


def insert_transaction(conn, description="Metro Grocery", amount=25.0, fingerprint="route-rule-tx"):
    """Insert a transaction and return its id."""
    tx_id = conn.execute(
        """
        INSERT INTO transactions (tx_date, description, amount, category, needs_review, fingerprint)
        VALUES ('2026-01-02', ?, ?, 'UNKNOWN', 1, ?)
        """,
        (description, amount, fingerprint),
    ).lastrowid
    conn.commit()
    return tx_id


def test_apply_single_rule_route_updates_matching_transactions(client, db_conn):
    """Verify applying one rule through the route mutates matching transactions."""
    rule_id = insert_rule(db_conn)
    tx_id = insert_transaction(db_conn)

    response = client.post(
        f"/rules/{rule_id}/apply",
        data={CSRF_FIELD_NAME: set_csrf_token(client)},
        follow_redirects=True,
    )

    tx = db_conn.execute(
        """
        SELECT category, needs_review, category_source, category_rule_id
        FROM transactions
        WHERE id = ?
        """,
        (tx_id,),
    ).fetchone()
    assert response.status_code == 200
    assert b"Rule applied to 1 existing transactions." in response.data
    assert tuple(tx) == ("Food", 0, "rule", rule_id)


def test_apply_single_rule_route_reports_missing_rule(client, db_conn):
    """Verify applying a missing rule flashes a validation message."""
    tx_id = insert_transaction(db_conn)

    response = client.post(
        "/rules/9999/apply",
        data={CSRF_FIELD_NAME: set_csrf_token(client)},
        follow_redirects=True,
    )

    tx = db_conn.execute(
        "SELECT category FROM transactions WHERE id = ?",
        (tx_id,),
    ).fetchone()
    assert response.status_code == 200
    assert b"Rule not found." in response.data
    assert tx["category"] == "UNKNOWN"


def test_apply_all_rules_route_queues_background_job(client, monkeypatch):
    """Verify apply-all route queues a background job with undo metadata."""
    submitted_jobs = []

    def capture_job(label, func, *args, undo_handler=None, undo_args=None, **kwargs):
        """Capture background job metadata."""
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
        return "applyall123"

    monkeypatch.setattr(rules_controller, "submit_background_job", capture_job)

    response = client.post(
        "/rules/apply-all",
        data={
            CSRF_FIELD_NAME: set_csrf_token(client),
            "next": "/rules?page=2",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"Applying all rules in the background." in response.data
    assert submitted_jobs[0]["label"] == "Apply all category rules"
    assert submitted_jobs[0]["func"] is apply_all_rules_job
    assert isinstance(submitted_jobs[0]["args"][0], dict)
    assert submitted_jobs[0]["undo_handler"] is undo_apply_all_rules_job
    assert submitted_jobs[0]["undo_args"] == (submitted_jobs[0]["args"][0],)
