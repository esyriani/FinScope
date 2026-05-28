"""Tests for LLM-assisted categorization internals."""

import json
import logging
import sys
from types import SimpleNamespace

from finance_app.modules.categories import llm
from finance_app.modules.categories.repository import get_category_options
from finance_app.modules.categories.taxonomy import get_category_rows, get_tag_options, get_tag_rows

"""
These tests are designed to verify the internal logic of the LLM categorization adapter, not the behavior of a specific model. They use deterministic mocked responses to ensure consistent test results and avoid external dependencies. If these tests are failing, focus on the adapter's handling of LLM results, integration with rules and retrieval, and metadata recording rather than the content of the mocked LLM responses.
IMPORTANT: No LLM is called in these tests, so no API keys or network access are required. The LLM response is fully mocked to return deterministic results for various scenarios, including accepted categories, confidence levels, and failure modes.
"""

def unknown_transaction(description, merchant_key, amount):
    """Build an unknown transaction payload for LLM categorization tests."""
    return {
        "description": description,
        "merchant_key": merchant_key,
        "amount": amount,
        "category": "UNKNOWN",
        "tags": [],
    }


def taxonomy_id(rows, name):
    """Return the taxonomy ID for a category or tag name in mocked LLM tests."""
    for row in rows:
        if row["name"] == name:
            return row["id"]
    raise AssertionError(f"Missing taxonomy row for {name}")


def set_owner_setting(conn, key, value):
    """Persist a runtime setting for the seeded owner account."""
    conn.execute(
        """
        UPDATE user_settings
        SET value = :value
        WHERE key = :key
          AND user_id = (SELECT id FROM users WHERE username = 'owner')
        """,
        {"key": key, "value": value},
    )


def compact_candidates_for_test(conn, unknown_items, category_options, tag_options, unknown_category, *args):
    """Attach narrow candidate taxonomies for fallback-policy tests."""
    del conn, category_options, tag_options, args
    for tx in unknown_items:
        tx["llm_candidate_categories"] = ["Food", unknown_category]
        tx["llm_candidate_tags"] = ["Tax"]


def result_payload(category_rows, tag_rows, request_id, category, confidence, tags=None, **extra):
    """Build a strict ID-based mocked LLM result."""
    payload = {
        "category_id": taxonomy_id(category_rows, category),
        "tag_ids": [taxonomy_id(tag_rows, tag) for tag in (tags or [])],
        "confidence": confidence,
    }
    if request_id is not None:
        payload["request_id"] = request_id
    payload.update(extra)
    return payload


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


def test_classify_unknowns_with_llm_applies_thresholds_and_filters_invalid_values(db_conn, monkeypatch):
    """Verify accepted LLM results update transactions conservatively."""
    set_owner_setting(db_conn, "llm_confidence_threshold", "0.80")
    set_owner_setting(db_conn, "verify_threshold", "0.90")
    db_conn.commit()
    transactions = [
        unknown_transaction("Metro Grocery 1", "METRO", 12.34),
        unknown_transaction("Metro Grocery 2", "METRO", 12.34),
        unknown_transaction("Hydro Quebec", "HYDRO", 120.00),
        unknown_transaction("Low Confidence", "LOW", 10.00),
        unknown_transaction("Invalid Category", "INVALID", 15.00),
    ]
    captured_chunks = []

    def request_for_test(unknown_chunk, *args):
        """Return out-of-order deterministic LLM results."""
        category_rows = args[3]
        tag_rows = args[4]
        captured_chunks.append([tx["llm_request_id"] for tx in unknown_chunk])
        return [
            result_payload(
                category_rows,
                tag_rows,
                "1",
                "Utilities",
                0.86,
                tags=["Government"],
                needs_review=False,
                supported_by_similar_transactions=False,
                reason="Hydro is likely a utility bill.",
            ),
            result_payload(
                category_rows,
                tag_rows,
                "0",
                "Food",
                0.95,
                tags=["Tax"],
                needs_review=False,
                supported_by_similar_transactions=True,
                reason="Metro is a grocery merchant.",
            ),
            result_payload(
                category_rows,
                tag_rows,
                "2",
                "Food",
                0.50,
                tags=["Tax"],
                needs_review=False,
            ),
            {
                "request_id": "3",
                "category_id": 999999,
                "confidence": 0.99,
                "needs_review": False,
                "tag_ids": [taxonomy_id(tag_rows, "Tax")],
            },
        ]

    monkeypatch.setattr(llm, "request_llm_categories", request_for_test)

    llm.classify_unknowns_with_llm(db_conn, transactions, [], "UNKNOWN")

    assert captured_chunks == [["0", "1", "2", "3"]]
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
    rules = db_conn.execute(
        """
        SELECT keyword, category, amount_min, amount_max, source
        FROM category_rules
        ORDER BY keyword
        """
    ).fetchall()
    assert [tuple(rule) for rule in rules] == [("METRO", "Food", 0.0, None, "automatic")]


