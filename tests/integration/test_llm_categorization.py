"""Tests for LLM-assisted categorization internals."""

import json

import pytest
from sqlalchemy import text
from tests.support.database import set_owner_setting
from tests.support.llm import (
    LLMRequestStub,
    compact_candidates_for_test,
    invalid_category_result,
    invalid_tag_result,
    llm_response_scenario,
    llm_result,
    result_payload,
    taxonomy_id,
    unknown_transaction,
)

from finance_app.modules.categories import llm, llm_estimation
from finance_app.modules.categories.llm_tokens import DEFAULT_EXPECTED_OUTPUT_TOKENS

"""
These tests are designed to verify the internal logic of the LLM categorization adapter, not the behavior of a specific model. They use deterministic mocked responses to ensure consistent test results and avoid external dependencies. If these tests are failing, focus on the adapter's handling of LLM results, integration with rules and retrieval, and metadata recording rather than the content of the mocked LLM responses.
IMPORTANT: No LLM is called in these tests, so no API keys or network access are required. The LLM response is fully mocked to return deterministic results for various scenarios, including accepted categories, confidence levels, and failure modes.
"""


class CharacterEncoding:
    """Deterministic test encoding that counts one token per character."""

    name = "character-test"

    def encode(self, value):
        """Return one fake token per character."""
        return list(str(value))


def test_pair_llm_results_uses_request_ids_and_positional_fallback():
    """Verify LLM response pairing prefers request ids and falls back to order."""
    unknown_items = [
        {"llm_request_id": "0", "description": "first"},
        {"llm_request_id": "1", "description": "second"},
    ]

    request_id_pairs = list(
        llm.pair_llm_results(
            unknown_items,
            [
                {"request_id": "1", "category": "Food"},
                {"request_id": "0", "category": "Utilities"},
            ],
        )
    )
    positional_pairs = list(
        llm.pair_llm_results(
            unknown_items,
            [
                {"category": "Food"},
                {"category": "Utilities"},
            ],
        )
    )

    assert request_id_pairs == [
        (unknown_items[0], {"request_id": "0", "category": "Utilities"}),
        (unknown_items[1], {"request_id": "1", "category": "Food"}),
    ]
    assert positional_pairs == [
        (unknown_items[0], {"category": "Food"}),
        (unknown_items[1], {"category": "Utilities"}),
    ]


