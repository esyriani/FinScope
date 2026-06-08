"""Tests for review workflow application and undo behavior."""

from sqlalchemy import text
import json

from finance_app.modules.categories.taxonomy import get_rule_tags_by_rule_id, get_transaction_tag_names
from finance_app.modules.review.workflow import (
    apply_review_group_job,
    apply_review_group_transactions,
    save_review_rule,
    undo_review_group_job,
    undo_review_rule,
)


def insert_review_transaction(conn, description, amount, fingerprint):
    """Insert a transaction that should appear in review workflows."""
    tx_id = conn.execute(
        text("""
        INSERT INTO transactions (
            tx_date,
            description,
            amount,
            category,
            needs_review,
            category_source,
            fingerprint
        )
        VALUES ('2026-01-02', :p0, :p1, 'UNKNOWN', 1, 'unknown', :p2)
        """),
        {"p0": description, "p1": amount, "p2": fingerprint},
    ).lastrowid
    conn.commit()
    return tx_id


def transaction_state(conn, tx_id):
    """Return selected transaction state for assertions."""
    return (
        conn.execute(
            text("""
        SELECT category, needs_review, category_source, category_confidence,
               category_rule_id, category_metadata, categorized_at, reviewed_at
        FROM transactions
        WHERE id = :p0
        """),
            {"p0": tx_id},
        )
        .mappings()
        .fetchone()
    )


def test_apply_review_group_transactions_updates_group_and_tags(core_conn):
    """Verify review workflow updates all matching unknown transactions."""
    first_id = insert_review_transaction(core_conn, "Metro Grocery", 12.34, "review-group-1")
    second_id = insert_review_transaction(core_conn, "Metro Grocery", 20.00, "review-group-2")
    other_id = insert_review_transaction(core_conn, "Other Shop", 20.00, "review-group-other")

    changes = apply_review_group_transactions(
        core_conn,
        "METRO GROCERY",
        "Food",
        ["Tax"],
        "UNKNOWN",
    )
    core_conn.commit()

    assert {change["transaction_id"] for change in changes} == {first_id, second_id}
    for tx_id in (first_id, second_id):
        tx = transaction_state(core_conn, tx_id)
        assert tx["category"] == "Food"
        assert tx["needs_review"] == 0
        assert tx["category_source"] == "manual"
        assert tx["category_confidence"] == 1.0
        assert json.loads(tx["category_metadata"])["decision_source"] == "manual"
        assert tx["categorized_at"] is not None
        assert tx["reviewed_at"] is not None
        assert get_transaction_tag_names(core_conn, tx_id) == ["Tax"]
    assert transaction_state(core_conn, other_id)["category"] == "UNKNOWN"


def test_apply_review_group_transactions_can_update_single_transaction(core_conn):
    """Verify review workflow can limit a group action to one transaction."""
    first_id = insert_review_transaction(core_conn, "Metro Grocery", 12.34, "review-single-1")
    second_id = insert_review_transaction(core_conn, "Metro Grocery", 20.00, "review-single-2")

    changes = apply_review_group_transactions(
        core_conn,
        "METRO GROCERY",
        "Food",
        [],
        "UNKNOWN",
        transaction_id=first_id,
    )
    core_conn.commit()

    assert [change["transaction_id"] for change in changes] == [first_id]
    assert transaction_state(core_conn, first_id)["category"] == "Food"
    assert transaction_state(core_conn, second_id)["category"] == "UNKNOWN"


def test_apply_review_group_transactions_can_update_selected_transactions(core_conn):
    """Verify review workflow can limit a group action to selected transactions."""
    first_id = insert_review_transaction(core_conn, "Metro Grocery", 12.34, "review-selected-1")
    second_id = insert_review_transaction(core_conn, "Metro Grocery", 20.00, "review-selected-2")
    third_id = insert_review_transaction(core_conn, "Metro Grocery", 15.00, "review-selected-3")

    changes = apply_review_group_transactions(
        core_conn,
        "METRO GROCERY",
        "Food",
        [],
        "UNKNOWN",
        transaction_ids=[first_id, third_id],
    )
    core_conn.commit()

    assert [change["transaction_id"] for change in changes] == [third_id, first_id]
    assert transaction_state(core_conn, first_id)["category"] == "Food"
    assert transaction_state(core_conn, second_id)["category"] == "UNKNOWN"
    assert transaction_state(core_conn, third_id)["category"] == "Food"