def test_classify_unknowns_with_llm_can_skip_automatic_rule_creation(db_conn, monkeypatch):
    """Verify one-off LLM runs can apply a category without saving a future rule."""
    transactions = [
        unknown_transaction("Metro Grocery 1", "METRO", 12.34),
    ]

    def request_for_test(unknown_chunk, *args):
        """Return a no-review result that would normally create an automatic rule."""
        category_rows = args[3]
        tag_rows = args[4]
        return [
            result_payload(
                category_rows,
                tag_rows,
                unknown_chunk[0]["llm_request_id"],
                "Food",
                0.95,
                tags=["Tax"],
                needs_review=False,
            )
        ]

    monkeypatch.setattr(llm, "request_llm_categories", request_for_test)

    llm.classify_unknowns_with_llm(
        db_conn,
        transactions,
        [],
        "UNKNOWN",
        save_automatic_rules=False,
    )

    rule_count = db_conn.execute("SELECT COUNT(*) AS count FROM category_rules").fetchone()["count"]
    assert transactions[0]["category"] == "Food"
    assert transactions[0]["needs_review"] == 0
    assert transactions[0]["category_rule_id"] is None
    assert rule_count == 0


def test_classify_unknowns_with_llm_keeps_review_worthy_best_fit(db_conn, monkeypatch):
    """Verify lower-confidence LLM category suggestions are kept for review."""
    transactions = [
        unknown_transaction("Sports streaming package", "SPORTS STREAMING", 20.68),
    ]

    def request_for_test(unknown_chunk, *args):
        """Return a best-fit entertainment result below the medium threshold."""
        category_rows = args[3]
        tag_rows = args[4]
        return [
            result_payload(
                category_rows,
                tag_rows,
                unknown_chunk[0]["llm_request_id"],
                "Entertainment",
                0.60,
                tags=["Service"],
                needs_review=True,
            )
        ]

    monkeypatch.setattr(llm, "request_llm_categories", request_for_test)

    llm.classify_unknowns_with_llm(db_conn, transactions, [], "UNKNOWN")

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


def test_classify_unknowns_with_llm_uses_review_threshold_setting(db_conn, monkeypatch):
    """Verify the runtime review threshold controls best-fit LLM suggestions."""
    set_owner_setting(db_conn, "llm_review_threshold", "0.70")
    db_conn.commit()
    transactions = [
        unknown_transaction("Sports streaming package", "SPORTS STREAMING", 20.68),
    ]

    def request_for_test(unknown_chunk, *args):
        """Return a category below the configured review floor."""
        category_rows = args[3]
        tag_rows = args[4]
        return [
            result_payload(
                category_rows,
                tag_rows,
                unknown_chunk[0]["llm_request_id"],
                "Entertainment",
                0.69,
                tags=["Service"],
                needs_review=True,
            )
        ]

    monkeypatch.setattr(llm, "request_llm_categories", request_for_test)

    llm.classify_unknowns_with_llm(db_conn, transactions, [], "UNKNOWN")

    assert transactions[0]["category"] == "UNKNOWN"
    assert transactions[0]["tags"] == []
    metadata = json.loads(transactions[0]["category_metadata"])
    assert metadata["proposed_category"] == "Entertainment"
    assert metadata["failure_reason"] == "confidence_below_review_threshold"