def test_classify_unknowns_with_llm_applies_thresholds_and_filters_invalid_values(core_conn):
    """Verify accepted LLM results update transactions conservatively."""
    set_owner_setting(core_conn, "llm_confidence_threshold", "0.80")
    set_owner_setting(core_conn, "verify_threshold", "0.90")
    core_conn.commit()
    transactions = [
        unknown_transaction("Metro Grocery 1", "METRO", 12.34),
        unknown_transaction("Metro Grocery 2", "METRO", 12.34),
        unknown_transaction("Hydro Quebec", "HYDRO", 120.00),
        unknown_transaction("Low Confidence", "LOW", 10.00),
        unknown_transaction("Invalid Category", "INVALID", 15.00),
    ]

    request_stub = LLMRequestStub(
        llm_response_scenario(
            llm_result(
                "Utilities",
                0.86,
                tags=["Government"],
                request_id="1",
                needs_review=False,
                supported_by_similar_transactions=False,
                reason="Hydro is likely a utility bill.",
            ),
            llm_result(
                "Food",
                0.95,
                tags=["Tax"],
                request_id="0",
                needs_review=False,
                supported_by_similar_transactions=True,
                reason="Metro is a grocery merchant.",
            ),
            llm_result("Food", 0.50, tags=["Tax"], request_id="2", needs_review=False),
            invalid_category_result(0.99, tags=["Tax"], request_id="3", needs_review=False),
        )
    )

    llm.classify_unknowns_with_llm(
        core_conn,
        transactions,
        [],
        "UNKNOWN",
        request_categories=request_stub,
    )

    assert [[tx["llm_request_id"] for tx in call["unknown_chunk"]] for call in request_stub.calls] == [
        ["0", "1", "2", "3"]
    ]
    assert transactions[0]["category"] == "Food"
    assert transactions[0]["needs_review"] == 0
    assert transactions[0]["category_source"] == "ai"
    assert transactions[0]["category_confidence"] == 0.95
    assert transactions[0]["category_rule_id"] is not None
    assert transactions[0]["tags"] == ["Tax"]
    metadata = json.loads(transactions[0]["category_metadata"])
    assert metadata["decision_source"] == "llm"
    assert isinstance(metadata["llm_category_id"], int)
    assert metadata["llm_tag_ids"]
    assert metadata["llm_reason"] == "Metro is a grocery merchant."
    assert metadata["supported_by_similar_transactions"] is True
    assert transactions[1]["category"] == "Food"
    assert transactions[1]["category_rule_id"] == transactions[0]["category_rule_id"]
    assert json.loads(transactions[1]["category_metadata"]) == metadata
    assert transactions[1]["tags"] == ["Tax"]
    assert transactions[2]["category"] == "Utilities"
    assert transactions[2]["needs_review"] == 1
    assert transactions[2]["category_source"] == "ai"
    assert transactions[2]["category_confidence"] == 0.86
    assert transactions[2]["category_rule_id"] is None
    assert transactions[2]["tags"] == ["Government"]
    metadata = json.loads(transactions[2]["category_metadata"])
    assert metadata["decision_source"] == "llm"
    assert metadata["review_required"] is True
    assert transactions[3]["category"] == "UNKNOWN"
    assert transactions[3]["tags"] == []
    metadata = json.loads(transactions[3]["category_metadata"])
    assert metadata["failure_reason"] == "confidence_below_review_threshold"
    assert transactions[4]["category"] == "UNKNOWN"
    assert transactions[4]["tags"] == []
    metadata = json.loads(transactions[4]["category_metadata"])
    assert metadata["failure_reason"] == "invalid_category_id"
    rules = core_conn.execute(text("""
        SELECT keyword, category, amount_min, amount_max, source
        FROM category_rules
        ORDER BY keyword
        """)).fetchall()
    assert [tuple(rule) for rule in rules] == [("METRO", "Food", 0.0, None, "automatic")]


def test_estimate_llm_categorization_tokens_uses_final_prompt_batches(core_conn):
    """Verify token estimates are built from final prepared LLM batches."""
    transactions = [unknown_transaction("Metro Grocery", "METRO", 12.34)]

    estimate = llm_estimation.estimate_llm_categorization_tokens(
        core_conn,
        transactions,
        [],
        "UNKNOWN",
        prepare_candidate_taxonomies=compact_candidates_for_test,
        batch_size=1,
        encoding_factory=lambda _model: CharacterEncoding(),
    )

    assert estimate["model"]
    assert estimate["request_count"] == 1
    assert estimate["batch_count"] == 1
    assert estimate["input_tokens"] > 0
    assert estimate["expected_output_tokens"] == DEFAULT_EXPECTED_OUTPUT_TOKENS
    assert estimate["total_tokens"] == estimate["input_tokens"] + estimate["expected_output_tokens"]
    assert estimate["max_batch_input_tokens"] == estimate["input_tokens"]
    assert estimate["tokenizer_available"] is True
    assert estimate["batches"][0]["request_count"] == 1


def test_default_llm_provider_requires_split_transaction_boundary(core_conn):
    """Verify the default provider cannot run inside an active DB transaction."""
    transactions = [unknown_transaction("Metro Grocery", "METRO", 12.34)]

    with pytest.raises(RuntimeError, match="split prepare/request/apply workflow"):
        llm.classify_unknowns_with_llm(core_conn, transactions, [], "UNKNOWN")


