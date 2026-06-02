"""Tests for category rule application and undo workflows."""

import json
import pytest
from sqlalchemy import insert, select, update

from finance_app.database.engine import db_core_transaction
from finance_app.database.tables import (
    accounts as accounts_table,
    category_rules as category_rules_table,
    transactions as transactions_table,
)
from finance_app.modules.categories.repository import resolve_category_id
from finance_app.modules.categories.taxonomy import get_transaction_tag_names, set_rule_tags
from finance_app.modules.merchants.repository import get_or_create_merchant, get_or_create_merchant_for_description
from finance_app.modules.rules import engine as rules_engine
from finance_app.modules.rules.engine import (
    apply_all_rules_job,
    apply_all_rules_to_transactions,
    apply_single_rule_to_transactions,
    preview_rule_matches,
    undo_apply_all_rules_job,
)


def insert_transaction(
    conn,
    description,
    amount,
    fingerprint,
    category="UNKNOWN",
    needs_review=1,
    category_source="unknown",
    ignored=0,
    merchant_id=None,
    account_id=None,
):
    """Insert a transaction row and return its id."""
    tx_id = conn.execute(
        insert(transactions_table).values(
            account_id=account_id,
            merchant_id=merchant_id,
            tx_date="2026-01-02",
            description=description,
            amount=amount,
            category=category,
            category_id=resolve_category_id(conn, category),
            needs_review=needs_review,
            category_source=category_source,
            ignored=ignored,
            fingerprint=fingerprint,
        )
    ).inserted_primary_key[0]
    conn.commit()
    return tx_id


def insert_rule(conn, keyword, category, amount_min=None, amount_max=None, tags=None):
    """Insert a category rule and optional rule tags."""
    rule_id = conn.execute(
        insert(category_rules_table).values(
            keyword=keyword,
            category=category,
            category_id=resolve_category_id(conn, category),
            amount_min=amount_min,
            amount_max=amount_max,
            source="manual",
        )
    ).inserted_primary_key[0]
    set_rule_tags(conn, rule_id, tags or [])
    conn.commit()
    return rule_id


def insert_merchant_rule(conn, merchant_id, keyword, category, amount_min=None, amount_max=None, tags=None):
    """Insert a merchant-bound category rule and optional rule tags."""
    rule_id = conn.execute(
        insert(category_rules_table).values(
            merchant_id=merchant_id,
            keyword=keyword,
            category=category,
            category_id=resolve_category_id(conn, category),
            amount_min=amount_min,
            amount_max=amount_max,
            source="manual",
        )
    ).inserted_primary_key[0]
    set_rule_tags(conn, rule_id, tags or [])
    conn.commit()
    return rule_id


def transaction_state(conn, tx_id):
    """Return selected transaction fields for assertions."""
    return conn.execute(
        select(
            transactions_table.c.category,
            transactions_table.c.category_id,
            transactions_table.c.needs_review,
            transactions_table.c.category_source,
            transactions_table.c.category_confidence,
            transactions_table.c.category_rule_id,
            transactions_table.c.category_metadata,
            transactions_table.c.categorized_at,
            transactions_table.c.reviewed_at,
        ).where(transactions_table.c.id == tx_id)
    ).mappings().fetchone()