def test_classify_unknowns_with_llm_deduplicates_and_skips_non_candidates(db_conn, monkeypatch):
    """Verify only unique unknown merchant/sign pairs are sent to the LLM."""
    transactions = [
        unknown_transaction("Metro Grocery one", "METRO", 12.34),
        unknown_transaction("Metro Grocery duplicate", "METRO", 12.34),
        unknown_transaction("Metro Refund", "METRO", -12.34),
        {"description": "Known Metro", "merchant_key": "METRO", "amount": 15.00, "category": "Food"},
        {"description": "Missing merchant", "amount": 10.00, "category": "UNKNOWN", "tags": []},
    ]
    captured_chunks = []

    def request_for_test(unknown_chunk, *args):
        """Return positional results to exercise the fallback pairing path."""
        category_rows = args[3]
        tag_rows = args[4]
        captured_chunks.append([(tx["description"], tx["llm_request_id"]) for tx in unknown_chunk])
        return [
            result_payload(category_rows, tag_rows, None, "Food", 0.96, tags=["Tax"], needs_review=False),
            result_payload(category_rows, tag_rows, None, "Income", 0.97, tags=[], needs_review=False),
        ]

    monkeypatch.setattr(llm, "request_llm_categories", request_for_test)

    llm.classify_unknowns_with_llm(db_conn, transactions, [], "UNKNOWN")

    assert captured_chunks == [[("Metro Grocery one", "0"), ("Metro Refund", "1")]]
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
    rules = db_conn.execute(
        """
        SELECT keyword, category, amount_min, amount_max, source
        FROM category_rules
        ORDER BY amount_min IS NOT NULL DESC, category
        """
    ).fetchall()
    assert [tuple(rule) for rule in rules] == [
        ("METRO", "Food", 0.0, None, "automatic"),
        ("METRO", "Income", None, 0.0, "automatic"),
    ]


def test_classify_unknowns_with_llm_boosts_agreement_with_rule_and_history(db_conn, monkeypatch):
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

    def request_for_test(unknown_chunk, *args):
        """Return a medium-confidence result that agreement should lift."""
        category_rows = args[3]
        tag_rows = args[4]
        return [
            result_payload(
                category_rows,
                tag_rows,
                unknown_chunk[0]["llm_request_id"],
                "Food",
                0.89,
                tags=["Tax"],
                needs_review=True,
                supported_by_similar_transactions=True,
                reason="Rule and prior transactions agree.",
            )
        ]

    monkeypatch.setattr(llm, "request_llm_categories", request_for_test)

    llm.classify_unknowns_with_llm(db_conn, transactions, [], "UNKNOWN")

    assert transactions[0]["category"] == "Food"
    assert transactions[0]["needs_review"] == 0
    assert transactions[0]["category_confidence"] >= 0.95
    assert transactions[0]["category_rule_id"] is not None
    metadata = json.loads(transactions[0]["category_metadata"])
    assert metadata["decision_source"] == "llm_with_similar_transactions"
    assert metadata["rule_agreed_with_llm"] is True
    assert metadata["retrieval_agreed_with_llm"] is True
    assert metadata["supported_by_similar_transactions"] is True


def test_classify_unknowns_with_llm_penalizes_strong_history_disagreement(db_conn, monkeypatch):
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

    def request_for_test(unknown_chunk, *args):
        """Return a strong LLM result that conflicts with stronger retrieval."""
        category_rows = args[3]
        tag_rows = args[4]
        return [
            result_payload(
                category_rows,
                tag_rows,
                unknown_chunk[0]["llm_request_id"],
                "Food",
                0.96,
                tags=[],
                needs_review=False,
                supported_by_similar_transactions=False,
                reason="Merchant name looks like food.",
            )
        ]

    monkeypatch.setattr(llm, "request_llm_categories", request_for_test)

    llm.classify_unknowns_with_llm(db_conn, transactions, [], "UNKNOWN")

    assert transactions[0]["category"] == "Food"
    assert transactions[0]["needs_review"] == 1
    assert transactions[0]["category_confidence"] < 0.95
    assert transactions[0]["category_rule_id"] is None
    metadata = json.loads(transactions[0]["category_metadata"])
    assert metadata["decision_source"] == "llm_with_similar_transactions"
    assert metadata["rule_agreed_with_llm"] is True
    assert metadata["retrieval_agreed_with_llm"] is False
    assert metadata["review_required"] is True


