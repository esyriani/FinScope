"""Tests for review workflow application and undo behavior."""

import json
from decimal import Decimal

import pytest
from sqlalchemy import text

from finance_app.core.constants import (
    CATEGORY_RULE_DIRECTION_ANY,
    CATEGORY_SOURCE_MANUAL,
    TRANSACTION_KIND_EXPENSE,
    TRANSACTION_KIND_INCOME,
    TRANSACTION_KIND_REFUND,
    TRANSACTION_KIND_TRANSFER,
    TRANSFER_CATEGORY,
)
from finance_app.modules.categories.repository import resolve_category_id
from finance_app.modules.categories.taxonomy import (
    get_rule_tags_by_rule_id,
    get_transaction_tag_names,
    set_transaction_tags,
)
from finance_app.modules.review.workflow import (
    apply_review_group_job,
    apply_review_group_transactions,
    restore_review_transaction,
    review_transaction_id_filter,
    reviewed_transaction_kind,
    rule_snapshots_match,
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
               category_id, category_rule_id, category_metadata, categorized_at,
               reviewed_at, transaction_kind
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


def test_apply_review_group_transactions_ignores_reviewed_rows_while_updating_candidates(core_conn):
    """Verify completed review rows are excluded without hiding pending candidates."""
    needs_update_id = insert_review_transaction(core_conn, "Metro Grocery", 12.34, "review-skip-unchanged-update")
    unchanged_id = insert_review_transaction(core_conn, "Metro Grocery", 20.00, "review-skip-unchanged-done")
    core_conn.execute(
        text("""
        UPDATE transactions
        SET category = 'Food',
            category_id = :category_id,
            needs_review = 0,
            category_source = 'manual',
            transaction_kind = 'expense'
        WHERE id = :transaction_id
        """),
        {"category_id": resolve_category_id(core_conn, "Food"), "transaction_id": unchanged_id},
    )
    set_transaction_tags(core_conn, unchanged_id, ["Tax"], source=CATEGORY_SOURCE_MANUAL)
    core_conn.commit()

    changes = apply_review_group_transactions(
        core_conn,
        "METRO GROCERY",
        "Food",
        ["Tax"],
        "UNKNOWN",
    )
    core_conn.commit()

    assert [change["transaction_id"] for change in changes] == [needs_update_id]
    assert transaction_state(core_conn, unchanged_id)["category"] == "Food"
    assert transaction_state(core_conn, needs_update_id)["category"] == "Food"
    assert get_transaction_tag_names(core_conn, unchanged_id) == ["Tax"]


def test_review_transaction_id_filter_normalizes_selected_ids_and_continues_after_bad_values():
    """Verify selected review IDs ignore invalid entries without dropping later valid IDs."""
    assert review_transaction_id_filter(transaction_ids=[None, "bad", "2", "2", 0, -1, " 3 "]) == [2, 3]
    assert review_transaction_id_filter(transaction_id=7, transaction_ids=["2"]) == [7]
    assert review_transaction_id_filter(transaction_ids=()) is None


@pytest.mark.parametrize(
    ("category", "amount", "current_kind", "expected"),
    [
        pytest.param(f"{TRANSFER_CATEGORY} "[:-1], "10.00", TRANSACTION_KIND_EXPENSE, TRANSACTION_KIND_TRANSFER),
        pytest.param("Utilities", "10.00", TRANSACTION_KIND_EXPENSE, TRANSACTION_KIND_EXPENSE),
        pytest.param("Food", "-0.01", TRANSACTION_KIND_EXPENSE, TRANSACTION_KIND_INCOME),
        pytest.param("Food", "0.00", TRANSACTION_KIND_INCOME, TRANSACTION_KIND_EXPENSE),
        pytest.param("Food", "10.00", TRANSACTION_KIND_INCOME, TRANSACTION_KIND_EXPENSE),
        pytest.param("Food", None, TRANSACTION_KIND_INCOME, TRANSACTION_KIND_EXPENSE),
        pytest.param("Food", "-10.00", f"{TRANSACTION_KIND_REFUND} "[:-1], TRANSACTION_KIND_REFUND),
        pytest.param("Food", "10.00", TRANSACTION_KIND_TRANSFER, TRANSACTION_KIND_EXPENSE),
    ],
)
def test_reviewed_transaction_kind_uses_category_amount_and_existing_refund(category, amount, current_kind, expected):
    """Verify review categorization preserves transfer/refund and amount sign semantics."""
    assert reviewed_transaction_kind(category, amount, current_kind) == expected


def test_save_review_rule_and_undo_created_rule(core_conn):
    """Verify saved review rules can be undone when newly created."""
    lower_rule = save_review_rule(core_conn, "ALPHA MARKET", "Personal", tags=[])
    rule_change = save_review_rule(core_conn, "METRO GROCERY", "Food", tags=["Tax"])
    higher_rule = save_review_rule(core_conn, "ZULU MARKET", "Utilities", tags=[])
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
    remaining_rule_ids = {
        row["id"]
        for row in core_conn.execute(
            text("SELECT id FROM category_rules WHERE id IN (:p0, :p1)"),
            {"p0": lower_rule["rule_id"], "p1": higher_rule["rule_id"]},
        )
        .mappings()
        .fetchall()
    }
    assert remaining_rule_ids == {lower_rule["rule_id"], higher_rule["rule_id"]}


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


def test_apply_review_group_job_reports_singular_transaction(app, core_conn):
    """Verify review job summaries distinguish one reviewed transaction."""
    tx_id = insert_review_transaction(core_conn, "Metro Grocery", 12.34, "review-job-single")
    undo_state = {}

    message = apply_review_group_job(
        undo_state,
        "METRO GROCERY",
        "Food",
        [],
        False,
        "",
        None,
        None,
        tx_id,
    )

    assert message == "Categorized 1 transaction as Food."
    assert [change["transaction_id"] for change in undo_state["changes"]] == [tx_id]


def test_undo_review_group_job_skips_changed_transactions(app, core_conn):
    """Verify review undo does not overwrite transactions changed after processing."""
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

    assert message == "Restored 0 reviewed transactions. Skipped 1 transaction changed after processing."
    assert transaction_state(core_conn, tx_id)["category"] == "Personal"


def test_restore_review_transaction_resolves_missing_old_category_id(core_conn):
    """Verify undo restores the old category foreign key for legacy snapshots."""
    tx_id = insert_review_transaction(core_conn, "Metro Grocery", 12.34, "review-restore-category-id")
    changes = apply_review_group_transactions(core_conn, "METRO GROCERY", "Food", [], "UNKNOWN")
    change = changes[0]
    change["old_category_id"] = None
    core_conn.commit()

    cursor = restore_review_transaction(core_conn, change)
    core_conn.commit()

    tx = transaction_state(core_conn, tx_id)
    assert cursor.rowcount == 1
    assert tx["category"] == "UNKNOWN"
    assert tx["category_id"] == resolve_category_id(core_conn, "UNKNOWN")
    assert tx["transaction_kind"] == TRANSACTION_KIND_EXPENSE


@pytest.mark.parametrize(
    "guard_update",
    [
        "UPDATE transactions SET category = 'Bills' WHERE id = :transaction_id",
        "UPDATE transactions SET needs_review = 1 WHERE id = :transaction_id",
        "UPDATE transactions SET category_source = 'ai' WHERE id = :transaction_id",
        "UPDATE transactions SET category_source = 'rule' WHERE id = :transaction_id",
        "UPDATE transactions SET category_metadata = '' WHERE id = :transaction_id",
        "UPDATE transactions SET category_metadata = '~' WHERE id = :transaction_id",
        "UPDATE transactions SET reviewed_at = '2000-01-01T00:00:00Z' WHERE id = :transaction_id",
        "UPDATE transactions SET reviewed_at = '2999-01-01T00:00:00Z' WHERE id = :transaction_id",
    ],
)
def test_restore_review_transaction_requires_current_review_snapshot(core_conn, guard_update):
    """Verify undo skips rows whose reviewed state no longer matches the undo snapshot."""
    tx_id = insert_review_transaction(core_conn, "Metro Grocery", 12.34, f"review-restore-guard-{hash(guard_update)}")
    changes = apply_review_group_transactions(core_conn, "METRO GROCERY", "Food", [], "UNKNOWN")
    change = changes[0]
    core_conn.commit()
    core_conn.execute(text(guard_update), {"transaction_id": tx_id})
    core_conn.commit()

    cursor = restore_review_transaction(core_conn, change)
    core_conn.commit()

    assert cursor.rowcount == 0
    assert transaction_state(core_conn, tx_id)["category"] != "UNKNOWN"


def test_restore_review_transaction_only_restores_the_snapshot_transaction(core_conn):
    """Verify undo does not restore later transactions that happen to match the same snapshot."""
    target_id = insert_review_transaction(core_conn, "Metro Grocery", 12.34, "review-restore-target")
    other_id = insert_review_transaction(core_conn, "Metro Grocery", 20.00, "review-restore-other")
    changes = apply_review_group_transactions(core_conn, "METRO GROCERY", "Food", [], "UNKNOWN")
    target_change = next(change for change in changes if change["transaction_id"] == target_id)
    core_conn.execute(
        text("""
        UPDATE transactions
        SET category = :category,
            category_id = :category_id,
            needs_review = :needs_review,
            category_source = :category_source,
            category_confidence = :category_confidence,
            category_rule_id = :category_rule_id,
            category_metadata = :category_metadata,
            categorized_at = :categorized_at,
            reviewed_at = :reviewed_at
        WHERE id = :transaction_id
        """),
        {
            "category": target_change["new_category"],
            "category_id": target_change["new_category_id"],
            "needs_review": target_change["new_needs_review"],
            "category_source": target_change["new_category_source"],
            "category_confidence": target_change["new_category_confidence"],
            "category_rule_id": target_change["new_category_rule_id"],
            "category_metadata": target_change["new_category_metadata"],
            "categorized_at": target_change["new_categorized_at"],
            "reviewed_at": target_change["new_reviewed_at"],
            "transaction_id": other_id,
        },
    )
    core_conn.commit()

    cursor = restore_review_transaction(core_conn, target_change)
    core_conn.commit()

    assert cursor.rowcount == 1
    assert transaction_state(core_conn, target_id)["category"] == "UNKNOWN"
    assert transaction_state(core_conn, other_id)["category"] == "Food"


def test_undo_review_rule_reports_rule_already_removed(core_conn):
    """Verify rule undo does not recreate a rule removed after processing."""
    lower_rule = save_review_rule(core_conn, "ALPHA MARKET", "Personal", tags=[])
    rule_change = save_review_rule(core_conn, "METRO GROCERY", "Food", tags=["Tax"])
    higher_rule = save_review_rule(core_conn, "ZULU MARKET", "Utilities", tags=[])
    core_conn.execute(text("DELETE FROM category_rules WHERE id = :p0"), {"p0": rule_change["rule_id"]})
    core_conn.commit()

    result = undo_review_rule(core_conn, rule_change)
    core_conn.commit()

    assert result == "Rule already removed."
    remaining_rule_ids = {
        row["id"]
        for row in core_conn.execute(
            text("SELECT id FROM category_rules WHERE id IN (:p0, :p1)"),
            {"p0": lower_rule["rule_id"], "p1": higher_rule["rule_id"]},
        )
        .mappings()
        .fetchall()
    }
    assert remaining_rule_ids == {lower_rule["rule_id"], higher_rule["rule_id"]}


def test_undo_review_rule_leaves_rule_changed_after_processing(core_conn):
    """Verify rule undo is skipped when the saved rule no longer matches its snapshot."""
    rule_change = save_review_rule(core_conn, "METRO GROCERY", "Food", tags=["Tax"])
    core_conn.execute(
        text("""
        UPDATE category_rules
        SET category = 'Personal', category_id = :category_id
        WHERE id = :rule_id
        """),
        {"category_id": resolve_category_id(core_conn, "Personal"), "rule_id": rule_change["rule_id"]},
    )
    core_conn.commit()

    result = undo_review_rule(core_conn, rule_change)
    core_conn.commit()

    rule = core_conn.execute(
        text("SELECT category, category_id FROM category_rules WHERE id = :p0"),
        {"p0": rule_change["rule_id"]},
    ).fetchone()
    assert result == "Rule changed after processing; left it in place."
    assert tuple(rule) == ("Personal", resolve_category_id(core_conn, "Personal"))


def test_undo_review_rule_restores_previous_rule_category_id_from_category_name(core_conn):
    """Verify rule undo restores legacy snapshots that lack a category foreign key."""
    lower_rule = save_review_rule(core_conn, "ALPHA MARKET", "Personal", tags=[])
    original_change = save_review_rule(core_conn, "METRO GROCERY", "Utilities", tags=["Government"])
    higher_rule = save_review_rule(core_conn, "ZULU MARKET", "Rental", tags=[])
    core_conn.commit()
    rule_change = save_review_rule(core_conn, "METRO GROCERY", "Food", tags=["Tax"])
    rule_change["previous_rule"]["category_id"] = None
    del rule_change["previous_rule"]["ai_approved"]
    core_conn.commit()

    result = undo_review_rule(core_conn, rule_change)
    core_conn.commit()

    rule = core_conn.execute(
        text("SELECT category, category_id, ai_approved FROM category_rules WHERE id = :p0"),
        {"p0": original_change["rule_id"]},
    ).fetchone()
    neighbor_rules = {
        row["id"]: row["keyword"]
        for row in core_conn.execute(
            text("SELECT id, keyword FROM category_rules WHERE id IN (:p0, :p1)"),
            {"p0": lower_rule["rule_id"], "p1": higher_rule["rule_id"]},
        )
        .mappings()
        .fetchall()
    }
    assert result == "Restored previous rule."
    assert tuple(rule) == ("Utilities", resolve_category_id(core_conn, "Utilities"), 0)
    assert neighbor_rules == {
        lower_rule["rule_id"]: "ALPHA MARKET",
        higher_rule["rule_id"]: "ZULU MARKET",
    }


def matching_rule_snapshot():
    """Return a representative review-rule snapshot."""
    return {
        "id": 10,
        "merchant_id": None,
        "account_id": None,
        "keyword": "METRO GROCERY",
        "category": "Food",
        "category_id": 20,
        "amount_min": Decimal("1.00"),
        "amount_max": Decimal("30.00"),
        "direction": CATEGORY_RULE_DIRECTION_ANY,
        "source": CATEGORY_SOURCE_MANUAL,
        "ai_approved": 0,
        "tags": ["Tax"],
    }


def test_rule_snapshots_match_accepts_equal_nonidentical_values():
    """Verify snapshot equality is value-based rather than identity-based."""
    right = matching_rule_snapshot()
    right["keyword"] = "".join(["METRO", " GROCERY"])
    right["category"] = "".join(["Fo", "od"])
    right["source"] = "".join(["man", "ual"])
    right["tags"] = ["Tax"]

    assert rule_snapshots_match(matching_rule_snapshot(), right)


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("id", 11),
        ("merchant_id", 2),
        ("account_id", 3),
        ("keyword", "OTHER"),
        ("category", "Personal"),
        ("category_id", 21),
        ("amount_min", Decimal("2.00")),
        ("amount_max", Decimal("31.00")),
        ("direction", "credit"),
        ("source", "rule"),
        ("ai_approved", 1),
        ("tags", ["Alpha"]),
        ("tags", ["Zzz"]),
    ],
)
def test_rule_snapshots_match_rejects_changed_persisted_fields(field, replacement):
    """Verify rule snapshot comparison includes every persisted rule decision field."""
    right = matching_rule_snapshot()
    right[field] = replacement

    assert not rule_snapshots_match(matching_rule_snapshot(), right)