def test_classify_unknowns_with_llm_can_skip_automatic_rule_creation(core_conn):
    """Verify one-off LLM runs can apply a category without saving a future rule."""
    transactions = [
        unknown_transaction("Metro Grocery 1", "METRO", 12.34),
    ]

    request_stub = LLMRequestStub(llm_response_scenario(llm_result("Food", 0.95, tags=["Tax"], needs_review=False)))

    llm.classify_unknowns_with_llm(
        core_conn,
        transactions,
        [],
        "UNKNOWN",
        save_automatic_rules=False,
        request_categories=request_stub,
    )

    rule_count = core_conn.execute(text("SELECT COUNT(*) AS count FROM category_rules")).fetchone()._mapping["count"]
    assert transactions[0]["category"] == "Food"
    assert transactions[0]["needs_review"] == 0
    assert transactions[0]["category_rule_id"] is None
    assert rule_count == 0


def test_classify_unknowns_with_llm_keeps_review_worthy_best_fit(core_conn):
    """Verify lower-confidence LLM category suggestions are kept for review."""
    transactions = [
        unknown_transaction("Sports streaming package", "SPORTS STREAMING", 20.68),
    ]

    request_stub = LLMRequestStub(
        llm_response_scenario(llm_result("Entertainment", 0.60, tags=["Service"], needs_review=True))
    )

    llm.classify_unknowns_with_llm(
        core_conn,
        transactions,
        [],
        "UNKNOWN",
        request_categories=request_stub,
    )

    assert transactions[0]["category"] == "Entertainment"
    assert transactions[0]["needs_review"] == 1
    assert transactions[0]["category_confidence"] == 0.60
    assert transactions[0]["category_rule_id"] is None
    assert transactions[0]["tags"] == ["Service"]
    metadata = json.loads(transactions[0]["category_metadata"])
    assert metadata["final_category"] == "Entertainment"
    assert metadata["proposed_confidence"] == 0.60
    assert metadata["review_required"] is True
    assert "failure_reason" not in metadata


def test_classify_unknowns_with_llm_uses_review_threshold_setting(core_conn):
    """Verify the runtime review threshold controls best-fit LLM suggestions."""
    set_owner_setting(core_conn, "llm_review_threshold", "0.70")
    core_conn.commit()
    transactions = [
        unknown_transaction("Sports streaming package", "SPORTS STREAMING", 20.68),
    ]

    request_stub = LLMRequestStub(
        llm_response_scenario(llm_result("Entertainment", 0.69, tags=["Service"], needs_review=True))
    )

    llm.classify_unknowns_with_llm(
        core_conn,
        transactions,
        [],
        "UNKNOWN",
        request_categories=request_stub,
    )

    assert transactions[0]["category"] == "UNKNOWN"
    assert transactions[0]["tags"] == []
    metadata = json.loads(transactions[0]["category_metadata"])
    assert metadata["proposed_category"] == "Entertainment"
    assert metadata["failure_reason"] == "confidence_below_review_threshold"


