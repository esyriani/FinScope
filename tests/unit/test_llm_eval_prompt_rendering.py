"""Unit tests for dry-run LLM categorization prompt rendering."""

from __future__ import annotations

import json
from pathlib import Path

from evals.llm_categorization.tools import render_prompt


def taxonomy() -> dict[str, object]:
    """Return a candidate taxonomy for prompt rendering tests."""
    return {
        "categories": [
            {"id": "cat_unknown", "name": "UNKNOWN", "description": "Unresolved.", "instruction": None},
            {"id": "cat_food", "name": "Food", "description": "Food purchases.", "instruction": None},
        ],
        "tags": [
            {
                "id": "tag_reimbursable",
                "name": "Reimbursable",
                "description": "Expense to reimburse.",
                "instruction": None,
            }
        ],
    }


def example(request_id: str, *, description: str = "CORNER CAFE") -> dict[str, object]:
    """Return one valid synthetic dataset example."""
    return {
        "request_id": request_id,
        "transaction": {
            "description": description,
            "merchant": "Corner cafe",
            "amount": 23.45,
            "date": "2026-05-03",
            "account": "Credit card",
            "statement_type": "credit_card",
        },
        "candidate_taxonomy": taxonomy(),
        "similar_transactions": [
            {
                "description": "CAFE",
                "amount": 20.0,
                "category_id": "cat_food",
                "tag_ids": ["tag_reimbursable"],
                "evidence_type": "history",
                "confidence": 0.91,
            }
        ],
        "expected": {"category_id": "cat_food", "tag_ids": ["tag_reimbursable"], "needs_review": False},
        "label_source": "reviewed",
        "privacy_level": "synthetic",
        "coverage": {
            "category": "Food",
            "tags": ["Reimbursable"],
            "direction": "debit",
            "statement_type": "credit_card",
            "confidence_band": "high",
            "ambiguity_type": "straightforward",
        },
        "notes": "Expected label must not be rendered.",
    }


def write_jsonl(path: Path, records: list[dict[str, object]]) -> None:
    """Write JSONL records for renderer tests."""
    path.write_text("\n".join(json.dumps(record, sort_keys=True) for record in records), encoding="utf-8")


def write_prompt(path: Path) -> None:
    """Write a prompt candidate for renderer tests."""
    path.write_text("# Test prompt\n\nReturn JSON only.", encoding="utf-8")


def test_render_prompt_writes_message_payload_without_expected_answer(tmp_path):
    """Verify one request renders the model payload and omits curation fields."""
    prompt_path = tmp_path / "prompt.md"
    dataset_path = tmp_path / "dataset.jsonl"
    out_path = tmp_path / "rendered.txt"
    write_prompt(prompt_path)
    write_jsonl(dataset_path, [example("req-1")])

    exit_code = render_prompt.main(
        ["--prompt", str(prompt_path), "--dataset", str(dataset_path), "--request-id", "req-1", "--out", str(out_path)]
    )

    rendered = json.loads(out_path.read_text(encoding="utf-8"))
    request = rendered["rendered_requests"][0]
    messages = request["message_payload"]["messages"]
    combined_content = "\n".join(message["content"] for message in messages)
    _, examples = render_prompt.load_validated_records(dataset_path)
    model_input = render_prompt.render_model_input(examples[0])

    assert exit_code == 0
    assert rendered["rendered_count"] == 1
    assert request["request_id"] == "req-1"
    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "user"
    assert "high-confidence wrong answer is worse" in combined_content
    assert "candidate_taxonomy" in combined_content
    assert "similar_transactions" in combined_content
    assert "Required JSON output format" in combined_content
    assert "expected" not in model_input
    assert "label_source" not in model_input
    assert "coverage" not in model_input
    assert "notes" not in model_input


def test_render_prompt_dry_run_renders_first_n_examples_deterministically(tmp_path):
    """Verify dry-run mode renders the first N examples in dataset order."""
    prompt_path = tmp_path / "prompt.md"
    dataset_path = tmp_path / "dataset.jsonl"
    out_path = tmp_path / "dry_run.txt"
    write_prompt(prompt_path)
    write_jsonl(dataset_path, [example("req-1"), example("req-2", description="GROCERY"), example("req-3")])

    first_exit_code = render_prompt.main(
        ["--prompt", str(prompt_path), "--dataset", str(dataset_path), "--dry-run", "2", "--out", str(out_path)]
    )
    first_output = out_path.read_text(encoding="utf-8")
    second_exit_code = render_prompt.main(
        ["--prompt", str(prompt_path), "--dataset", str(dataset_path), "--dry-run", "2", "--out", str(out_path)]
    )

    rendered = json.loads(out_path.read_text(encoding="utf-8"))
    request_ids = [request["request_id"] for request in rendered["rendered_requests"]]

    assert first_exit_code == 0
    assert second_exit_code == 0
    assert out_path.read_text(encoding="utf-8") == first_output
    assert request_ids == ["req-1", "req-2"]


def test_render_prompt_reports_missing_request_id(tmp_path, capsys):
    """Verify missing request IDs fail without writing a payload."""
    prompt_path = tmp_path / "prompt.md"
    dataset_path = tmp_path / "dataset.jsonl"
    write_prompt(prompt_path)
    write_jsonl(dataset_path, [example("req-1")])

    exit_code = render_prompt.main(
        ["--prompt", str(prompt_path), "--dataset", str(dataset_path), "--request-id", "missing"]
    )
    output = capsys.readouterr()

    assert exit_code == 1
    assert "request_id not found in dataset: missing" in output.err


def test_validate_rendered_taxonomy_ids_rejects_mismatched_category_ids(tmp_path):
    """Verify rendered taxonomy IDs are checked against the selected example."""
    dataset_path = tmp_path / "dataset.jsonl"
    write_jsonl(dataset_path, [example("req-1")])
    _, examples = render_prompt.load_validated_records(dataset_path)
    model_input = render_prompt.render_model_input(examples[0])
    model_input["candidate_taxonomy"]["categories"][1]["id"] = "cat_other"

    try:
        render_prompt.validate_rendered_taxonomy_ids(model_input, examples[0])
    except ValueError as exc:
        message = str(exc)
    else:
        raise AssertionError("taxonomy ID mismatch should fail validation")

    assert "rendered category IDs do not match" in message
