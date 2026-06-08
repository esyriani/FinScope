"""Tests for LLM provider request handling.

Verifies the OpenAI adapter behavior with deterministic fake clients. The tests
avoid network calls and focus on parsing, error handling, and log sanitization.
"""

import logging

from tests.support.llm import (
    openai_error_response,
    openai_invalid_json_response,
    openai_json_response,
)

from finance_app.modules.categories import llm


def test_request_llm_categories_parses_mocked_openai_json():
    """Verify request helper parses JSON results from a mocked OpenAI client."""
    fake_client = openai_json_response(
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
        client_factory=fake_client,
        api_key="sk-test",
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
    assert fake_client.constructor_calls == [{"api_key": "sk-test", "timeout": llm.LLM_TIMEOUT_SECONDS}]
    assert fake_client.created_calls[0]["model"] == "gpt-test"
    assert fake_client.created_calls[0]["response_format"] == {"type": "json_object"}


def test_request_llm_categories_handles_invalid_json():
    """Verify invalid model JSON is handled as no categorization results."""
    fake_client = openai_invalid_json_response()

    assert (
        llm.request_llm_categories(
            [],
            [],
            [],
            [],
            [],
            [],
            "gpt-test",
            0.9,
            0.6,
            client_factory=fake_client,
            api_key="sk-test",
        )
        == []
    )


def test_request_llm_categories_handles_api_exceptions_and_sanitizes_logs(caplog):
    """Verify OpenAI timeouts or rate-limit errors keep transactions unchanged."""
    fake_client = openai_error_response(TimeoutError("request timed out for sk-testsecret123"))

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
            client_factory=fake_client,
            api_key="sk-test",
        )

    assert results == []
    assert fake_client.constructor_calls == [{"api_key": "sk-test", "timeout": llm.LLM_TIMEOUT_SECONDS}]
    status = llm.last_llm_request_status()
    assert status["status"] == "request_error"
    assert status["error_type"] == "TimeoutError"
    assert status["requested_count"] == 1
    assert "sk-testsecret123" not in status["detail"]
    assert "sk-***" in status["detail"]
    assert "OpenAI categorization request failed: TimeoutError" in caplog.text
    assert "sk-testsecret123" not in caplog.text
    assert "sk-***" in caplog.text