def test_classify_unknowns_with_llm_marks_three_way_disagreement_for_review(db_conn, monkeypatch):
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

    def request_for_test(unknown_chunk, *args):
        """Return a third plausible category that conflicts with both sources."""
        category_rows = args[3]
        tag_rows = args[4]
        return [
            result_payload(
                category_rows,
                tag_rows,
                unknown_chunk[0]["llm_request_id"],
                "Personal",
                0.96,
                tags=[],
                needs_review=False,
                supported_by_similar_transactions=False,
                reason="The merchant could be a personal purchase.",
            )
        ]

    monkeypatch.setattr(llm, "request_llm_categories", request_for_test)

    llm.classify_unknowns_with_llm(db_conn, transactions, [], "UNKNOWN")

    assert transactions[0]["category"] == "Personal"
    assert transactions[0]["needs_review"] == 1
    assert transactions[0]["category_confidence"] < 0.95
    metadata = json.loads(transactions[0]["category_metadata"])
    assert metadata["rule_agreed_with_llm"] is False
    assert metadata["retrieval_agreed_with_llm"] is False
    assert metadata["matched_rule_id"] == 12
    assert metadata["retrieval_confidence"] == 0.92


def test_classify_unknowns_with_llm_passes_taxonomy_rules_and_runtime_settings(db_conn, monkeypatch):
    """Verify the LLM adapter receives taxonomy metadata and central thresholds."""
    set_owner_setting(db_conn, "llm_confidence_threshold", "0.82")
    set_owner_setting(db_conn, "llm_review_threshold", "0.64")
    set_owner_setting(db_conn, "verify_threshold", "0.74")
    set_owner_setting(db_conn, "openai_model", "gpt-unit")
    db_conn.commit()
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

    def request_for_test(
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

    monkeypatch.setattr(llm, "request_llm_categories", request_for_test)

    llm.classify_unknowns_with_llm(db_conn, transactions, rules, "UNKNOWN")

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


def test_classify_unknowns_with_llm_continues_after_empty_chunk(db_conn, monkeypatch):
    """Verify an empty LLM response only skips that chunk."""
    transactions = [
        unknown_transaction("Merchant A", "MERCHANT A", 1.00),
        unknown_transaction("Merchant B", "MERCHANT B", 2.00),
        unknown_transaction("Merchant C", "MERCHANT C", 3.00),
    ]
    captured_chunks = []

    def request_for_test(unknown_chunk, *args):
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

    monkeypatch.setattr(llm, "LLM_BATCH_SIZE", 2)
    monkeypatch.setattr(llm, "request_llm_categories", request_for_test)

    llm.classify_unknowns_with_llm(db_conn, transactions, [], "UNKNOWN")

    assert captured_chunks == [["MERCHANT A", "MERCHANT B"], ["MERCHANT C"]]
    assert [tx["category"] for tx in transactions] == ["UNKNOWN", "UNKNOWN", "Food"]
    assert transactions[0]["category_source"] == "unknown"
    assert transactions[1]["category_source"] == "unknown"
    assert json.loads(transactions[0]["category_metadata"])["failure_reason"] == "llm_no_results"
    assert json.loads(transactions[1]["category_metadata"])["failure_reason"] == "llm_no_results"
    assert transactions[2]["category_source"] == "ai"
    assert transactions[2]["category_rule_id"] is not None


def test_classify_unknowns_with_llm_records_missing_results_as_unknown(db_conn, monkeypatch):
    """Verify omitted LLM results become explicit unknown decisions with metadata."""
    transactions = [
        unknown_transaction("Merchant A", "MERCHANT A", 1.00),
        unknown_transaction("Merchant B", "MERCHANT B", 2.00),
    ]

    def request_for_test(unknown_chunk, *args):
        """Return only one of two requested transaction results."""
        category_rows = args[3]
        tag_rows = args[4]
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

    monkeypatch.setattr(llm, "request_llm_categories", request_for_test)

    llm.classify_unknowns_with_llm(db_conn, transactions, [], "UNKNOWN")

    assert transactions[0]["category"] == "Food"
    assert transactions[0]["needs_review"] == 0
    assert transactions[1]["category"] == "UNKNOWN"
    assert transactions[1]["needs_review"] == 1
    assert transactions[1]["category_source"] == "unknown"
    metadata = json.loads(transactions[1]["category_metadata"])
    assert metadata["failure_reason"] == "llm_missing_result"


def test_classify_unknowns_with_llm_keeps_custom_unknown_category_on_unknown_result(db_conn, monkeypatch):
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

    def request_for_test(unknown_chunk, *args):
        """Return a high-confidence UNKNOWN result."""
        category_rows = args[3]
        return [
            {
                "request_id": "0",
                "category_id": taxonomy_id(category_rows, "UNKNOWN"),
                "confidence": 0.99,
                "needs_review": True,
                "tag_ids": [],
            }
        ]

    monkeypatch.setattr(llm, "request_llm_categories", request_for_test)

    llm.classify_unknowns_with_llm(db_conn, transactions, [], "Needs Review")

    assert transactions[0]["category"] == "Needs Review"
    assert transactions[0]["needs_review"] == 1
    assert transactions[0]["category_source"] == "unknown"
    assert transactions[0]["category_confidence"] is None
    assert transactions[0]["tags"] == []
    metadata = json.loads(transactions[0]["category_metadata"])
    assert metadata["decision_source"] == "llm"
    assert metadata["proposed_category"] == "Needs Review"
    assert metadata["review_required"] is True


def test_request_llm_categories_parses_mocked_openai_json(monkeypatch):
    """Verify request helper parses JSON results from a mocked OpenAI client."""
    created_calls = []

    class FakeCompletions:
        """Fake chat completions endpoint."""

        def create(self, **kwargs):
            """Return a deterministic JSON response."""
            created_calls.append(kwargs)
            content = json.dumps(
                {
                    "results": [
                        {
                            "request_id": "0",
                            "category_id": 2,
                            "confidence": 0.95,
                            "needs_review": False,
                            "tag_ids": [1],
                        }
                    ]
                }
            )
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content=content),
                    )
                ]
            )

    class FakeOpenAI:
        """Fake OpenAI client constructor."""

        def __init__(self, api_key, timeout):
            """Capture constructor arguments through endpoint calls."""
            self.chat = SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=FakeOpenAI))
    monkeypatch.setattr(
        llm,
        "settings",
        SimpleNamespace(
            openai_api_key="sk-test",
            default_verify_threshold=0.9,
            default_categorization_model="gpt-test",
        ),
    )

    results = llm.request_llm_categories(
        [{"llm_request_id": "0", "merchant_key": "METRO", "description": "Metro", "amount": 12.34}],
        [],
        ["UNKNOWN", "Food"],
        ["Tax"],
        [
            {"id": 1, "name": "UNKNOWN", "description": "", "instruction": ""},
            {"id": 2, "name": "Food", "description": "food", "instruction": "food"},
        ],
        [{"id": 1, "name": "Tax", "description": "tax", "instruction": "tax"}],
        "gpt-test",
        0.9,
        0.6,
    )

    assert results == [
        {
            "request_id": "0",
            "category_id": 2,
            "confidence": 0.95,
            "needs_review": False,
            "tag_ids": [1],
        }
    ]
    assert created_calls[0]["model"] == "gpt-test"
    assert created_calls[0]["response_format"] == {"type": "json_object"}