def test_classify_unknowns_with_llm_deduplicates_and_skips_non_candidates(core_conn):
    """Verify only unique unknown merchant/sign pairs are sent to the LLM."""
    transactions = [
        unknown_transaction("Metro Grocery one", "METRO", 12.34),
        unknown_transaction("Metro Grocery duplicate", "METRO", 12.34),
        unknown_transaction("Metro Refund", "METRO", -12.34),
        {"description": "Known Metro", "merchant_key": "METRO", "amount": 15.00, "category": "Food"},
        {"description": "Missing merchant", "amount": 10.00, "category": "UNKNOWN", "tags": []},
    ]

    request_stub = LLMRequestStub(
        llm_response_scenario(
            llm_result("Food", 0.96, tags=["Tax"], request_id=None, needs_review=False),
            llm_result("Income", 0.97, request_id=None, needs_review=False),
        )
    )

    llm.classify_unknowns_with_llm(
        core_conn,
        transactions,
        [],
        "UNKNOWN",
        request_categories=request_stub,
    )

    assert [
        [(tx["description"], tx["llm_request_id"]) for tx in call["unknown_chunk"]] for call in request_stub.calls
    ] == [[("Metro Grocery one", "0"), ("Metro Refund", "1")]]
    assert transactions[0]["category"] == "Food"
    assert transactions[0]["category_source"] == "ai"
    assert transactions[0]["category_rule_id"] is not None
    assert transactions[0]["tags"] == ["Tax"]
    assert transactions[1]["category"] == "Food"
    assert transactions[1]["category_source"] == "ai"
    assert transactions[1]["category_rule_id"] == transactions[0]["category_rule_id"]
    assert transactions[1]["tags"] == ["Tax"]
    assert transactions[2]["category"] == "Income"
    assert transactions[2]["category_source"] == "ai"
    assert transactions[3] == {
        "description": "Known Metro",
        "merchant_key": "METRO",
        "amount": 15.00,
        "category": "Food",
    }
    assert transactions[4] == {
        "description": "Missing merchant",
        "amount": 10.00,
        "category": "UNKNOWN",
        "tags": [],
    }
    rules = core_conn.execute(text("""
        SELECT keyword, category, amount_min, amount_max, source
        FROM category_rules
        ORDER BY amount_min IS NOT NULL DESC, category
        """)).fetchall()
    assert [tuple(rule) for rule in rules] == [
        ("METRO", "Food", 0.0, None, "automatic"),
        ("METRO", "Income", None, 0.0, "automatic"),
    ]


def test_classify_unknowns_with_llm_boosts_agreement_with_rule_and_history(core_conn):
    """Verify agreement across LLM, rule, and retrieval can finalize a decision."""
    transactions = [
        {
            **unknown_transaction("Metro Grocery", "METRO", 12.34),
            "rule_evidence": {
                "category": "Food",
                "tags": ["Tax"],
                "confidence": 0.86,
            },
            "historical_evidence": {
                "category": "Food",
                "tags": ["Tax"],
                "confidence": 0.90,
                "evidence_ids": [22],
                "examples": [],
            },
        }
    ]

    request_stub = LLMRequestStub(
        llm_response_scenario(
            llm_result(
                "Food",
                0.89,
                tags=["Tax"],
                needs_review=True,
                supported_by_similar_transactions=True,
                reason="Rule and prior transactions agree.",
            )
        )
    )

    llm.classify_unknowns_with_llm(
        core_conn,
        transactions,
        [],
        "UNKNOWN",
        request_categories=request_stub,
    )

    assert transactions[0]["category"] == "Food"
    assert transactions[0]["needs_review"] == 0
    assert transactions[0]["category_confidence"] >= 0.95
    assert transactions[0]["category_rule_id"] is not None
    metadata = json.loads(transactions[0]["category_metadata"])
    assert metadata["decision_source"] == "llm_with_similar_transactions"
    assert metadata["rule_agreed_with_llm"] is True
    assert metadata["retrieval_agreed_with_llm"] is True
    assert metadata["supported_by_similar_transactions"] is True


def test_classify_unknowns_with_llm_penalizes_strong_history_disagreement(core_conn):
    """Verify strong retrieval disagreement lowers LLM confidence and requires review."""
    transactions = [
        {
            **unknown_transaction("Market Lane", "MARKET", 30.00),
            "rule_evidence": {
                "category": "Food",
                "tags": [],
                "confidence": 0.86,
            },
            "historical_evidence": {
                "category": "Utilities",
                "tags": [],
                "confidence": 0.95,
                "evidence_ids": [31, 32],
                "examples": [],
            },
        }
    ]

    request_stub = LLMRequestStub(
        llm_response_scenario(
            llm_result(
                "Food",
                0.96,
                needs_review=False,
                supported_by_similar_transactions=False,
                reason="Merchant name looks like food.",
            )
        )
    )

    llm.classify_unknowns_with_llm(
        core_conn,
        transactions,
        [],
        "UNKNOWN",
        request_categories=request_stub,
    )

    assert transactions[0]["category"] == "Food"
    assert transactions[0]["needs_review"] == 1
    assert transactions[0]["category_confidence"] < 0.95
    assert transactions[0]["category_rule_id"] is None
    metadata = json.loads(transactions[0]["category_metadata"])
    assert metadata["decision_source"] == "llm_with_similar_transactions"
    assert metadata["rule_agreed_with_llm"] is True
    assert metadata["retrieval_agreed_with_llm"] is False
    assert metadata["review_required"] is True


