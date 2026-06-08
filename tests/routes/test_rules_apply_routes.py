"""Route tests for applying category rules."""

from sqlalchemy import text
from tests.support.html import assert_visible_text
from tests.support.jobs import capture_background_jobs

from finance_app.modules.rules import controller as rules_controller
from finance_app.modules.rules.engine import apply_all_rules_job, undo_apply_all_rules_job


def test_apply_single_rule_route_requires_preview_confirmation(csrf_client, core_conn, data_factory):
    """Verify applying one rule is blocked until preview confirmation."""
    rule_id = data_factory.rules.create()
    tx_id = data_factory.transactions.create()

    response = csrf_client.post(
        f"/rules/{rule_id}/apply",
        follow_redirects=True,
    )

    tx = core_conn.execute(
        text("""
        SELECT category, needs_review, category_source, category_rule_id
        FROM transactions
        WHERE id = :p0
    """),
        {"p0": tx_id},
    ).fetchone()
    assert response.status_code == 200
    assert_visible_text(response, "Preview apply before applying a rule.")
    assert tuple(tx) == ("UNKNOWN", 1, "unknown", None)


def test_apply_single_rule_route_updates_only_transactions_where_rule_wins(csrf_client, core_conn, data_factory):
    """Verify confirmed default apply skips transactions won by another rule."""
    broad_rule_id = data_factory.rules.create(keyword="METRO", category="Food")
    data_factory.rules.create(keyword="METRO GROCERY", category="Utilities")
    winning_tx_id = data_factory.transactions.create(description="Metro Pharmacy")
    losing_tx_id = data_factory.transactions.create(description="Metro Grocery #123")

    response = csrf_client.post(
        f"/rules/{broad_rule_id}/apply",
        data={
            "confirm_preview": "1",
            "mode": "apply_where_wins",
        },
        follow_redirects=True,
    )

    winning_tx = core_conn.execute(
        text("""
        SELECT category, needs_review, category_source, category_rule_id
        FROM transactions
        WHERE id = :p0
        """),
        {"p0": winning_tx_id},
    ).fetchone()
    losing_tx = core_conn.execute(
        text("""
        SELECT category, needs_review, category_rule_id
        FROM transactions
        WHERE id = :p0
    """),
        {"p0": losing_tx_id},
    ).fetchone()
    assert response.status_code == 200
    assert_visible_text(response, "Rule applied where it wins to 1 existing transactions.")
    assert tuple(winning_tx) == ("Food", 0, "rule", broad_rule_id)
    assert tuple(losing_tx) == ("UNKNOWN", 1, None)


def test_apply_single_rule_route_can_force_apply_matching_transactions(csrf_client, core_conn, data_factory):
    """Verify confirmed force apply updates all matching transactions."""
    broad_rule_id = data_factory.rules.create(keyword="METRO", category="Food")
    data_factory.rules.create(keyword="METRO GROCERY", category="Utilities")
    winning_tx_id = data_factory.transactions.create(description="Metro Pharmacy")
    losing_tx_id = data_factory.transactions.create(description="Metro Grocery #123")

    response = csrf_client.post(
        f"/rules/{broad_rule_id}/apply",
        data={
            "confirm_preview": "1",
            "mode": "force_apply_rule",
        },
        follow_redirects=True,
    )

    rows = core_conn.execute(
        text("""
        SELECT id, category, needs_review, category_source, category_rule_id
        FROM transactions
        WHERE id IN (:p0, :p1)
        ORDER BY id
    """),
        {"p0": winning_tx_id, "p1": losing_tx_id},
    ).fetchall()
    assert response.status_code == 200
    assert_visible_text(response, "Rule force-applied to 2 existing transactions.")
    assert [tuple(row)[1:] for row in rows] == [
        ("Food", 0, "rule", broad_rule_id),
        ("Food", 0, "rule", broad_rule_id),
    ]


def test_apply_single_rule_route_reports_missing_rule(csrf_client, core_conn, data_factory):
    """Verify applying a missing rule flashes a validation message."""
    tx_id = data_factory.transactions.create()

    response = csrf_client.post(
        "/rules/9999/apply",
        follow_redirects=True,
    )

    tx = core_conn.execute(text("SELECT category FROM transactions WHERE id = :p0"), {"p0": tx_id}).fetchone()
    assert response.status_code == 200
    assert_visible_text(response, "Rule not found.")
    assert tx._mapping["category"] == "UNKNOWN"


def test_apply_all_rules_route_requires_preview_confirmation(csrf_client, monkeypatch):
    """Verify apply-all is blocked until preview confirmation."""
    submitted_jobs = capture_background_jobs(monkeypatch, rules_controller)

    response = csrf_client.post(
        "/rules/apply-all",
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert_visible_text(response, "Preview apply before applying all rules.")
    assert len(submitted_jobs) == 0


def test_apply_all_rules_route_queues_background_job(csrf_client, monkeypatch):
    """Verify apply-all route queues a background job with undo metadata."""
    submitted_jobs = capture_background_jobs(monkeypatch, rules_controller, job_id="applyall123")

    response = csrf_client.post(
        "/rules/apply-all",
        data={
            "confirm_preview": "1",
            "next": "/rules?page=2",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert_visible_text(response, "Applying all rules in the background.")
    submitted = submitted_jobs.single()
    assert submitted.label == "Apply all category rules"
    assert submitted.func is apply_all_rules_job
    assert isinstance(submitted.args[0], dict)
    assert submitted.undo_handler is undo_apply_all_rules_job
    assert submitted.undo_args == (submitted.args[0],)
