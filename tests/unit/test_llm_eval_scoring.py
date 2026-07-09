"""Unit tests for saved LLM categorization output scoring."""

from __future__ import annotations

import json
from pathlib import Path

from evals.llm_categorization.tools import score_outputs


def taxonomy() -> dict[str, object]:
    """Return a candidate taxonomy for scorer tests."""
    return {
        "categories": [
            {"id": "cat_unknown", "name": "UNKNOWN", "description": "Unresolved.", "instruction": None},
            {"id": "cat_food", "name": "Food", "description": "Food purchases.", "instruction": None},
            {"id": "cat_income", "name": "Income", "description": "Income and credits.", "instruction": None},
        ],
        "tags": [
            {"id": "tag_tax", "name": "Tax", "description": "Tax-relevant.", "instruction": None},
            {
                "id": "tag_reimbursable",
                "name": "Reimbursable",
                "description": "Expense to reimburse.",
                "instruction": None,
            },
        ],
    }


def example(
    request_id: str,
    *,
    amount: float,
    category_id: str,
    category: str,
    tag_ids: list[str] | None = None,
    tags: list[str] | None = None,
    needs_review: bool = False,
    similar_category_id: str | None = None,
    similar_tag_ids: list[str] | None = None,
) -> dict[str, object]:
    """Return one valid synthetic scoring dataset example."""
    direction = "debit" if amount > 0 else "credit" if amount < 0 else "zero"
    similar_transactions = []
    if similar_category_id is not None:
        similar_transactions.append(
            {
                "description": "SIMILAR TRANSACTION",
                "amount": amount,
                "category_id": similar_category_id,
                "tag_ids": similar_tag_ids or [],
                "evidence_type": "history",
                "confidence": 0.91,
            }
        )
    return {
        "request_id": request_id,
        "transaction": {
            "description": "TEST TRANSACTION",
            "merchant": "Test merchant",
            "amount": amount,
            "date": "2026-05-03",
            "account": "Checking",
            "statement_type": "bank_account",
        },
        "candidate_taxonomy": taxonomy(),
        "similar_transactions": similar_transactions,
        "expected": {"category_id": category_id, "tag_ids": tag_ids or [], "needs_review": needs_review},
        "label_source": "reviewed",
        "privacy_level": "synthetic",
        "coverage": {
            "category": category,
            "tags": tags or [],
            "direction": direction,
            "statement_type": "bank_account",
            "confidence_band": "high" if not needs_review else "low",
            "ambiguity_type": "straightforward" if category != "UNKNOWN" else "unknown_correct",
        },
        "notes": "",
    }


def raw_output(
    request_id: str,
    *,
    category_id: str,
    tag_ids: list[str] | None = None,
    confidence: float = 0.8,
    needs_review: bool = False,
    supported_by_similar_transactions: bool = False,
) -> dict[str, object]:
    """Return one saved raw model output row."""
    return {
        "request_id": request_id,
        "raw_output": json.dumps(
            {
                "request_id": request_id,
                "category_id": category_id,
                "tag_ids": tag_ids or [],
                "confidence": confidence,
                "needs_review": needs_review,
                "supported_by_similar_transactions": supported_by_similar_transactions,
                "reason": "Synthetic reason.",
            },
            sort_keys=True,
        ),
        "model": "test-model",
        "prompt_id": "001_baseline",
    }


def write_jsonl(path: Path, records: list[dict[str, object]]) -> None:
    """Write JSONL records for scorer tests."""
    path.write_text("\n".join(json.dumps(record, sort_keys=True) for record in records), encoding="utf-8")


def read_jsonl(path: Path) -> list[dict[str, object]]:
    """Read JSONL records for assertions."""
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def scoring_dataset() -> list[dict[str, object]]:
    """Return a small labeled dataset with known, UNKNOWN, tag, and invalid-output cases."""
    return [
        example(
            "req-food",
            amount=23.45,
            category_id="cat_food",
            category="Food",
            tag_ids=["tag_reimbursable"],
            tags=["Reimbursable"],
            similar_category_id="cat_food",
            similar_tag_ids=["tag_reimbursable"],
        ),
        example("req-unknown", amount=9.0, category_id="cat_unknown", category="UNKNOWN", needs_review=True),
        example(
            "req-tax",
            amount=44.0,
            category_id="cat_food",
            category="Food",
            tag_ids=["tag_tax"],
            tags=["Tax"],
        ),
        example("req-invalid-json", amount=-100.0, category_id="cat_income", category="Income"),
    ]


