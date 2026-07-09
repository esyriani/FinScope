"""Unit tests for LLM categorization run comparison reports."""

from __future__ import annotations

import json
from pathlib import Path

from evals.llm_categorization.tools import compare_runs


def write_json(path: Path, payload: dict[str, object]) -> None:
    """Write a JSON object for comparison tests."""
    path.write_text(f"{json.dumps(payload, sort_keys=True)}\n", encoding="utf-8")


def write_jsonl(path: Path, records: list[dict[str, object]]) -> None:
    """Write JSONL records for comparison tests."""
    path.write_text("\n".join(json.dumps(record, sort_keys=True) for record in records) + "\n", encoding="utf-8")


def expected(
    category_id: str, category_name: str, tag_ids: list[str] | None = None, needs_review: bool = False
) -> dict[str, object]:
    """Return an expected scoring block."""
    return {
        "category_id": category_id,
        "category_name": category_name,
        "tag_ids": tag_ids or [],
        "needs_review": needs_review,
    }


def predicted(
    category_id: str,
    category_name: str,
    tag_ids: list[str] | None = None,
    *,
    needs_review: bool = False,
    confidence: float = 0.8,
) -> dict[str, object]:
    """Return a predicted scoring block."""
    return {
        "category_id": category_id,
        "category_name": category_name,
        "tag_ids": tag_ids or [],
        "confidence": confidence,
        "needs_review": needs_review,
        "supported_by_similar_transactions": False,
        "reason": "Synthetic comparison output.",
    }


def scored_record(
    request_id: str,
    *,
    expected_block: dict[str, object],
    predicted_block: dict[str, object],
    exact: bool,
    unsafe: bool = False,
    failure_modes: list[str] | None = None,
) -> dict[str, object]:
    """Return one scored output row."""
    return {
        "request_id": request_id,
        "expected": expected_block,
        "predicted": predicted_block,
        "validity": {
            "valid_json": True,
            "schema_valid": True,
            "valid_category_id": True,
            "valid_tag_ids": True,
            "valid_taxonomy_ids": True,
        },
        "scores": {
            "category_correct": predicted_block["category_id"] == expected_block["category_id"],
            "known_category_correct": predicted_block["category_id"] == expected_block["category_id"],
            "exact_taxonomy_match": exact,
            "unsafe_auto_assignment": unsafe,
            "high_confidence_wrong": unsafe,
            "tag_true_positives": 0,
            "tag_false_positives": 0,
            "tag_false_negatives": 0,
        },
        "errors": [],
        "failure_modes": failure_modes or [],
    }


def metrics(
    *,
    category_accuracy: float,
    exact_rate: float,
    false_unknown_rate: float,
    missed_unknown_rate: float,
    unsafe_rate: float,
    tag_false_positives: int,
    failure_mode_counts: dict[str, int],
) -> dict[str, object]:
    """Return a metrics artifact with fields used by comparison reports."""
    return {
        "run": {"example_count": 3, "raw_output_count": 3},
        "headline": {
            "composite_score": 0.5,
            "valid_json_rate": 1.0,
            "schema_valid_rate": 1.0,
            "valid_category_id_rate": 1.0,
            "valid_tag_id_rate": 1.0,
            "valid_taxonomy_id_rate": 1.0,
            "invalid_output_rate": 0.0,
            "category_accuracy": category_accuracy,
            "known_category_accuracy": category_accuracy,
            "exact_taxonomy_match_rate": exact_rate,
            "tag_micro_precision": 0.75,
            "tag_micro_recall": 0.75,
            "tag_micro_f1": 0.75,
            "tag_macro_precision": 0.75,
            "tag_macro_recall": 0.75,
            "tag_macro_f1": 0.75,
            "unknown_precision": 0.5,
            "unknown_recall": 0.5,
            "false_unknown_rate": false_unknown_rate,
            "missed_unknown_rate": missed_unknown_rate,
            "needs_review_precision": 0.5,
            "needs_review_recall": 0.5,
            "needs_review_f1": 0.5,
            "unsafe_auto_assignment_rate": unsafe_rate,
            "high_confidence_wrong_rate": unsafe_rate,
            "confidence_calibration_score": 0.8,
        },
        "counts": {"tag_false_positives": tag_false_positives},
        "confidence_calibration": {
            "method": "proxy_high_confidence_correctness",
            "note": "Small dataset proxy.",
            "bands": [{"band": "0.95-1.00", "count": 1}],
        },
        "failure_mode_counts": failure_mode_counts,
    }


def config(run_id: str, *, dataset_hash: str = "same-dataset") -> dict[str, object]:
    """Return a run config artifact."""
    return {
        "run_id": run_id,
        "model": "gpt-test",
        "prompt_path": f"evals/llm_categorization/prompts/{run_id}.md",
        "prompt_hash": f"{run_id}-hash",
        "dataset_hash": dataset_hash,
        "temperature": 0.0,
        "number_of_examples": 3,
        "timestamp": "2026-07-08T12:00:00Z",
        "response_format": "json_object",
    }