def test_build_llm_prompt_includes_transaction_kind_and_bank_context():
    """Verify prompts include bank-statement context and transaction kind hints."""
    prompt = llm.build_llm_prompt(
        [
            {
                "llm_request_id": "0",
                "merchant_key": "HYDRO QUEBEC",
                "description": "HYDRO-QUEBEC FAC",
                "amount": 120.00,
                "tx_date": "2026-01-02",
                "category": "UNKNOWN",
                "transaction_kind": "expense",
            }
        ],
        [],
        ["UNKNOWN", "Utilities"],
    )
    system_prompt = llm.build_llm_system_prompt(
        [{"id": 2, "name": "Utilities", "description": "utilities", "instruction": "utilities"}],
        [],
        0.9,
    )

    payload = json.loads(prompt)
    assert payload["transactions"][0]["transaction_kind"] == "expense"
    assert "Bank statement context" in system_prompt
    assert "FAC" in system_prompt
    assert "Common merchant examples" in system_prompt
    assert "account_name" in system_prompt
    assert "best supported category" in system_prompt


def test_build_llm_prompt_includes_evidence_and_compact_candidate_taxonomy():
    """Verify LLM prompts include compact taxonomy and local evidence."""
    prompt = llm.build_llm_prompt(
        [
            {
                "llm_request_id": "0",
                "merchant_key": "METRO",
                "description": "Metro Grocery",
                "amount": 12.34,
                "tx_date": "2026-01-02",
                "account_id": 4,
                "account_name": "TD Visa",
                "account_type": "credit",
                "category": "UNKNOWN",
                "transaction_kind": "expense",
                "rule_evidence": {
                    "rule_id": 7,
                    "keyword": "METRO",
                    "category": "Food",
                    "tags": ["Tax"],
                    "confidence": 0.88,
                },
                "historical_evidence": {
                    "category": "Food",
                    "confidence": 0.84,
                    "examples": [
                        {
                            "transaction_id": 12,
                            "description": "Metro previous",
                            "amount": 10.00,
                            "category": "Food",
                            "tags": ["Tax"],
                            "score": 0.82,
                        }
                    ],
                },
                "llm_candidate_categories": ["Food", "UNKNOWN"],
                "llm_candidate_tags": ["Tax"],
            }
        ],
        [],
        ["Food", "UNKNOWN", "Utilities"],
        ["Government", "Tax"],
        [
            {"id": 2, "name": "Food", "description": "Food purchases", "instruction": "Use for groceries."},
            {"id": 1, "name": "UNKNOWN", "description": "Unknown", "instruction": ""},
            {"id": 3, "name": "Utilities", "description": "Utilities", "instruction": ""},
        ],
        [
            {"id": 1, "name": "Government", "description": "Government", "instruction": ""},
            {"id": 2, "name": "Tax", "description": "Tax", "instruction": "Tax-related."},
        ],
    )

    payload = json.loads(prompt)
    transaction = payload["transactions"][0]

    assert [row["name"] for row in payload["taxonomy"]["categories"]] == ["Food", "UNKNOWN", "Utilities"]
    assert [row["name"] for row in payload["taxonomy"]["tags"]] == ["Government", "Tax"]
    assert transaction["best_matching_rule"]["rule_id"] == 7
    assert transaction["similar_transactions"]["examples"][0]["transaction_id"] == 12
    assert transaction["account_name"] == "TD Visa"
    assert transaction["account_type"] == "credit"
    assert [row["name"] for row in transaction["candidate_taxonomy"]["categories"]] == ["Food", "UNKNOWN"]
    assert transaction["candidate_taxonomy"]["categories"][0]["instruction"] == "Use for groceries."
    assert [row["name"] for row in transaction["candidate_taxonomy"]["tags"]] == ["Tax"]
    assert transaction["candidate_taxonomy"]["tags"][0]["instruction"] == "Tax-related."