def test_apply_single_rule_to_transactions_updates_matching_active_rows(db_conn):
    """Verify a single rule only updates matching non-ignored transactions."""
    matching_id = insert_transaction(db_conn, "Metro Grocery #123", 12.34, "single-match")
    out_of_range_id = insert_transaction(db_conn, "Metro Grocery #456", 30.00, "single-range")
    ignored_id = insert_transaction(db_conn, "Metro Grocery #789", 12.34, "single-ignored", ignored=1)
    rule_id = insert_rule(db_conn, "METRO", "Food", amount_min=10, amount_max=20, tags=["Tax"])

    updated_count = apply_single_rule_to_transactions(
        db_conn,
        {
            "id": rule_id,
            "keyword": "METRO",
            "category": "Food",
            "amount_min": 10,
            "amount_max": 20,
            "tags": ["Tax"],
        },
    )
    db_conn.commit()

    matching = transaction_state(db_conn, matching_id)
    out_of_range = transaction_state(db_conn, out_of_range_id)
    ignored = transaction_state(db_conn, ignored_id)
    assert updated_count == 1
    assert matching["category"] == "Food"
    assert matching["needs_review"] == 0
    assert matching["category_source"] == "rule"
    assert matching["category_confidence"] == 1.0
    assert matching["category_rule_id"] == rule_id
    metadata = json.loads(matching["category_metadata"])
    assert metadata["decision_source"] == "rule"
    assert metadata["rule"]["rule_id"] == rule_id
    assert matching["categorized_at"] is not None
    assert matching["reviewed_at"] is None
    assert get_transaction_tag_names(db_conn, matching_id) == ["Tax"]
    assert out_of_range["category"] == "UNKNOWN"
    assert ignored["category"] == "UNKNOWN"


def test_rules_respect_account_and_direction_constraints(db_conn):
    """Verify rule matching honors explicit account and direction constraints."""
    checking_id = db_conn.execute(
        insert(accounts_table).values(name="Checking")
    ).inserted_primary_key[0]
    savings_id = db_conn.execute(
        insert(accounts_table).values(name="Savings")
    ).inserted_primary_key[0]
    checking_match_id = insert_transaction(
        db_conn,
        "Metro Grocery",
        12.34,
        "rule-account-direction-checking",
        account_id=checking_id,
    )
    savings_id_tx = insert_transaction(
        db_conn,
        "Metro Grocery",
        12.34,
        "rule-account-direction-savings",
        account_id=savings_id,
    )
    credit_id = insert_transaction(
        db_conn,
        "Metro Grocery refund",
        -12.34,
        "rule-account-direction-credit",
        account_id=checking_id,
    )
    rule_id = db_conn.execute(
        insert(category_rules_table).values(
            keyword="METRO",
            category="Food",
            category_id=resolve_category_id(db_conn, "Food"),
            account_id=checking_id,
            direction="debit",
            source="manual",
        )
    ).inserted_primary_key[0]
    db_conn.commit()

    updated_count = apply_all_rules_to_transactions(db_conn)
    db_conn.commit()

    checking = transaction_state(db_conn, checking_match_id)
    savings = transaction_state(db_conn, savings_id_tx)
    credit = transaction_state(db_conn, credit_id)
    assert updated_count == 1
    assert checking["category"] == "Food"
    assert checking["category_rule_id"] == rule_id
    assert savings["category"] == "UNKNOWN"
    assert credit["category"] == "UNKNOWN"


def test_merchant_bound_rule_matches_only_the_bound_merchant(db_conn):
    """Verify merchant-bound rules use merchant IDs instead of fuzzy keywords."""
    metro_id = get_or_create_merchant_for_description(db_conn, "Metro Grocery #123")["id"]
    other_id = get_or_create_merchant(
        db_conn,
        "OTHER MERCHANT",
    )["id"]
    matching_id = insert_transaction(
        db_conn,
        "Metro Grocery #123",
        12.34,
        "merchant-bound-match",
        merchant_id=metro_id,
    )
    other_merchant_id = insert_transaction(
        db_conn,
        "Metro Grocery #456",
        12.34,
        "merchant-bound-other",
        merchant_id=other_id,
    )
    rule_id = insert_merchant_rule(db_conn, metro_id, "METRO GROCERY", "Food", tags=["Tax"])
    rule = {
        "id": rule_id,
        "merchant_id": metro_id,
        "keyword": "METRO GROCERY",
        "category": "Food",
        "amount_min": None,
        "amount_max": None,
        "tags": ["Tax"],
    }

    preview_count, preview_sample = preview_rule_matches(db_conn, rule, limit=5)
    updated_count = apply_single_rule_to_transactions(db_conn, rule)
    db_conn.commit()

    assert preview_count == 1
    assert [row["id"] for row in preview_sample] == [matching_id]
    assert updated_count == 1
    assert transaction_state(db_conn, matching_id)["category_rule_id"] == rule_id
    assert transaction_state(db_conn, other_merchant_id)["category"] == "UNKNOWN"