def test_classify_unknowns_with_llm_marks_three_way_disagreement_for_review(core_conn):
    """Verify disagreement across rule, retrieval, and LLM keeps confidence below high."""
    transactions = [
        {
            **unknown_transaction("Market Lane", "MARKET", 30.00),
            "rule_evidence": {
                "rule_id": 12,
                "category": "Food",
                "tags": [],
                "confidence": 0.90,
            },
            "historical_evidence": {
                "category": "Utilities",
                "tags": [],
                "confidence": 0.92,
                "evidence_ids": [31, 32],
                "examples": [
                    {
                        "transaction_id": 33,
                        "description": "Prior personal store purchase",
                        "category": "Personal",
                        "tags": [],
                    }
                ],
            },
        }
    ]

    request_stub = LLMRequestStub(
        llm_response_scenario(
            llm_result(
                "Personal",
                0.96,
                needs_review=False,
                supported_by_similar_transactions=False,
                reason="The merchant could be a personal purchase.",
            )
        )
    )

    llm.classify_unknowns_with_llm(
        core_conn,
        transactions,
        [],
        "UNKNOWN",
        request_categories=request_stub,
    )

    assert transactions[0]["category"] == "Personal"
    assert transactions[0]["needs_review"] == 1
    assert transactions[0]["category_confidence"] < 0.95
    metadata = json.loads(transactions[0]["category_metadata"])
    assert metadata["rule_agreed_with_llm"] is False
    assert metadata["retrieval_agreed_with_llm"] is False
    assert metadata["matched_rule_id"] == 12
    assert metadata["retrieval_confidence"] == 0.92


def test_classify_unknowns_with_llm_passes_taxonomy_rules_and_runtime_settings(core_conn):
    """Verify the LLM adapter receives taxonomy metadata and central thresholds."""
    set_owner_setting(core_conn, "llm_confidence_threshold", "0.82")
    set_owner_setting(core_conn, "llm_review_threshold", "0.64")
    set_owner_setting(core_conn, "verify_threshold", "0.74")
    set_owner_setting(core_conn, "openai_model", "gpt-unit")
    core_conn.commit()
    rules = [
        {
            "id": 10,
            "keyword": "METRO",
            "category": "Food",
            "amount_min": None,
            "amount_max": None,
            "source": "manual",
            "tags": ["Tax"],
        }
    ]
    transactions = [unknown_transaction("Metro Grocery", "METRO", 12.34)]
    captured = {}

    def response_for_test(
        unknown_chunk,
        requested_rules,
        category_options,
        tag_options,
        category_rows,
        tag_rows,
        openai_model,
        verify_threshold,
        review_threshold,
    ):
        """Capture adapter inputs and return one accepted result."""
        captured.update(
            {
                "unknown_chunk": [dict(tx) for tx in unknown_chunk],
                "rules": requested_rules,
                "category_options": category_options,
                "tag_options": tag_options,
                "category_rows": category_rows,
                "tag_rows": tag_rows,
                "openai_model": openai_model,
                "verify_threshold": verify_threshold,
                "review_threshold": review_threshold,
            }
        )
        return [
            result_payload(
                category_rows,
                tag_rows,
                "0",
                "Food",
                0.91,
                tags=["Tax"],
                needs_review=False,
            )
        ]

    request_stub = LLMRequestStub(response_for_test)

    llm.classify_unknowns_with_llm(
        core_conn,
        transactions,
        rules,
        "UNKNOWN",
        request_categories=request_stub,
    )

    assert captured["unknown_chunk"][0]["llm_request_id"] == "0"
    assert captured["rules"] is rules
    assert "Food" in captured["category_options"]
    assert "UNKNOWN" in captured["category_options"]
    assert "Tax" in captured["tag_options"]
    assert any(row["name"] == "Food" and row["instruction"] for row in captured["category_rows"])
    assert any(row["name"] == "Tax" and row["instruction"] for row in captured["tag_rows"])
    assert captured["openai_model"] == "gpt-unit"
    assert captured["verify_threshold"] == 0.74
    assert captured["review_threshold"] == 0.64
    assert transactions[0]["category"] == "Food"
    assert transactions[0]["category_confidence"] == 0.91
    assert transactions[0]["needs_review"] == 0
    assert transactions[0]["category_rule_id"] is not None
    metadata = json.loads(transactions[0]["category_metadata"])
    assert metadata["final_confidence"] == 0.91
    assert metadata["review_required"] is False


