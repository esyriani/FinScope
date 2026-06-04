"""Tests for LLM prompt payload construction.

Verifies the prompt builder's privacy boundary and compact taxonomy payloads.
The assertions exercise deterministic JSON payloads without calling an LLM.
"""

import json

from finance_app.modules.categories import llm
from finance_app.modules.categories.repository import get_category_options
from finance_app.modules.categories.taxonomy import get_category_rows, get_tag_options, get_tag_rows


def test_build_llm_prompt_includes_transaction_kind_and_bank_context():
    """Verify prompts include bank context without raw statement details."""
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
    transaction = payload["transactions"][0]
    assert transaction["transaction_kind"] == "expense"
    assert transaction["amount_direction"] == "debit"
    assert transaction["amount_magnitude"] == "large"
    assert "description" not in transaction
    assert "date" not in transaction
    assert "amount" not in transaction
    assert "Bank statement context" in system_prompt
    assert "FAC" in system_prompt
    assert "Common merchant examples" in system_prompt
    assert "account_name" not in system_prompt
    assert "best supported category" in system_prompt


def test_build_llm_prompt_minimizes_evidence_and_keeps_compact_candidate_taxonomy():
    """Verify LLM prompts omit raw finance context while keeping compact hints."""
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
    payload_text = json.dumps(payload)

    assert [row["name"] for row in payload["taxonomy"]["categories"]] == ["Food", "UNKNOWN", "Utilities"]
    assert [row["name"] for row in payload["taxonomy"]["tags"]] == ["Government", "Tax"]
    assert transaction["amount_direction"] == "debit"
    assert transaction["amount_magnitude"] == "small"
    assert transaction["evidence_summary"]["best_matching_rule"] == {
        "category": "Food",
        "tags": ["Tax"],
        "confidence": 0.88,
    }
    assert transaction["evidence_summary"]["similar_transactions"] == {
        "category": "Food",
        "tags": [],
        "confidence": 0.84,
    }
    assert "description" not in transaction
    assert "date" not in transaction
    assert "amount" not in transaction
    assert "account_id" not in transaction
    assert "account_name" not in transaction
    assert "account_type" not in transaction
    assert "Metro Grocery" not in payload_text
    assert "TD Visa" not in payload_text
    assert "2026-01-02" not in payload_text
    assert "Metro previous" not in payload_text
    assert "12.34" not in payload_text
    assert [row["name"] for row in transaction["candidate_taxonomy"]["categories"]] == ["Food", "UNKNOWN"]
    assert transaction["candidate_taxonomy"]["categories"][0]["instruction"] == "Use for groceries."
    assert [row["name"] for row in transaction["candidate_taxonomy"]["tags"]] == ["Tax"]
    assert transaction["candidate_taxonomy"]["tags"][0]["instruction"] == "Tax-related."


def test_prepare_llm_candidate_taxonomies_includes_semantic_category_matches(core_conn):
    """Verify merchant text can pull semantically relevant taxonomy hints."""
    category_options = get_category_options(core_conn)
    tag_options = get_tag_options(core_conn)
    transactions = [
        {
            "merchant_key": "TVA SPORTS DIRECT",
            "description": "TVA SPORTS DIRECT",
            "amount": 20.68,
            "category": "UNKNOWN",
        }
    ]

    llm.prepare_llm_candidate_taxonomies(
        core_conn,
        transactions,
        category_options,
        tag_options,
        "UNKNOWN",
        get_category_rows(core_conn),
        get_tag_rows(core_conn),
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
