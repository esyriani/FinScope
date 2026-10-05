"""Tests for the transaction categorization workflow."""

import json
from decimal import Decimal
from types import SimpleNamespace

from tests.support.database import insert_rule, insert_transaction
from tests.support.llm import LLMRequestStub, llm_response_scenario, llm_result

from finance_app.modules.categories import categorization
from finance_app.modules.categories import llm as llm_module
from finance_app.modules.categories.categorization import categorize_transactions
from finance_app.modules.categories.history import HistoricalDecision
from finance_app.modules.categories.llm_workflow import (
    prepare_transaction_llm_categorization,
    request_prepared_transaction_llm_categorization,
)


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


def scored_rule_fixture(*, rule_id="9001", category="Food", confidence=0.95):
    """Build scored rule evidence for categorization boundary tests."""
    return SimpleNamespace(
        rule={
            "id": rule_id,
            "keyword": "BOUNDARY",
            "category": category,
            "amount_min": None,
            "amount_max": None,
            "account_id": None,
            "direction": "any",
            "source": "manual",
        },
        match_score=confidence,
        confidence=confidence,
        category=category,
        tags=("Shared",),
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

    categorized = categorize_transactions(transactions, conn=core_conn)

    metro_match, metro_out_of_range, payroll_income, payroll_positive, unknown = categorized
    assert metro_match["amount"] == Decimal("12.34")
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
    metadata = json.loads(metro_out_of_range["category_metadata"])
    assert metadata["decision_source"] == "unknown"
    assert metadata["reason"] == "insufficient_rule_or_history_evidence"
    assert metadata["review_required"] is True

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
    metadata = json.loads(unknown["category_metadata"])
    assert metadata["review_required"] is True


def test_high_confidence_rule_boundary_does_not_consult_history(core_conn, monkeypatch):
    """Verify the exact high-confidence threshold finalizes rule evidence."""

    def fail_history(*args, **kwargs):
        """Fail if exact high-confidence rule decisions ask for history."""
        del args, kwargs
        raise AssertionError("history should not be consulted at the high threshold")

    monkeypatch.setattr(categorization, "retrieve_historical_decision", fail_history)

    state = categorization.category_state_from_evidence(
        core_conn,
        {},
        scored_rule_fixture(confidence=0.95),
        ["UNKNOWN", "Food"],
        "UNKNOWN",
    )

    assert state.category == "Food"
    assert state.needs_review == 0
    assert state.assignment.category_source == "rule"
    assert state.assignment.category_confidence == 0.95


def test_medium_confidence_rule_boundary_is_still_useful(core_conn, monkeypatch):
    """Verify the exact medium-confidence threshold applies for local review."""
    monkeypatch.setattr(
        categorization,
        "retrieve_historical_decision",
        lambda *args, **kwargs: HistoricalDecision(None, (), 0.0, (), ()),
    )

    state = categorization.category_state_from_evidence(
        core_conn,
        {},
        scored_rule_fixture(confidence=0.85),
        ["UNKNOWN", "Food"],
        "UNKNOWN",
        prefer_llm_fallback=False,
    )

    assert state.category == "Food"
    assert state.needs_review == 1
    assert state.assignment.category_source == "rule"
    assert state.assignment.category_confidence == 0.85


def test_evidence_categorization_defaults_to_llm_fallback(core_conn):
    """Verify direct evidence categorization defaults to the split LLM policy."""
    rule_id = insert_rule(core_conn, "MARKET", "Food")
    transactions = [{"description": "Market Lane", "amount": 30.00}]

    categorization.categorize_transactions_from_evidence(transactions, core_conn)

    assert transactions[0]["category"] == "UNKNOWN"
    assert transactions[0]["category_source"] == "unknown"
    assert transactions[0]["needs_review"] == 1
    assert transactions[0]["rule_evidence"]["rule_id"] == rule_id
    metadata = json.loads(transactions[0]["category_metadata"])
    assert metadata["reason"] == "medium_confidence_rule_needs_llm"
    assert metadata["matched_rule_id"] == rule_id


def test_unknown_category_metadata_preserves_historical_evidence_ids():
    """Verify unresolved audit metadata keeps retrieval context intact."""
    metadata = categorization.unknown_category_metadata(
        "needs_more_evidence",
        historical_evidence={
            "category": None,
            "tags": [],
            "confidence": 0.86,
            "evidence_ids": [101, 102],
            "examples": [],
        },
    )

    assert metadata["review_required"] is True
    assert metadata["retrieval_confidence"] == 0.86
    assert metadata["similar_transaction_ids"] == [101, 102]


def test_historical_metadata_uses_value_equality_for_rule_agreement():
    """Verify audit metadata treats distinct but equal category strings as agreeing."""
    category = "Utilities and rent"
    rule_category = "".join(["Utilities", " and ", "rent"])
    assert rule_category == category
    assert rule_category is not category

    metadata = categorization.historical_category_metadata(
        HistoricalDecision(category, (), 0.95, (77,), ()),
        category,
        (),
        0.95,
        "UNKNOWN",
        scored_rule=scored_rule_fixture(rule_id="42", category=rule_category),
        rule_category=rule_category,
    )

    assert metadata["rule_agreed_with_retrieval"] is True


def test_medium_confidence_rule_is_applied_for_review_in_local_categorization(core_conn):
    """Verify ordinary local categorization keeps useful broad rules reviewable."""
    rule_id = insert_rule(core_conn, "MARKET", "Food")

    categorized = categorize_transactions(
        [{"description": "Market Lane", "amount": 30.00}],
        conn=core_conn,
    )

    assert categorized[0]["category"] == "Food"
    assert categorized[0]["category_source"] == "rule"
    assert 0.85 <= categorized[0]["category_confidence"] < 0.95
    assert categorized[0]["needs_review"] == 1
    assert categorized[0]["category_rule_id"] == rule_id
    assert categorized[0]["category_id"] is not None
    metadata = json.loads(categorized[0]["category_metadata"])
    assert metadata["decision_source"] == "rule"
    assert metadata["final_category"] == "Food"
    assert metadata["review_required"] is True
    assert metadata["matched_rule_id"] == rule_id
    assert metadata["rule"]["confidence"] == categorized[0]["category_confidence"]


def test_medium_confidence_rule_is_deferred_for_split_llm_workflow(core_conn):
    """Verify LLM preparation leaves useful broad rules for provider arbitration."""
    rule_id = insert_rule(core_conn, "MARKET", "Food")
    transactions = [{"description": "Market Lane", "amount": 30.00}]

    prepared = prepare_transaction_llm_categorization(transactions)

    assert prepared.request_context is not None
    assert transactions[0]["category"] == "UNKNOWN"
    assert transactions[0]["category_source"] == "unknown"
    assert transactions[0]["needs_review"] == 1
    assert transactions[0]["category_id"] is not None
    assert transactions[0]["rule_evidence"]["rule_id"] == rule_id
    assert transactions[0]["rule_evidence"]["category"] == "Food"
    assert 0.85 <= transactions[0]["rule_evidence"]["confidence"] < 0.95
    metadata = json.loads(transactions[0]["category_metadata"])
    assert metadata["decision_source"] == "unknown"
    assert metadata["reason"] == "medium_confidence_rule_needs_llm"
    assert metadata["review_required"] is True
    assert metadata["matched_rule_id"] == rule_id
    assert metadata["rule"]["category"] == "Food"


def test_manual_prefix_rule_auto_applies_location_suffix(core_conn):
    """Verify a strong manual prefix rule applies merchant location suffixes."""
    rule_id = insert_rule(core_conn, "COSTCO WHOLESALE", "Food", tags=["Grocery"])

    categorized = categorize_transactions(
        [{"description": "COSTCO WHOLESALE W527 MONTREAL, QC", "amount": 277.72}],
        conn=core_conn,
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


def test_categorize_transactions_defaults_to_local_evidence_only(core_conn):
    """Verify ordinary categorization leaves provider work to the split workflow."""
    categorized = categorize_transactions(
        [{"description": "Mystery Shop", "amount": 9.99}],
        conn=core_conn,
    )

    assert categorized[0]["category"] == "UNKNOWN"
    assert categorized[0]["category_source"] == "unknown"
    assert categorized[0]["needs_review"] == 1
    assert categorized[0]["category_id"] is not None


def test_high_confidence_rule_skips_history(core_conn, monkeypatch):
    """Verify a specific rule finalizes without historical retrieval."""
    rule_id = insert_rule(core_conn, "METRO", "Food", amount_min=10, amount_max=20)

    def fail_history(*args, **kwargs):
        """Fail if high-confidence rule decisions ask for historical evidence."""
        del args, kwargs
        raise AssertionError("history should not be consulted")

    monkeypatch.setattr(categorization, "retrieve_historical_decision", fail_history)

    categorized = categorize_transactions(
        [{"description": "Metro Grocery #123", "amount": 12.34}],
        conn=core_conn,
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


def test_no_rule_high_confidence_history_categorizes_locally(core_conn):
    """Verify strongly agreeing historical transactions categorize locally."""
    insert_historical_transaction(core_conn, "Hydro Quebec", 120.00, "Utilities", tags=["Government"])
    insert_historical_transaction(
        core_conn,
        "Hydro Quebec",
        119.50,
        "Utilities",
        tx_date="2026-01-03",
        tags=["Government"],
    )
    categorized = categorize_transactions(
        [{"description": "Hydro Quebec", "amount": 120.00}],
        conn=core_conn,
    )

    assert categorized[0]["category"] == "Utilities"
    assert categorized[0]["category_source"] == "history"
    assert categorized[0]["category_confidence"] >= 0.95
    assert categorized[0]["needs_review"] == 0
    assert categorized[0]["tags"] == ["Government"]
    metadata = json.loads(categorized[0]["category_metadata"])
    assert metadata["decision_source"] == "similar_transactions"


def test_split_llm_workflow_requests_and_applies_provider_results(core_conn):
    """Verify provider-backed categorization runs through the split workflow."""
    insert_historical_transaction(core_conn, "Ambiguous Shop", 10.00, "Food")
    insert_historical_transaction(core_conn, "Ambiguous Shop", 10.00, "Utilities", tx_date="2026-01-03")
    transactions = [{"description": "Ambiguous Shop", "amount": 10.00}]
    request_stub = LLMRequestStub(
        llm_response_scenario(
            llm_result(
                "Food",
                0.90,
                tags=["Tax"],
                needs_review=True,
                supported_by_similar_transactions=False,
                reason="Ambiguous merchant needs provider arbitration.",
            )
        )
    )

    prepared = prepare_transaction_llm_categorization(transactions)
    outcome = request_prepared_transaction_llm_categorization(prepared, request_categories=request_stub)
    llm_module.apply_llm_categorization_outcome(core_conn, transactions, outcome, prepared.unknown_category)

    assert len(request_stub.calls) == 1
    assert transactions[0]["category"] == "Food"
    assert transactions[0]["category_source"] == "ai"
    assert transactions[0]["needs_review"] == 1
    assert transactions[0]["tags"] == ["Tax"]
