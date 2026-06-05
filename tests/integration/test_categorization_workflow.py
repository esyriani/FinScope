"""Tests for the transaction categorization workflow."""

import json

from finance_app.modules.categories import categorization
from finance_app.modules.categories.categorization import categorize_transactions
from tests.support.database import insert_rule, insert_transaction


def insert_historical_transaction(
    conn,
    description,
    amount,
    category,
    *,
    tx_date="2026-01-01",
    account_id=None,
    source="manual",
    reviewed_at="2026-01-02T00:00:00Z",
    tags=None,
):
    """Insert a categorized historical transaction and optional tags."""
    fingerprint = f"history-{description}-{amount}-{category}-{source}-{tx_date}"
    return insert_transaction(
        conn,
        description=description,
        amount=amount,
        category=category,
        tx_date=tx_date,
        account_id=account_id,
        category_source=source,
        category_confidence=1.0 if source == "manual" else 0.95,
        needs_review=0,
        reviewed_at=reviewed_at,
        fingerprint=fingerprint,
        tags=tags or [],
        tag_source=source,
    )


def test_categorize_transactions_matches_rules_without_cross_amount_cache_bleed(core_conn):
    """Verify amount-aware rule matches do not leak to different amounts."""
    metro_rule_id = insert_rule(
        core_conn,
        "METRO",
        "Food",
        amount_min=10,
        amount_max=20,
        tags=["Tax", "Shared"],
    )
    payroll_rule_id = insert_rule(core_conn, "PAYROLL", "Income")
    transactions = [
        {"description": "Metro Grocery #123", "amount": 12.34},
        {"description": "Metro Grocery #456", "amount": 30.00},
        {"description": "Payroll Deposit", "amount": -1000.00},
        {"description": "Payroll Deposit", "amount": 1000.00},
        {"description": "Unseen Merchant", "amount": 22.00},
    ]

    categorized = categorize_transactions(transactions, conn=core_conn, use_llm=False)

    metro_match, metro_out_of_range, payroll_income, payroll_positive, unknown = categorized
    assert metro_match["merchant_key"] == "METRO GROCERY"
    assert metro_match["category"] == "Food"
    assert metro_match["needs_review"] == 0
    assert metro_match["category_source"] == "rule"
    assert metro_match["category_confidence"] >= 0.95
    assert metro_match["category_rule_id"] == metro_rule_id
    assert metro_match["categorized_at"] is not None
    assert metro_match["reviewed_at"] is None
    assert metro_match["tags"] == ["Shared", "Tax"]

    assert metro_out_of_range["category"] == "UNKNOWN"
    assert metro_out_of_range["needs_review"] == 1
    assert metro_out_of_range["category_source"] == "unknown"
    assert metro_out_of_range["category_rule_id"] is None
    assert metro_out_of_range["tags"] == []

    assert payroll_income["category"] == "Income"
    assert payroll_income["needs_review"] == 0
    assert payroll_income["category_rule_id"] == payroll_rule_id

    assert payroll_positive["category"] == "UNKNOWN"
    assert payroll_positive["needs_review"] == 1
    assert payroll_positive["category_source"] == "unknown"
    assert payroll_positive["category_rule_id"] is None

    assert unknown["category"] == "UNKNOWN"
    assert unknown["needs_review"] == 1
    assert unknown["category_source"] == "unknown"
    assert unknown["category_confidence"] is None
    assert unknown["categorized_at"] is None
    assert unknown["tags"] == []


