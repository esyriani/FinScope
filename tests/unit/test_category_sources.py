"""Tests for category assignment source metadata helpers."""

import json

from finance_app.modules.categories.sources import category_metadata_json


def test_category_metadata_json_normalizes_decision_source_values():
    """Verify structured metadata uses the controlled audit source vocabulary."""
    metadata = category_metadata_json(
        {
            "decision_source": "history",
            "nested": {"decision_source": "ai"},
            "items": [{"decision_source": "manual"}],
        }
    )

    parsed = json.loads(metadata)
    assert parsed["decision_source"] == "similar_transactions"
    assert parsed["nested"]["decision_source"] == "llm"
    assert parsed["items"][0]["decision_source"] == "manual"


def test_category_metadata_json_normalizes_serialized_metadata():
    """Verify JSON strings are normalized before category metadata is persisted."""
    metadata = category_metadata_json('{"decision_source": "ai", "review_required": true}')

    assert json.loads(metadata)["decision_source"] == "llm"
