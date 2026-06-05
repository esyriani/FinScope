"""Tests for category assignment source metadata helpers."""

import json
import re
import warnings

from finance_app.modules.categories.sources import category_metadata_json, category_source_label, utc_timestamp


UTC_TIMESTAMP_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


def test_category_source_label_uses_similarity_for_history_source():
    """Verify the persisted history source is displayed as similarity."""
    assert category_source_label("history") == "Similarity"


def test_utc_timestamp_does_not_emit_naive_utc_deprecation_warning():
    """Verify category timestamps use an aware UTC clock and keep the Z format."""
    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        timestamp = utc_timestamp()

    assert UTC_TIMESTAMP_PATTERN.match(timestamp)


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