def test_prepare_llm_candidate_taxonomies_includes_semantic_category_matches(db_conn):
    """Verify merchant text can pull semantically relevant taxonomy hints."""
    category_options = get_category_options(db_conn)
    tag_options = get_tag_options(db_conn)
    transactions = [
        {
            "merchant_key": "TVA SPORTS DIRECT",
            "description": "TVA SPORTS DIRECT",
            "amount": 20.68,
            "category": "UNKNOWN",
        }
    ]

    llm.prepare_llm_candidate_taxonomies(
        db_conn,
        transactions,
        category_options,
        tag_options,
        "UNKNOWN",
        get_category_rows(db_conn),
        get_tag_rows(db_conn),
    )

    categories = transactions[0]["llm_candidate_categories"]
    assert "Entertainment" in categories
    assert categories.index("Entertainment") < categories.index("Food")


def test_build_llm_prompt_sends_only_relevant_manual_rules():
    """Verify prompt manual-rule context is scoped to the current batch."""
    prompt = llm.build_llm_prompt(
        [
            {
                "llm_request_id": "0",
                "merchant_key": "TVA SPORTS DIRECT",
                "description": "TVA SPORTS DIRECT",
                "amount": 20.68,
                "tx_date": "2026-05-04",
                "category": "UNKNOWN",
            }
        ],
        [
            {
                "id": 1,
                "keyword": "TVA SPORTS",
                "category": "Entertainment",
                "amount_min": None,
                "amount_max": None,
                "account_id": None,
                "direction": "any",
                "source": "manual",
                "tags": ["Service"],
            },
            {
                "id": 2,
                "keyword": "METRO",
                "category": "Food",
                "amount_min": None,
                "amount_max": None,
                "account_id": None,
                "direction": "any",
                "source": "manual",
                "tags": ["Grocery"],
            },
        ],
        ["UNKNOWN", "Entertainment", "Food"],
        ["Service", "Grocery"],
        [
            {"id": 1, "name": "UNKNOWN", "description": "", "instruction": ""},
            {"id": 2, "name": "Entertainment", "description": "Sports and streaming.", "instruction": ""},
            {"id": 3, "name": "Food", "description": "Food.", "instruction": ""},
        ],
        [
            {"id": 1, "name": "Service", "description": "Service.", "instruction": ""},
            {"id": 2, "name": "Grocery", "description": "Grocery.", "instruction": ""},
        ],
    )

    payload = json.loads(prompt)
    assert [rule["keyword"] for rule in payload["current_manual_rules"]] == ["TVA SPORTS"]
    assert payload["current_manual_rules"][0]["category"] == "Entertainment"
    assert payload["current_manual_rules"][0]["tags"] == ["Service"]