def test_score_outputs_writes_artifacts_and_required_metrics(tmp_path):
    """Verify scorer writes artifacts and computes headline methodology metrics."""
    dataset_path = tmp_path / "validation.jsonl"
    outputs_path = tmp_path / "raw_outputs.jsonl"
    out_dir = tmp_path / "run"
    write_jsonl(dataset_path, scoring_dataset())
    write_jsonl(
        outputs_path,
        [
            raw_output(
                "req-food",
                category_id="cat_food",
                tag_ids=["tag_reimbursable"],
                confidence=0.98,
                supported_by_similar_transactions=True,
            ),
            raw_output("req-unknown", category_id="cat_food", confidence=0.96, needs_review=False),
            raw_output("req-tax", category_id="cat_food", tag_ids=["tag_reimbursable"], confidence=0.74),
            {
                "request_id": "req-invalid-json",
                "raw_output": "{not json",
                "model": "test-model",
                "prompt_id": "001_baseline",
            },
        ],
    )

    exit_code = score_outputs.main(
        ["--dataset", str(dataset_path), "--outputs", str(outputs_path), "--out-dir", str(out_dir)]
    )

    metrics = json.loads((out_dir / "metrics.json").read_text(encoding="utf-8"))
    failures = read_jsonl(out_dir / "failures.jsonl")
    scored = read_jsonl(out_dir / "scored_outputs.jsonl")
    report = (out_dir / "report.md").read_text(encoding="utf-8")

    assert exit_code == 0
    assert len(scored) == 4
    assert metrics["headline"]["valid_json_rate"] == 0.75
    assert metrics["headline"]["schema_valid_rate"] == 0.75
    assert metrics["headline"]["valid_taxonomy_id_rate"] == 0.75
    assert metrics["headline"]["category_accuracy"] == 0.5
    assert metrics["headline"]["known_category_accuracy"] == 0.666667
    assert metrics["headline"]["exact_taxonomy_match_rate"] == 0.25
    assert metrics["headline"]["tag_micro_precision"] == 0.5
    assert metrics["headline"]["tag_micro_recall"] == 0.5
    assert metrics["headline"]["tag_micro_f1"] == 0.5
    assert metrics["headline"]["unknown_recall"] == 0.0
    assert metrics["headline"]["missed_unknown_rate"] == 1.0
    assert metrics["headline"]["unsafe_auto_assignment_rate"] == 0.5
    assert metrics["headline"]["high_confidence_wrong_rate"] == 0.25
    assert metrics["headline"]["under_review_count"] == 1
    assert metrics["failure_mode_counts"]["invalid_json"] == 1
    assert metrics["failure_mode_counts"]["missed_unknown"] == 1
    assert metrics["failure_mode_counts"]["unsafe_auto_assignment"] == 2
    assert metrics["failure_mode_counts"]["high_confidence_wrong"] == 1
    assert metrics["failure_mode_counts"]["missing_tag"] == 1
    assert metrics["failure_mode_counts"]["extra_tag"] == 1
    assert any("req-unknown" == failure["request_id"] for failure in failures)
    assert "## Headline metrics" in report
    assert "## Confidence calibration" in report
    assert "Small datasets or sparse confidence bands" in report


