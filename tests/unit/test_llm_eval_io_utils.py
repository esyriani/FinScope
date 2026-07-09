"""Unit tests for offline LLM categorization JSONL helpers."""

from __future__ import annotations

import json

from evals.llm_categorization.tools import io_utils


def test_write_and_read_jsonl_round_trips_sorted_objects(tmp_path):
    """Verify JSONL helpers write deterministic objects and read them back."""
    path = tmp_path / "records.jsonl"
    records = [{"b": 2, "a": 1}, {"id": "two", "value": None}]

    row_count = io_utils.write_jsonl(path, records)

    assert row_count == 2
    assert path.read_text(encoding="utf-8").splitlines()[0] == '{"a": 1, "b": 2}'
    assert io_utils.load_jsonl(path) == records


def test_read_jsonl_skips_empty_lines_and_accepts_utf8_bom(tmp_path):
    """Verify reader tolerates BOM and blank lines."""
    path = tmp_path / "records.jsonl"
    path.write_text("\ufeff" + json.dumps({"id": "one"}) + "\n\n" + json.dumps({"id": "two"}), encoding="utf-8")

    rows = list(io_utils.read_jsonl(path))

    assert rows == [(1, {"id": "one"}), (3, {"id": "two"})]


def test_read_jsonl_rejects_invalid_json_with_file_context(tmp_path):
    """Verify invalid JSON reports path and line number."""
    path = tmp_path / "bad.jsonl"
    path.write_text('{"ok": true}\n{bad-json}\n', encoding="utf-8")

    try:
        list(io_utils.read_jsonl(path))
    except io_utils.JsonlError as exc:
        message = str(exc)
    else:
        raise AssertionError("invalid JSONL should fail")

    assert str(path) in message
    assert ":2:" in message
    assert "invalid JSON" in message