def test_classify_unknowns_with_llm_accepts_full_taxonomy_category_for_review(db_conn, monkeypatch):
    """Verify valid outside-candidate categories are accepted for review."""
    set_owner_setting(db_conn, "llm_confidence_threshold", "0.80")
    db_conn.commit()
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

    def request_for_test(unknown_chunk, requested_rules, category_options, tag_options, category_rows, *args):
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

    monkeypatch.setattr(llm, "request_llm_categories", request_for_test)
    monkeypatch.setattr(llm, "prepare_llm_candidate_taxonomies", compact_candidates_for_test)

    llm.classify_unknowns_with_llm(db_conn, transactions, [], "UNKNOWN")

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


def test_classify_unknowns_with_llm_keeps_medium_confidence_full_taxonomy_category(db_conn, monkeypatch):
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

    def request_for_test(unknown_chunk, *args):
        """Return a plausible full-taxonomy category below the high threshold."""
        category_rows = args[3]
        return [
            {
                "request_id": unknown_chunk[0]["llm_request_id"],
                "category_id": taxonomy_id(category_rows, "Travel"),
                "confidence": 0.94,
                "needs_review": True,
                "tag_ids": [],
            }
        ]

    monkeypatch.setattr(llm, "request_llm_categories", request_for_test)
    monkeypatch.setattr(llm, "prepare_llm_candidate_taxonomies", compact_candidates_for_test)

    llm.classify_unknowns_with_llm(db_conn, transactions, [], "UNKNOWN")

    assert transactions[0]["category"] == "Travel"
    assert transactions[0]["tags"] == []
    assert transactions[0]["needs_review"] == 1
    metadata = json.loads(transactions[0]["category_metadata"])
    assert metadata["category_outside_candidate_taxonomy"] is True
    assert metadata["full_taxonomy_fallback_used"] is True
    assert metadata["full_taxonomy_fallback_rejected"] is False
    assert "failure_reason" not in metadata


def test_classify_unknowns_with_llm_keeps_full_taxonomy_tags(db_conn, monkeypatch):
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

    def request_for_test(unknown_chunk, *args):
        """Return a valid tag ID outside the compact candidate taxonomy."""
        category_rows = args[3]
        tag_rows = args[4]
        return [
            {
                "request_id": unknown_chunk[0]["llm_request_id"],
                "category_id": taxonomy_id(category_rows, "Food"),
                "confidence": 0.99,
                "needs_review": False,
                "tag_ids": [taxonomy_id(tag_rows, "Government")],
            }
        ]

    monkeypatch.setattr(llm, "request_llm_categories", request_for_test)
    monkeypatch.setattr(llm, "prepare_llm_candidate_taxonomies", compact_candidates_for_test)

    llm.classify_unknowns_with_llm(db_conn, transactions, [], "UNKNOWN")

    assert transactions[0]["category"] == "Food"
    assert transactions[0]["tags"] == ["Government"]
    assert transactions[0]["needs_review"] == 0
    assert transactions[0]["category_rule_id"] is not None
    metadata = json.loads(transactions[0]["category_metadata"])
    assert metadata["tag_ids_outside_candidate_taxonomy"] == metadata["llm_tag_ids"]
    assert metadata["review_required"] is False
    assert "failure_reason" not in metadata