def test_save_review_rule_and_undo_created_rule(core_conn):
    """Verify saved review rules can be undone when newly created."""
    rule_change = save_review_rule(core_conn, "METRO GROCERY", "Food", tags=["Tax"])
    core_conn.commit()

    assert rule_change["previous_rule"] is None
    assert rule_change["new_rule"]["keyword"] == "METRO GROCERY"
    assert get_rule_tags_by_rule_id(core_conn, [rule_change["rule_id"]])[rule_change["rule_id"]] == ["Tax"]

    result = undo_review_rule(core_conn, rule_change)
    core_conn.commit()

    remaining = (
        core_conn.execute(
            text("SELECT COUNT(*) AS count FROM category_rules WHERE id = :p0"), {"p0": rule_change["rule_id"]}
        )
        .fetchone()
        ._mapping["count"]
    )
    assert result == "Removed created rule."
    assert remaining == 0


def test_save_review_rule_and_undo_restores_previous_rule(core_conn):
    """Verify review-rule undo restores a replaced rule snapshot and tags."""
    original_change = save_review_rule(core_conn, "METRO GROCERY", "Utilities", tags=["Government"])
    core_conn.commit()
    rule_change = save_review_rule(core_conn, "METRO GROCERY", "Food", tags=["Tax"])
    core_conn.commit()

    result = undo_review_rule(core_conn, rule_change)
    core_conn.commit()

    rule = core_conn.execute(
        text("""
        SELECT keyword, category, source
        FROM category_rules
        WHERE id = :p0
        """),
        {"p0": original_change["rule_id"]},
    ).fetchone()
    assert result == "Restored previous rule."
    assert tuple(rule) == ("METRO GROCERY", "Utilities", "manual")
    assert get_rule_tags_by_rule_id(core_conn, [original_change["rule_id"]])[original_change["rule_id"]] == [
        "Government"
    ]


def test_apply_review_group_job_and_undo_restore_transactions_and_rule(app, core_conn):
    """Verify review background job saves undo state and restores transactions/rules."""
    first_id = insert_review_transaction(core_conn, "Metro Grocery", 12.34, "review-job-1")
    second_id = insert_review_transaction(core_conn, "Metro Grocery", 20.00, "review-job-2")
    undo_state = {}

    message = apply_review_group_job(
        undo_state,
        "METRO GROCERY",
        "Food",
        ["Tax"],
        True,
        "METRO GROCERY",
        None,
        None,
        None,
    )

    assert message == "Categorized 2 transactions as Food. Rule saved for METRO GROCERY."
    assert undo_state["category"] == "Food"
    assert len(undo_state["changes"]) == 2
    assert undo_state["rule_change"]["new_rule"]["category"] == "Food"
    assert transaction_state(core_conn, first_id)["category"] == "Food"
    assert transaction_state(core_conn, second_id)["category"] == "Food"

    undo_message = undo_review_group_job(undo_state)

    assert undo_message == "Restored 2 reviewed transactions. Removed created rule."
    assert transaction_state(core_conn, first_id)["category"] == "UNKNOWN"
    assert transaction_state(core_conn, second_id)["category"] == "UNKNOWN"
    assert get_transaction_tag_names(core_conn, first_id) == []
    assert core_conn.execute(text("SELECT COUNT(*) AS count FROM category_rules")).fetchone()._mapping["count"] == 0


def test_undo_review_group_job_skips_changed_transactions(app, core_conn):
    """Verify review undo does not overwrite transactions changed after the job."""
    tx_id = insert_review_transaction(core_conn, "Metro Grocery", 12.34, "review-skip")
    undo_state = {}
    apply_review_group_job(
        undo_state,
        "METRO GROCERY",
        "Food",
        [],
        False,
        "",
        None,
        None,
        None,
    )
    core_conn.execute(text("UPDATE transactions SET category = 'Personal' WHERE id = :p0"), {"p0": tx_id})
    core_conn.commit()

    message = undo_review_group_job(undo_state)

    assert message == "Restored 0 reviewed transactions. Skipped 1 transaction changed after the job."
    assert transaction_state(core_conn, tx_id)["category"] == "Personal"