def test_score_outputs_invalid_taxonomy_ids_receive_zero_semantic_score(tmp_path):
    """Verify invalid category or tag IDs are validity failures with zero semantic score."""
    dataset_path = tmp_path / "validation.jsonl"
    outputs_path = tmp_path / "raw_outputs.jsonl"
    out_dir = tmp_path / "run"
    write_jsonl(
        dataset_path,
        [
            example(
                "req-food",
                amount=23.45,
                category_id="cat_food",
                category="Food",
                tag_ids=["tag_tax"],
                tags=["Tax"],
            )
        ],
    )
    write_jsonl(
        outputs_path,
        [raw_output("req-food", category_id="cat_missing", tag_ids=["tag_missing"], confidence=0.99)],
    )

    exit_code = score_outputs.main(
        ["--dataset", str(dataset_path), "--outputs", str(outputs_path), "--out-dir", str(out_dir)]
    )

    metrics = json.loads((out_dir / "metrics.json").read_text(encoding="utf-8"))
    scored = read_jsonl(out_dir / "scored_outputs.jsonl")[0]

    assert exit_code == 0
    assert metrics["headline"]["valid_json_rate"] == 1.0
    assert metrics["headline"]["schema_valid_rate"] == 1.0
    assert metrics["headline"]["valid_category_id_rate"] == 0.0
    assert metrics["headline"]["valid_tag_id_rate"] == 0.0
    assert metrics["headline"]["valid_taxonomy_id_rate"] == 0.0
    assert metrics["headline"]["category_accuracy"] == 0.0
    assert metrics["headline"]["tag_micro_f1"] == 0.0
    assert "invalid_category_id" in scored["failure_modes"]
    assert "invalid_tag_id" in scored["failure_modes"]
    assert "unsafe_auto_assignment" in scored["failure_modes"]
    assert "high_confidence_wrong" in scored["failure_modes"]


def test_score_outputs_rejects_invalid_schema_output(tmp_path):
    """Verify schema-invalid model objects are tracked separately from semantic failures."""
    dataset_path = tmp_path / "validation.jsonl"
    outputs_path = tmp_path / "raw_outputs.jsonl"
    out_dir = tmp_path / "run"
    write_jsonl(dataset_path, [example("req-food", amount=23.45, category_id="cat_food", category="Food")])
    write_jsonl(
        outputs_path,
        [
            {
                "request_id": "req-food",
                "raw_output": json.dumps(
                    {
                        "request_id": "req-food",
                        "category_id": "cat_food",
                        "tag_ids": [],
                        "confidence": 0.8,
                        "needs_review": False,
                        "reason": "Missing support flag.",
                    },
                    sort_keys=True,
                ),
                "model": "test-model",
                "prompt_id": "001_baseline",
            }
        ],
    )

    exit_code = score_outputs.main(
        ["--dataset", str(dataset_path), "--outputs", str(outputs_path), "--out-dir", str(out_dir)]
    )

    metrics = json.loads((out_dir / "metrics.json").read_text(encoding="utf-8"))
    scored = read_jsonl(out_dir / "scored_outputs.jsonl")[0]

    assert exit_code == 0
    assert metrics["headline"]["valid_json_rate"] == 1.0
    assert metrics["headline"]["schema_valid_rate"] == 0.0
    assert metrics["headline"]["category_accuracy"] == 0.0
    assert "invalid_schema" in scored["failure_modes"]
    assert "model output missing required field: supported_by_similar_transactions" in scored["errors"]