def test_classify_unknowns_with_llm_continues_after_empty_chunk(core_conn):
    """Verify an empty LLM response only skips that chunk."""
    transactions = [
        unknown_transaction("Merchant A", "MERCHANT A", 1.00),
        unknown_transaction("Merchant B", "MERCHANT B", 2.00),
        unknown_transaction("Merchant C", "MERCHANT C", 3.00),
    ]
    captured_chunks = []

    def response_for_test(unknown_chunk, *args):
        """Return no results for the first chunk and a result for the next."""
        category_rows = args[3]
        tag_rows = args[4]
        captured_chunks.append([tx["merchant_key"] for tx in unknown_chunk])
        if len(captured_chunks) == 1:
            return []
        return [
            result_payload(
                category_rows,
                tag_rows,
                unknown_chunk[0]["llm_request_id"],
                "Food",
                0.96,
                tags=[],
                needs_review=False,
            )
        ]

    request_stub = LLMRequestStub(response_for_test)

    llm.classify_unknowns_with_llm(
        core_conn,
        transactions,
        [],
        "UNKNOWN",
        request_categories=request_stub,
        batch_size=2,
    )

    assert captured_chunks == [["MERCHANT A", "MERCHANT B"], ["MERCHANT C"]]
    assert [tx["category"] for tx in transactions] == ["UNKNOWN", "UNKNOWN", "Food"]
    assert transactions[0]["category_source"] == "unknown"
    assert transactions[1]["category_source"] == "unknown"
    assert json.loads(transactions[0]["category_metadata"])["failure_reason"] == "llm_no_results"
    assert json.loads(transactions[1]["category_metadata"])["failure_reason"] == "llm_no_results"
    assert transactions[2]["category_source"] == "ai"
    assert transactions[2]["category_rule_id"] is not None


def test_classify_unknowns_with_llm_records_missing_results_as_unknown(core_conn):
    """Verify omitted LLM results become explicit unknown decisions with metadata."""
    transactions = [
        unknown_transaction("Merchant A", "MERCHANT A", 1.00),
        unknown_transaction("Merchant B", "MERCHANT B", 2.00),
    ]

    request_stub = LLMRequestStub(llm_response_scenario(llm_result("Food", 0.96, needs_review=False)))

    llm.classify_unknowns_with_llm(
        core_conn,
        transactions,
        [],
        "UNKNOWN",
        request_categories=request_stub,
    )

    assert transactions[0]["category"] == "Food"
    assert transactions[0]["needs_review"] == 0
    assert transactions[1]["category"] == "UNKNOWN"
    assert transactions[1]["needs_review"] == 1
    assert transactions[1]["category_source"] == "unknown"
    metadata = json.loads(transactions[1]["category_metadata"])
    assert metadata["failure_reason"] == "llm_missing_result"