def test_core_preview_and_single_rule_application(app, db_conn):
    """Verify preview and single-rule application can run through Core."""
    del app
    matching_id = insert_transaction(db_conn, "Metro Grocery #123", 12.34, "core-single-match")
    out_of_range_id = insert_transaction(db_conn, "Metro Grocery #456", 30.00, "core-single-range")
    insert_transaction(db_conn, "Metro Grocery #789", 12.34, "core-single-ignored", ignored=1)
    rule_id = insert_rule(db_conn, "METRO", "Food", amount_min=10, amount_max=20, tags=["Tax"])
    rule = {
        "id": rule_id,
        "merchant_id": None,
        "keyword": "METRO",
        "category": "Food",
        "amount_min": 10,
        "amount_max": 20,
        "tags": ["Tax"],
    }

    with db_core_transaction() as conn:
        match_count, sample = preview_rule_matches(conn, rule, limit=5)
        updated_count = apply_single_rule_to_transactions(conn, rule)

    assert match_count == 1
    assert [row["id"] for row in sample] == [matching_id]
    assert updated_count == 1
    assert transaction_state(db_conn, matching_id)["category_rule_id"] == rule_id
    assert transaction_state(db_conn, out_of_range_id)["category"] == "UNKNOWN"
    assert get_transaction_tag_names(db_conn, matching_id) == ["Tax"]


def test_apply_all_rules_to_transactions_captures_undo_and_skips_noops(db_conn):
    """Verify all-rule application updates changed rows and captures undo snapshots."""
    metro_id = insert_transaction(db_conn, "Metro Grocery", 20.00, "all-metro")
    payroll_id = insert_transaction(db_conn, "Payroll Deposit", -1000.00, "all-payroll")
    already_id = insert_transaction(
        db_conn,
        "Cafe Bistro",
        15.00,
        "all-already",
        category="Food",
        needs_review=0,
        category_source="rule",
    )
    metro_rule_id = insert_rule(db_conn, "METRO", "Food", tags=["Tax"])
    payroll_rule_id = insert_rule(db_conn, "PAYROLL", "Income")
    cafe_rule_id = insert_rule(db_conn, "CAFE", "Food")
    db_conn.execute(
        update(transactions_table)
        .where(transactions_table.c.id == already_id)
        .values(category_rule_id=cafe_rule_id)
    )
    db_conn.commit()

    updated_count, undo_changes = apply_all_rules_to_transactions(db_conn, capture_undo=True)
    db_conn.commit()

    assert updated_count == 2
    assert {change["transaction_id"] for change in undo_changes} == {metro_id, payroll_id}
    assert transaction_state(db_conn, metro_id)["category_rule_id"] == metro_rule_id
    assert transaction_state(db_conn, payroll_id)["category_rule_id"] == payroll_rule_id
    assert transaction_state(db_conn, already_id)["category"] == "Food"
    assert get_transaction_tag_names(db_conn, metro_id) == ["Tax"]


def test_core_apply_all_rules_uses_deterministic_merchant_keys(app, db_conn):
    """Verify Core all-rule application matches deterministic merchant keys."""
    del app
    amazon_id = insert_transaction(db_conn, "AMZN Mktp CA*ZZ999", 20.00, "core-alias-amazon")
    rule_id = insert_rule(db_conn, "AMZN MKTP", "Food", tags=["Tax"])

    with db_core_transaction() as conn:
        updated_count, undo_changes = apply_all_rules_to_transactions(conn, capture_undo=True)

    assert updated_count == 1
    assert [change["transaction_id"] for change in undo_changes] == [amazon_id]
    assert transaction_state(db_conn, amazon_id)["category_rule_id"] == rule_id
    assert get_transaction_tag_names(db_conn, amazon_id) == ["Tax"]