def test_manual_prefix_rule_auto_applies_location_suffix(core_conn):
    """Verify a strong manual prefix rule applies merchant location suffixes."""
    rule_id = insert_rule(core_conn, "COSTCO WHOLESALE", "Food", tags=["Grocery"])

    categorized = categorize_transactions(
        [{"description": "COSTCO WHOLESALE W527 MONTREAL, QC", "amount": 277.72}],
        conn=core_conn,
        use_llm=False,
    )

    assert categorized[0]["category"] == "Food"
    assert categorized[0]["category_source"] == "rule"
    assert categorized[0]["category_confidence"] >= 0.95
    assert categorized[0]["needs_review"] == 0
    assert categorized[0]["category_rule_id"] == rule_id
    assert categorized[0]["tags"] == ["Grocery"]
    metadata = json.loads(categorized[0]["category_metadata"])
    assert metadata["rule"]["match_score"] == 0.94
    assert metadata["review_required"] is False


def test_categorize_transactions_invokes_llm_when_enabled(core_conn, monkeypatch):
    """Verify the workflow hands categorized rows to the optional LLM pass."""
    calls = []

    def classify_for_test(conn, transactions, rules, unknown_category):
        """Capture the LLM handoff and categorize the unknown row."""
        calls.append(
            {
                "transactions": transactions,
                "rules": rules,
                "unknown_category": unknown_category,
            }
        )
        transactions[0].update(
            {
                "category": "Personal",
                "needs_review": 0,
                "category_source": "ai",
                "category_confidence": 0.8,
                "category_rule_id": None,
                "categorized_at": "2026-05-09T00:00:00Z",
                "reviewed_at": None,
                "tags": [],
            }
        )

    monkeypatch.setattr(categorization, "classify_unknowns_with_llm", classify_for_test)

    categorized = categorize_transactions(
        [{"description": "Mystery Shop", "amount": 9.99}],
        conn=core_conn,
        use_llm=True,
    )

    assert len(calls) == 1
    assert calls[0]["unknown_category"] == "UNKNOWN"
    assert calls[0]["transactions"] is categorized
    assert categorized[0]["category"] == "Personal"
    assert categorized[0]["category_source"] == "ai"


def test_high_confidence_rule_skips_history_and_llm(core_conn, monkeypatch):
    """Verify a specific rule finalizes without retrieval or LLM fallback."""
    rule_id = insert_rule(core_conn, "METRO", "Food", amount_min=10, amount_max=20)

    def fail_history(*args, **kwargs):
        """Fail if high-confidence rule decisions ask for historical evidence."""
        del args, kwargs
        raise AssertionError("history should not be consulted")

    def fail_llm(*args, **kwargs):
        """Fail if high-confidence rule decisions ask for LLM fallback."""
        del args, kwargs
        raise AssertionError("LLM should not be called")

    monkeypatch.setattr(categorization, "retrieve_historical_decision", fail_history)
    monkeypatch.setattr(categorization, "classify_unknowns_with_llm", fail_llm)

    categorized = categorize_transactions(
        [{"description": "Metro Grocery #123", "amount": 12.34}],
        conn=core_conn,
        use_llm=True,
    )

    assert categorized[0]["category"] == "Food"
    assert categorized[0]["category_source"] == "rule"
    assert categorized[0]["category_confidence"] >= 0.95
    assert categorized[0]["needs_review"] == 0
    assert categorized[0]["category_rule_id"] == rule_id
    metadata = json.loads(categorized[0]["category_metadata"])
    assert metadata["decision_source"] == "rule"
    assert metadata["rule"]["rule_id"] == rule_id
    assert metadata["review_required"] is False


def test_medium_confidence_rule_confirmed_by_history(core_conn):
    """Verify historical agreement can raise a broad rule to high confidence."""
    rule_id = insert_rule(core_conn, "MARKET", "Food")
    insert_historical_transaction(core_conn, "Market Lane", 30.00, "Food", tags=["Shared"])

    categorized = categorize_transactions(
        [{"description": "Market Lane", "amount": 30.00}],
        conn=core_conn,
        use_llm=False,
    )

    assert categorized[0]["category"] == "Food"
    assert categorized[0]["category_source"] == "history"
    assert categorized[0]["category_confidence"] >= 0.95
    assert categorized[0]["needs_review"] == 0
    assert categorized[0]["category_rule_id"] == rule_id
    assert categorized[0]["tags"] == ["Shared"]
    metadata = json.loads(categorized[0]["category_metadata"])
    assert metadata["decision_source"] == "combined"
    assert metadata["rule_agreed_with_retrieval"] is True
    assert metadata["similar_transaction_ids"]


