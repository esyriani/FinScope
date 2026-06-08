"""Tests for transaction repository behavior."""

import json

import pytest
from sqlalchemy import insert, select, update

from finance_app.database.tables import transactions as transactions_table
from finance_app.modules.categories.repository import resolve_category_id
from finance_app.modules.categories.taxonomy import (
    get_rule_tags_by_rule_id,
    get_transaction_tag_names,
    set_transaction_tags,
)
from finance_app.modules.merchants.repository import get_or_create_merchant_for_description
from finance_app.modules.transactions.repository import (
    assign_manual_category,
    get_transaction_for_category_update,
    mark_transaction_verified,
    set_transaction_ignored,
)


@pytest.fixture
def repository_transaction(core_conn):
    """Create a transaction row used by repository mutation tests."""
    merchant_id = get_or_create_merchant_for_description(core_conn, "STORE 123")["id"]
    transaction_id = core_conn.execute(
        insert(transactions_table).values(
            merchant_id=merchant_id,
            tx_date="2026-01-02",
            description="STORE 123",
            amount=12.34,
            category="UNKNOWN",
            category_id=resolve_category_id(core_conn, "UNKNOWN"),
            needs_review=1,
            fingerprint="tx-1",
        )
    ).inserted_primary_key[0]
    return transaction_id, merchant_id


def test_get_transaction_for_category_update_returns_edit_context(core_conn, repository_transaction):
    """Verify get transaction for category update returns edit context."""
    transaction_id, merchant_id = repository_transaction

    row = get_transaction_for_category_update(core_conn, transaction_id)

    assert row["description"] == "STORE 123"
    assert row["merchant_id"] == merchant_id
    assert row["amount"] == 12.34


def test_assign_manual_category_updates_transaction_tags_and_optional_rule(core_conn, repository_transaction):
    """Verify assign manual category updates transaction tags and optional rule."""
    transaction_id, merchant_id = repository_transaction

    result = assign_manual_category(
        core_conn,
        transaction_id,
        "Food",
        tag_names=["Tax"],
        rule_keyword="STORE",
        amount_min=10,
        amount_max=20,
        rule_merchant_id=merchant_id,
    )

    tx = (
        core_conn.execute(
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
            ).where(transactions_table.c.id == transaction_id)
        )
        .mappings()
        .fetchone()
    )

    assert result.updated is True
    assert result.saved_rule_id is not None
    assert tx["category"] == "Food"
    assert tx["category_id"] == resolve_category_id(core_conn, "Food")
    assert tx["needs_review"] == 0
    assert tx["category_source"] == "manual"
    assert tx["category_confidence"] == 1.0
    assert tx["category_rule_id"] is None
    assert json.loads(tx["category_metadata"])["decision_source"] == "manual"
    assert tx["categorized_at"] is not None
    assert tx["reviewed_at"] is not None
    assert get_transaction_tag_names(core_conn, transaction_id) == ["Tax"]

    rule = get_transaction_rule(core_conn, result.saved_rule_id)
    assert rule == {
        "merchant_id": merchant_id,
        "keyword": "STORE",
        "category": "Food",
        "amount_min": 10.0,
        "amount_max": 20.0,
        "source": "manual",
    }
    assert get_rule_tags_by_rule_id(core_conn, [result.saved_rule_id])[result.saved_rule_id] == ["Tax"]


def test_assign_manual_category_saves_rule_and_approves_unchanged_transaction(core_conn, repository_transaction):
    """Verify saving a rule approves the current transaction even when category data is unchanged."""
    transaction_id, merchant_id = repository_transaction
    core_conn.execute(
        update(transactions_table)
        .where(transactions_table.c.id == transaction_id)
        .values(
            category="Food",
            category_id=resolve_category_id(core_conn, "Food"),
            category_source="rule",
            needs_review=0,
            reviewed_at=None,
        )
    )
    set_transaction_tags(core_conn, transaction_id, ["Tax"], source="rule")

    result = assign_manual_category(
        core_conn,
        transaction_id,
        "Food",
        tag_names=["Tax"],
        rule_keyword="STORE",
        amount_min=10,
        amount_max=20,
        rule_merchant_id=merchant_id,
    )

    tx = (
        core_conn.execute(
            select(
                transactions_table.c.category,
                transactions_table.c.category_source,
                transactions_table.c.needs_review,
                transactions_table.c.reviewed_at,
            ).where(transactions_table.c.id == transaction_id)
        )
        .mappings()
        .fetchone()
    )

    assert result.updated is True
    assert result.transaction_changed is False
    assert result.saved_rule_id is not None
    assert tx["category"] == "Food"
    assert tx["category_source"] == "rule"
    assert tx["needs_review"] == 0
    assert tx["reviewed_at"] is not None
    assert get_transaction_tag_names(core_conn, transaction_id) == ["Tax"]


def test_mark_transaction_verified_updates_review_status(core_conn, repository_transaction):
    """Verify mark transaction verified updates review status."""
    transaction_id, _ = repository_transaction

    updated = mark_transaction_verified(
        core_conn,
        transaction_id,
        reviewed_at="2026-05-08T00:00:00Z",
    )

    tx = (
        core_conn.execute(
            select(
                transactions_table.c.needs_review,
                transactions_table.c.reviewed_at,
            ).where(transactions_table.c.id == transaction_id)
        )
        .mappings()
        .fetchone()
    )
    assert updated is True
    assert tx["needs_review"] == 0
    assert tx["reviewed_at"] == "2026-05-08T00:00:00Z"


def test_set_transaction_ignored_marks_review_complete_when_ignored(core_conn, repository_transaction):
    """Verify set transaction ignored marks review complete when ignored."""
    transaction_id, _ = repository_transaction

    updated = set_transaction_ignored(core_conn, transaction_id, True)

    tx = (
        core_conn.execute(
            select(
                transactions_table.c.ignored,
                transactions_table.c.needs_review,
            ).where(transactions_table.c.id == transaction_id)
        )
        .mappings()
        .fetchone()
    )
    assert updated is True
    assert tx["ignored"] == 1
    assert tx["needs_review"] == 0


def test_missing_transaction_mutations_report_no_update(core_conn):
    """Verify missing transaction mutations report no update."""
    assert assign_manual_category(core_conn, 999, "Food", tag_names=[]).updated is False
    assert mark_transaction_verified(core_conn, 999) is False
    assert set_transaction_ignored(core_conn, 999, True) is False


def get_transaction_rule(conn, rule_id):
    """Return persisted rule fields for repository assertions."""
    from finance_app.database.tables import category_rules as category_rules_table

    row = (
        conn.execute(
            select(
                category_rules_table.c.merchant_id,
                category_rules_table.c.keyword,
                category_rules_table.c.category,
                category_rules_table.c.amount_min,
                category_rules_table.c.amount_max,
                category_rules_table.c.source,
            ).where(category_rules_table.c.id == rule_id)
        )
        .mappings()
        .fetchone()
    )
    return dict(row)