def test_classify_unknowns_with_llm_keeps_custom_unknown_category_on_unknown_result(core_conn):
    """Verify an explicit UNKNOWN model result maps back to the caller's unknown label."""
    transactions = [
        {
            "description": "Ambiguous Merchant",
            "merchant_key": "AMBIGUOUS",
            "amount": 12.34,
            "category": "Needs Review",
            "tags": [],
        }
    ]

    request_stub = LLMRequestStub(llm_response_scenario(llm_result("UNKNOWN", 0.99, needs_review=True)))

    llm.classify_unknowns_with_llm(
        core_conn,
        transactions,
        [],
        "Needs Review",
        request_categories=request_stub,
    )

    assert transactions[0]["category"] == "Needs Review"
    assert transactions[0]["needs_review"] == 1
    assert transactions[0]["category_source"] == "unknown"
    assert transactions[0]["category_confidence"] is None
    assert transactions[0]["tags"] == []
    metadata = json.loads(transactions[0]["category_metadata"])
    assert metadata["decision_source"] == "llm"
    assert metadata["proposed_category"] == "Needs Review"
    assert metadata["review_required"] is True


def test_classify_unknowns_with_llm_accepts_full_taxonomy_category_for_review(core_conn):
    """Verify valid outside-candidate categories are accepted for review."""
    set_owner_setting(core_conn, "llm_confidence_threshold", "0.80")
    core_conn.commit()
    transactions = [
        {
            **unknown_transaction("Metro Grocery", "METRO", 12.34),
            "rule_evidence": {
                "category": "Food",
                "tags": ["Tax"],
                "confidence": 0.88,
            },
        }
    ]
    captured = {}

    def response_for_test(unknown_chunk, requested_rules, category_options, tag_options, category_rows, *args):
        """Return a category that exists globally but not in this compact taxonomy."""
        del requested_rules, tag_options, args
        captured["category_options"] = category_options
        captured["candidate_categories"] = list(unknown_chunk[0]["llm_candidate_categories"])
        return [
            {
                "request_id": unknown_chunk[0]["llm_request_id"],
                "category_id": taxonomy_id(category_rows, "Travel"),
                "confidence": 0.96,
                "needs_review": False,
                "tag_ids": [],
            }
        ]

    request_stub = LLMRequestStub(response_for_test)

    llm.classify_unknowns_with_llm(
        core_conn,
        transactions,
        [],
        "UNKNOWN",
        request_categories=request_stub,
        prepare_candidate_taxonomies=compact_candidates_for_test,
    )

    assert "Food" in captured["category_options"]
    assert "Travel" in captured["category_options"]
    assert "Travel" not in captured["candidate_categories"]
    assert transactions[0]["category"] == "Travel"
    assert transactions[0]["needs_review"] == 1
    assert transactions[0]["category_rule_id"] is None
    assert transactions[0]["tags"] == []
    metadata = json.loads(transactions[0]["category_metadata"])
    assert metadata["category_outside_candidate_taxonomy"] is True
    assert metadata["full_taxonomy_fallback_used"] is True
    assert metadata["full_taxonomy_fallback_rejected"] is False
    assert "failure_reason" not in metadata


def test_classify_unknowns_with_llm_keeps_medium_confidence_full_taxonomy_category(core_conn):
    """Verify outside-candidate categories no longer require high confidence."""
    transactions = [
        {
            **unknown_transaction("Metro Grocery", "METRO", 12.34),
            "rule_evidence": {
                "category": "Food",
                "tags": ["Tax"],
                "confidence": 0.88,
            },
        }
    ]

    request_stub = LLMRequestStub(llm_response_scenario(llm_result("Travel", 0.94, needs_review=True)))

    llm.classify_unknowns_with_llm(
        core_conn,
        transactions,
        [],
        "UNKNOWN",
        request_categories=request_stub,
        prepare_candidate_taxonomies=compact_candidates_for_test,
    )

    assert transactions[0]["category"] == "Travel"
    assert transactions[0]["tags"] == []
    assert transactions[0]["needs_review"] == 1
    metadata = json.loads(transactions[0]["category_metadata"])
    assert metadata["category_outside_candidate_taxonomy"] is True
    assert metadata["full_taxonomy_fallback_used"] is True
    assert metadata["full_taxonomy_fallback_rejected"] is False
    assert "failure_reason" not in metadata