def test_medium_confidence_rule_contradicted_by_history_requires_review(core_conn):
    """Verify useful but conflicting evidence keeps the transaction reviewable."""
    rule_id = insert_rule(core_conn, "MARKET", "Food")
    insert_historical_transaction(core_conn, "Market Lane", 30.00, "Utilities")

    categorized = categorize_transactions(
        [{"description": "Market Lane", "amount": 30.00}],
        conn=core_conn,
        use_llm=False,
    )

    assert categorized[0]["category"] == "Utilities"
    assert categorized[0]["category_source"] == "history"
    assert 0.85 <= categorized[0]["category_confidence"] < 0.95
    assert categorized[0]["needs_review"] == 1
    assert categorized[0]["category_rule_id"] is None
    assert rule_id is not None
    metadata = json.loads(categorized[0]["category_metadata"])
    assert metadata["decision_source"] == "combined"
    assert metadata["rule_agreed_with_retrieval"] is False
    assert metadata["review_required"] is True


def test_no_rule_high_confidence_history_skips_llm(core_conn, monkeypatch):
    """Verify strongly agreeing historical transactions categorize without LLM."""
    insert_historical_transaction(core_conn, "Hydro Quebec", 120.00, "Utilities", tags=["Government"])
    insert_historical_transaction(
        core_conn,
        "Hydro Quebec",
        119.50,
        "Utilities",
        tx_date="2026-01-03",
        tags=["Government"],
    )
    llm_calls = []

    def fail_llm(*args, **kwargs):
        """Capture unexpected LLM calls."""
        llm_calls.append((args, kwargs))

    monkeypatch.setattr(categorization, "classify_unknowns_with_llm", fail_llm)

    categorized = categorize_transactions(
        [{"description": "Hydro Quebec", "amount": 120.00}],
        conn=core_conn,
        use_llm=True,
    )

    assert llm_calls == []
    assert categorized[0]["category"] == "Utilities"
    assert categorized[0]["category_source"] == "history"
    assert categorized[0]["category_confidence"] >= 0.95
    assert categorized[0]["needs_review"] == 0
    assert categorized[0]["tags"] == ["Government"]
    metadata = json.loads(categorized[0]["category_metadata"])
    assert metadata["decision_source"] == "similar_transactions"


def test_no_rule_ambiguous_history_invokes_llm(core_conn, monkeypatch):
    """Verify contradictory historical evidence falls through to LLM."""
    insert_historical_transaction(core_conn, "Ambiguous Shop", 10.00, "Food")
    insert_historical_transaction(core_conn, "Ambiguous Shop", 10.00, "Utilities", tx_date="2026-01-03")
    calls = []

    def classify_for_test(conn, transactions, rules, unknown_category):
        """Capture LLM fallback for ambiguous historical evidence."""
        calls.append((transactions, rules, unknown_category))
        transactions[0].update(
            {
                "category": "Food",
                "needs_review": 1,
                "category_source": "ai",
                "category_confidence": 0.90,
                "category_rule_id": None,
                "categorized_at": "2026-05-09T00:00:00Z",
                "reviewed_at": None,
                "tags": [],
            }
        )

    monkeypatch.setattr(categorization, "classify_unknowns_with_llm", classify_for_test)

    categorized = categorize_transactions(
        [{"description": "Ambiguous Shop", "amount": 10.00}],
        conn=core_conn,
        use_llm=True,
    )

    assert len(calls) == 1
    assert categorized[0]["category"] == "Food"
    assert categorized[0]["category_source"] == "ai"
    assert categorized[0]["needs_review"] == 1