def test_score_outputs_tag_unknown_review_confidence_and_composite_metrics(tmp_path):
    """Verify scoring edge cases from the benchmark methodology."""
    dataset_path = tmp_path / "validation.jsonl"
    outputs_path = tmp_path / "raw_outputs.jsonl"
    out_dir = tmp_path / "run"
    write_jsonl(
        dataset_path,
        [
            example(
                "req-exact-tags",
                amount=10.0,
                category_id="cat_food",
                category="Food",
                tag_ids=["tag_tax", "tag_reimbursable"],
                tags=["Tax", "Reimbursable"],
            ),
            example(
                "req-partial-tags",
                amount=11.0,
                category_id="cat_food",
                category="Food",
                tag_ids=["tag_tax", "tag_reimbursable"],
                tags=["Tax", "Reimbursable"],
            ),
            example("req-empty-extra", amount=12.0, category_id="cat_food", category="Food"),
            example(
                "req-missing-all-tags",
                amount=13.0,
                category_id="cat_food",
                category="Food",
                tag_ids=["tag_reimbursable"],
                tags=["Reimbursable"],
            ),
            example(
                "req-unknown-tp",
                amount=14.0,
                category_id="cat_unknown",
                category="UNKNOWN",
                needs_review=True,
            ),
            example("req-false-unknown", amount=-20.0, category_id="cat_income", category="Income"),
            example(
                "req-missed-unknown",
                amount=15.0,
                category_id="cat_unknown",
                category="UNKNOWN",
                needs_review=True,
            ),
        ],
    )
    write_jsonl(
        outputs_path,
        [
            raw_output(
                "req-exact-tags",
                category_id="cat_food",
                tag_ids=["tag_tax", "tag_reimbursable"],
                confidence=0.4,
            ),
            raw_output("req-partial-tags", category_id="cat_food", tag_ids=["tag_tax"], confidence=0.6),
            raw_output("req-empty-extra", category_id="cat_food", tag_ids=["tag_tax"], confidence=0.8),
            raw_output("req-missing-all-tags", category_id="cat_food", tag_ids=[], confidence=0.9),
            raw_output(
                "req-unknown-tp",
                category_id="cat_unknown",
                confidence=0.96,
                needs_review=True,
            ),
            raw_output(
                "req-false-unknown",
                category_id="cat_unknown",
                confidence=0.5,
                needs_review=True,
            ),
            raw_output("req-missed-unknown", category_id="cat_food", confidence=0.97, needs_review=False),
        ],
    )

    exit_code = score_outputs.main(
        ["--dataset", str(dataset_path), "--outputs", str(outputs_path), "--out-dir", str(out_dir)]
    )

    metrics = json.loads((out_dir / "metrics.json").read_text(encoding="utf-8"))
    scored = {row["request_id"]: row for row in read_jsonl(out_dir / "scored_outputs.jsonl")}
    bands = {band["band"]: band for band in metrics["confidence_calibration"]["bands"]}

    assert exit_code == 0
    assert scored["req-exact-tags"]["scores"]["exact_taxonomy_match"] is True
    assert scored["req-partial-tags"]["scores"]["tag_true_positives"] == 1
    assert scored["req-partial-tags"]["scores"]["tag_false_negatives"] == 1
    assert scored["req-empty-extra"]["scores"]["tag_false_positives"] == 1
    assert scored["req-missing-all-tags"]["scores"]["tag_false_negatives"] == 1
    assert scored["req-unknown-tp"]["scores"]["unknown_true_positive"] is True
    assert scored["req-false-unknown"]["scores"]["false_unknown"] is True
    assert scored["req-missed-unknown"]["scores"]["missed_unknown"] is True
    assert metrics["headline"]["category_accuracy"] == 0.714286
    assert metrics["headline"]["known_category_accuracy"] == 0.8
    assert metrics["headline"]["exact_taxonomy_match_rate"] == 0.285714
    assert metrics["headline"]["tag_micro_precision"] == 0.75
    assert metrics["headline"]["tag_micro_recall"] == 0.6
    assert metrics["headline"]["tag_micro_f1"] == 0.666667
    assert metrics["headline"]["unknown_precision"] == 0.5
    assert metrics["headline"]["unknown_recall"] == 0.5
    assert metrics["headline"]["false_unknown_rate"] == 0.2
    assert metrics["headline"]["missed_unknown_rate"] == 0.5
    assert metrics["headline"]["needs_review_precision"] == 0.5
    assert metrics["headline"]["needs_review_recall"] == 0.5
    assert metrics["headline"]["needs_review_f1"] == 0.5
    assert metrics["headline"]["unsafe_auto_assignment_rate"] == 0.571429
    assert metrics["headline"]["high_confidence_wrong_rate"] == 0.142857
    assert metrics["headline"]["confidence_calibration_score"] == 0.5
    assert metrics["headline"]["composite_score"] == 0.452381
    assert bands["0.00-0.49"]["count"] == 1
    assert bands["0.50-0.69"]["count"] == 2
    assert bands["0.70-0.84"]["count"] == 1
    assert bands["0.85-0.94"]["count"] == 1
    assert bands["0.95-1.00"]["count"] == 2
    assert metrics["failure_mode_counts"]["missing_tag"] == 2
    assert metrics["failure_mode_counts"]["extra_tag"] == 1
    assert metrics["failure_mode_counts"]["false_unknown"] == 1
    assert metrics["failure_mode_counts"]["missed_unknown"] == 1
    assert metrics["failure_mode_counts"]["unsafe_auto_assignment"] == 4
    assert metrics["failure_mode_counts"]["high_confidence_wrong"] == 1
    assert metrics["failure_mode_counts"]["over_review"] == 1
    assert metrics["failure_mode_counts"]["under_review"] == 1