def test_classify_unknowns_with_llm_keeps_full_taxonomy_tags(core_conn):
    """Verify valid outside-candidate tags are kept as LLM suggestions."""
    transactions = [
        {
            **unknown_transaction("Metro Grocery", "METRO", 12.34),
            "rule_evidence": {
                "category": "Food",
                "tags": ["Tax"],
                "confidence": 0.88,
            },
        }
    ]

    request_stub = LLMRequestStub(
        llm_response_scenario(llm_result("Food", 0.99, tags=["Government"], needs_review=False))
    )

    llm.classify_unknowns_with_llm(
        core_conn,
        transactions,
        [],
        "UNKNOWN",
        request_categories=request_stub,
        prepare_candidate_taxonomies=compact_candidates_for_test,
    )

    assert transactions[0]["category"] == "Food"
    assert transactions[0]["tags"] == ["Government"]
    assert transactions[0]["needs_review"] == 0
    assert transactions[0]["category_rule_id"] is not None
    metadata = json.loads(transactions[0]["category_metadata"])
    assert metadata["tag_ids_outside_candidate_taxonomy"] == metadata["llm_tag_ids"]
    assert metadata["review_required"] is False
    assert "failure_reason" not in metadata


def test_classify_unknowns_with_llm_drops_invalid_tag_ids_without_losing_category(core_conn):
    """Verify invalid tag IDs are dropped while the valid category is kept for review."""
    transactions = [
        {
            **unknown_transaction("Metro Grocery", "METRO", 12.34),
            "rule_evidence": {
                "category": "Food",
                "tags": ["Tax"],
                "confidence": 0.88,
            },
        }
    ]

    request_stub = LLMRequestStub(llm_response_scenario(invalid_tag_result("Food", 0.99, needs_review=False)))

    llm.classify_unknowns_with_llm(
        core_conn,
        transactions,
        [],
        "UNKNOWN",
        request_categories=request_stub,
    )

    assert transactions[0]["category"] == "Food"
    assert transactions[0]["tags"] == []
    assert transactions[0]["needs_review"] == 1
    assert transactions[0]["category_rule_id"] is None
    metadata = json.loads(transactions[0]["category_metadata"])
    assert "failure_reason" not in metadata
    assert metadata["dropped_invalid_tag_ids"] == [999999]
    assert metadata["llm_category_id"] is not None
    assert metadata["llm_tag_ids"] == []


def test_classify_unknowns_with_llm_rejects_confidence_outside_probability_range(core_conn):
    """Verify invalid confidence values are treated as unaccepted results."""
    transactions = [
        {
            **unknown_transaction("Metro Grocery", "METRO", 12.34),
            "rule_evidence": {
                "category": "Food",
                "tags": ["Tax"],
                "confidence": 0.88,
            },
        }
    ]

    request_stub = LLMRequestStub(llm_response_scenario(llm_result("Food", 1.20, tags=["Tax"], needs_review=False)))

    llm.classify_unknowns_with_llm(
        core_conn,
        transactions,
        [],
        "UNKNOWN",
        request_categories=request_stub,
    )

    assert transactions[0]["category"] == "UNKNOWN"
    assert transactions[0]["tags"] == []
    metadata = json.loads(transactions[0]["category_metadata"])
    assert metadata["failure_reason"] == "invalid_confidence"