def test_classify_unknowns_with_llm_drops_invalid_tag_ids_without_losing_category(db_conn, monkeypatch):
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

    def request_for_test(unknown_chunk, *args):
        """Return a valid category ID with an invalid tag ID."""
        category_rows = args[3]
        return [
            {
                "request_id": unknown_chunk[0]["llm_request_id"],
                "category_id": taxonomy_id(category_rows, "Food"),
                "confidence": 0.99,
                "needs_review": False,
                "tag_ids": [999999],
            }
        ]

    monkeypatch.setattr(llm, "request_llm_categories", request_for_test)

    llm.classify_unknowns_with_llm(db_conn, transactions, [], "UNKNOWN")

    assert transactions[0]["category"] == "Food"
    assert transactions[0]["tags"] == []
    assert transactions[0]["needs_review"] == 1
    assert transactions[0]["category_rule_id"] is None
    metadata = json.loads(transactions[0]["category_metadata"])
    assert "failure_reason" not in metadata
    assert metadata["dropped_invalid_tag_ids"] == [999999]
    assert metadata["llm_category_id"] is not None
    assert metadata["llm_tag_ids"] == []


def test_classify_unknowns_with_llm_rejects_confidence_outside_probability_range(db_conn, monkeypatch):
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

    def request_for_test(unknown_chunk, *args):
        """Return an otherwise valid category with an invalid confidence."""
        category_rows = args[3]
        tag_rows = args[4]
        return [
            result_payload(
                category_rows,
                tag_rows,
                unknown_chunk[0]["llm_request_id"],
                "Food",
                1.20,
                tags=["Tax"],
                needs_review=False,
            )
        ]

    monkeypatch.setattr(llm, "request_llm_categories", request_for_test)

    llm.classify_unknowns_with_llm(db_conn, transactions, [], "UNKNOWN")

    assert transactions[0]["category"] == "UNKNOWN"
    assert transactions[0]["tags"] == []
    metadata = json.loads(transactions[0]["category_metadata"])
    assert metadata["failure_reason"] == "invalid_confidence"


def test_request_llm_categories_handles_invalid_json(monkeypatch):
    """Verify invalid model JSON is handled as no categorization results."""
    class FakeCompletions:
        """Fake chat completions endpoint returning bad JSON."""

        def create(self, **kwargs):
            """Return malformed JSON content."""
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content="{not-json"),
                    )
                ]
            )

    class FakeOpenAI:
        """Fake OpenAI client constructor."""

        def __init__(self, api_key, timeout):
            """Attach fake completions endpoint."""
            self.chat = SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=FakeOpenAI))
    monkeypatch.setattr(
        llm,
        "settings",
        SimpleNamespace(
            openai_api_key="sk-test",
            default_verify_threshold=0.9,
            default_categorization_model="gpt-test",
        ),
    )

    assert llm.request_llm_categories([], [], [], [], [], [], "gpt-test", 0.9, 0.6) == []


def test_request_llm_categories_handles_api_exceptions_and_sanitizes_logs(monkeypatch, caplog):
    """Verify OpenAI timeouts or rate-limit errors keep transactions unchanged."""
    class FakeCompletions:
        """Fake chat completions endpoint raising an API-style failure."""

        def create(self, **kwargs):
            """Raise a timeout containing a key-like value that must be masked."""
            del kwargs
            raise TimeoutError("request timed out for sk-testsecret123")

    class FakeOpenAI:
        """Fake OpenAI client constructor."""

        def __init__(self, api_key, timeout):
            """Attach fake completions endpoint and validate timeout wiring."""
            assert api_key == "sk-test"
            assert timeout == llm.LLM_TIMEOUT_SECONDS
            self.chat = SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=FakeOpenAI))
    monkeypatch.setattr(
        llm,
        "settings",
        SimpleNamespace(
            openai_api_key="sk-test",
            default_verify_threshold=0.9,
            default_categorization_model="gpt-test",
        ),
    )

    with caplog.at_level(logging.WARNING, logger=llm.logger.name):
        results = llm.request_llm_categories(
            [{"llm_request_id": "0", "merchant_key": "METRO", "description": "Metro", "amount": 12.34}],
            [],
            ["UNKNOWN", "Food"],
            [],
            [{"name": "UNKNOWN", "description": "", "instruction": ""}],
            [],
            "gpt-test",
            0.9,
            0.6,
        )

    assert results == []
    status = llm.last_llm_request_status()
    assert status["status"] == "request_error"
    assert status["error_type"] == "TimeoutError"
    assert status["requested_count"] == 1
    assert "sk-testsecret123" not in status["detail"]
    assert "sk-***" in status["detail"]
    assert "OpenAI categorization request failed: TimeoutError" in caplog.text
    assert "sk-testsecret123" not in caplog.text
    assert "sk-***" in caplog.text