def test_apply_all_rules_preserves_square_processor_merchant_token(app, db_conn):
    """Verify Square payment descriptors keep the starred merchant for rule matching."""
    del app
    tx_id = insert_transaction(db_conn, "SQ *COSMETA", 74.73, "square-cosmeta")
    rule_id = insert_rule(db_conn, "COSMETA", "Food", tags=["Tax"])

    with db_core_transaction() as conn:
        updated_count, undo_changes = apply_all_rules_to_transactions(conn, capture_undo=True)

    assert updated_count == 1
    assert [change["transaction_id"] for change in undo_changes] == [tx_id]
    assert transaction_state(db_conn, tx_id)["category_rule_id"] == rule_id
    assert get_transaction_tag_names(db_conn, tx_id) == ["Tax"]


def test_apply_all_rules_job_and_undo_restore_previous_values(app, db_conn):
    """Verify the background all-rules job can restore transaction state."""
    metro_id = insert_transaction(db_conn, "Metro Grocery", 20.00, "job-metro")
    insert_rule(db_conn, "METRO", "Food", tags=["Tax"])
    undo_state = {}

    message = apply_all_rules_job(undo_state)

    applied = transaction_state(db_conn, metro_id)
    assert message == "Rules applied to 1 existing transaction."
    assert applied["category"] == "Food"
    assert applied["category_source"] == "rule"
    assert get_transaction_tag_names(db_conn, metro_id) == ["Tax"]
    assert len(undo_state["changes"]) == 1

    undo_message = undo_apply_all_rules_job(undo_state)
    restored = transaction_state(db_conn, metro_id)
    assert undo_message == "Restored previous rule categories for 1 transaction."
    assert restored["category"] == "UNKNOWN"
    assert restored["needs_review"] == 1
    assert restored["category_source"] == "unknown"
    assert restored["category_metadata"] is None
    assert get_transaction_tag_names(db_conn, metro_id) == []


def test_apply_all_rules_job_rolls_back_when_late_tag_write_fails(app, db_conn, monkeypatch):
    """Verify all-rule jobs do not persist partial category updates on failure."""
    metro_id = insert_transaction(db_conn, "Metro Grocery", 20.00, "job-rollback")
    insert_rule(db_conn, "METRO", "Food", tags=["Tax"])

    def fail_tag_write(conn, transaction_id, tag_names, source="unknown", rule_id=None):
        """Simulate a late write failure after the transaction row update."""
        del conn, transaction_id, tag_names, source, rule_id
        raise RuntimeError("tag write failed")

    monkeypatch.setattr(rules_engine, "set_transaction_tags", fail_tag_write)

    with pytest.raises(RuntimeError, match="tag write failed"):
        apply_all_rules_job({})

    state = transaction_state(db_conn, metro_id)
    assert state["category"] == "UNKNOWN"
    assert state["category_source"] == "unknown"
    assert state["category_rule_id"] is None
    assert get_transaction_tag_names(db_conn, metro_id) == []


def test_undo_apply_all_rules_job_skips_transactions_changed_after_job(app, db_conn):
    """Verify undo does not overwrite transactions edited after rule application."""
    metro_id = insert_transaction(db_conn, "Metro Grocery", 20.00, "job-skip")
    insert_rule(db_conn, "METRO", "Food")
    undo_state = {}
    apply_all_rules_job(undo_state)

    db_conn.execute(
        update(transactions_table)
        .where(transactions_table.c.id == metro_id)
        .values(category="Personal", category_id=resolve_category_id(db_conn, "Personal"))
    )
    db_conn.commit()

    message = undo_apply_all_rules_job(undo_state)

    assert message == (
        "Restored previous rule categories for 0 transactions. "
        "Skipped 1 transaction that changed after the job."
    )
    assert transaction_state(db_conn, metro_id)["category"] == "Personal"


def test_undo_apply_all_rules_job_reports_no_changes(app):
    """Verify empty all-rules undo state is handled cleanly."""
    assert undo_apply_all_rules_job({}) == "No rule changes needed to be restored."