def create_run(run_dir: Path, *, run_id: str, dataset_hash: str = "same-dataset") -> None:
    """Create one synthetic run directory."""
    run_dir.mkdir()
    if run_id == "baseline":
        scored = [
            scored_record(
                "req-1",
                expected_block=expected("cat_food", "Food", ["tag_reimbursable"]),
                predicted_block=predicted("cat_food", "Food", ["tag_reimbursable"], confidence=0.97),
                exact=True,
            ),
            scored_record(
                "req-2",
                expected_block=expected("cat_unknown", "UNKNOWN", needs_review=True),
                predicted_block=predicted("cat_food", "Food", confidence=0.96),
                exact=False,
                unsafe=True,
                failure_modes=["missed_unknown", "unsafe_auto_assignment", "high_confidence_wrong", "under_review"],
            ),
            scored_record(
                "req-3",
                expected_block=expected("cat_food", "Food", ["tag_tax"]),
                predicted_block=predicted("cat_food", "Food", ["tag_tax", "tag_reimbursable"], confidence=0.7),
                exact=False,
                unsafe=True,
                failure_modes=["extra_tag", "reimbursement_confusion"],
            ),
        ]
        run_metrics = metrics(
            category_accuracy=0.667,
            exact_rate=0.333,
            false_unknown_rate=0.0,
            missed_unknown_rate=1.0,
            unsafe_rate=0.667,
            tag_false_positives=1,
            failure_mode_counts={
                "extra_tag": 1,
                "high_confidence_wrong": 1,
                "missed_unknown": 1,
                "reimbursement_confusion": 1,
                "unsafe_auto_assignment": 2,
            },
        )
    else:
        scored = [
            scored_record(
                "req-1",
                expected_block=expected("cat_food", "Food", ["tag_reimbursable"]),
                predicted_block=predicted("cat_unknown", "UNKNOWN", needs_review=True, confidence=0.65),
                exact=False,
                failure_modes=["false_unknown", "over_review"],
            ),
            scored_record(
                "req-2",
                expected_block=expected("cat_unknown", "UNKNOWN", needs_review=True),
                predicted_block=predicted("cat_unknown", "UNKNOWN", needs_review=True, confidence=0.8),
                exact=True,
            ),
            scored_record(
                "req-3",
                expected_block=expected("cat_food", "Food", ["tag_tax"]),
                predicted_block=predicted("cat_food", "Food", ["tag_tax"], confidence=0.8),
                exact=True,
            ),
        ]
        run_metrics = metrics(
            category_accuracy=0.667,
            exact_rate=0.667,
            false_unknown_rate=0.5,
            missed_unknown_rate=0.0,
            unsafe_rate=0.0,
            tag_false_positives=0,
            failure_mode_counts={"false_unknown": 1, "over_review": 1},
        )

    write_json(run_dir / "config.json", config(run_id, dataset_hash=dataset_hash))
    write_json(run_dir / "metrics.json", run_metrics)
    write_jsonl(run_dir / "scored_outputs.jsonl", scored)
    write_jsonl(run_dir / "failures.jsonl", [record for record in scored if record["failure_modes"]])
    write_jsonl(
        run_dir / "raw_outputs.jsonl",
        [
            {
                "request_id": "req-1",
                "raw_output": "{}",
                "model": "gpt-test",
                "prompt_id": run_id,
                "token_usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
                "duration_ms": 100,
            }
        ],
    )


def test_compare_runs_writes_tradeoff_report(tmp_path):
    """Verify comparison report includes required metrics, failures, and examples."""
    baseline = tmp_path / "baseline"
    conservative = tmp_path / "conservative"
    out_path = tmp_path / "comparison.md"
    create_run(baseline, run_id="baseline")
    create_run(conservative, run_id="conservative")

    exit_code = compare_runs.main(["--runs", str(baseline), str(conservative), "--out", str(out_path)])

    report = out_path.read_text(encoding="utf-8")

    assert exit_code == 0
    assert "# Prompt Run Comparison" in report
    assert "## Run configurations" in report
    assert "## Headline metrics" in report
    assert "## Validity metrics" in report
    assert "## Category Metrics" in report
    assert "## Tag Metrics" in report
    assert "## UNKNOWN Behavior" in report
    assert "## needs_review Behavior" in report
    assert "## Confidence Calibration Summary" in report
    assert "## Token Usage" in report
    assert "prompt_tokens" in report
    assert "## Failure-Mode Comparison" in report
    assert "unsafe_auto_assignment" in report
    assert "## Disagreement Examples" in report
    assert "`req-1`" in report
    assert "## Uniquely Correct vs Unsafe" in report
    assert "Uniquely correct: conservative; unsafe: baseline" in report
    assert "## Overused UNKNOWN Examples" in report
    assert "false_unknown: conservative" in report
    assert "## Overused Tag Examples" in report
    assert "extra_tag: baseline" in report
    assert "## Interpretation Notes" in report
    assert "Do not claim a winner based only on the composite score" in report
    assert "Prompt with lowest unsafe auto-assignment rate: conservative" in report
    assert "Prompt that overuses `UNKNOWN`: conservative" in report
    assert "Prompt that overuses tags: baseline" in report
    assert "| Invented taxonomy IDs | Output-format prompt issue |" in report


def test_compare_runs_warns_on_dataset_hash_mismatch(tmp_path):
    """Verify mismatched dataset hashes are reported clearly."""
    first = tmp_path / "first"
    second = tmp_path / "second"
    out_path = tmp_path / "comparison.md"
    create_run(first, run_id="baseline", dataset_hash="hash-a")
    create_run(second, run_id="conservative", dataset_hash="hash-b")

    exit_code = compare_runs.main(["--runs", str(first), str(second), "--out", str(out_path)])

    report = out_path.read_text(encoding="utf-8")

    assert exit_code == 0
    assert "Runs do not all use the same dataset hash" in report
