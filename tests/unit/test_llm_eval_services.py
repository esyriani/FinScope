"""Unit tests for LLM categorization eval service facades."""

from __future__ import annotations

import json
from pathlib import Path

from evals.llm_categorization.services import (
    PROMPT_LAB_NOTICE,
    comparison_service,
    dataset_service,
    prompt_service,
    run_service,
    scoring_service,
)
from evals.llm_categorization.tools import run_eval


def taxonomy() -> dict[str, object]:
    """Return a candidate taxonomy for eval service tests."""
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


def example(request_id: str, *, amount: float = 23.45) -> dict[str, object]:
    """Return one valid synthetic eval example."""
    return {
        "request_id": request_id,
        "transaction": {
            "description": "CORNER CAFE",
            "merchant": "Corner cafe",
            "amount": amount,
            "date": "2026-05-03",
            "account": "Credit card",
            "statement_type": "credit_card",
        },
        "candidate_taxonomy": taxonomy(),
        "similar_transactions": [],
        "expected": {"category_id": "cat_food", "tag_ids": ["tag_reimbursable"], "needs_review": False},
        "label_source": "reviewed",
        "privacy_level": "synthetic",
        "coverage": {
            "category": "Food",
            "tags": ["Reimbursable"],
            "direction": "debit" if amount > 0 else "credit",
            "statement_type": "credit_card",
            "confidence_band": "high",
            "ambiguity_type": "straightforward",
        },
        "notes": "Curator-only note.",
    }


def model_output(request_id: str) -> dict[str, object]:
    """Return a valid model output for one request."""
    return {
        "request_id": request_id,
        "category_id": "cat_food",
        "tag_ids": ["tag_reimbursable"],
        "confidence": 0.97,
        "needs_review": False,
        "supported_by_similar_transactions": False,
        "reason": "Clear food merchant.",
    }


def write_jsonl(path: Path, records: list[dict[str, object]]) -> None:
    """Write JSONL records for service tests."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(record, sort_keys=True) for record in records), encoding="utf-8")


def write_prompt(path: Path) -> None:
    """Write a prompt candidate for service tests."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# Prompt\n\nReturn JSON only.", encoding="utf-8")


def write_raw_outputs(path: Path, request_ids: list[str]) -> None:
    """Write raw model output wrappers for scoring service tests."""
    write_jsonl(
        path,
        [
            {
                "request_id": request_id,
                "raw_output": json.dumps(model_output(request_id), sort_keys=True),
                "model": "gpt-test",
                "prompt_id": "prompt",
            }
            for request_id in request_ids
        ],
    )


def test_dataset_service_lists_validates_summarizes_and_reads_examples(tmp_path):
    """Verify dataset services expose strict validation and record reads."""
    datasets_dir = tmp_path / "datasets"
    dataset_path = datasets_dir / "valid.jsonl"
    invalid_path = datasets_dir / "invalid.jsonl"
    write_jsonl(dataset_path, [example("req-1"), example("req-2", amount=-10.0)])
    write_jsonl(invalid_path, [example("req-1"), example("req-1")])

    artifacts = dataset_service.list_datasets(datasets_dir)
    valid_result = dataset_service.validate_dataset_file(dataset_path)
    invalid_result = dataset_service.validate_dataset_file(invalid_path)
    curation_summary = dataset_service.read_dataset_summary(dataset_path)
    selected_examples = dataset_service.read_dataset_examples(dataset_path, request_id="req-2")

    assert [artifact.name for artifact in artifacts] == ["invalid.jsonl", "valid.jsonl"]
    assert valid_result.valid is True
    assert valid_result.summary is not None
    assert valid_result.summary.example_count == 2
    assert invalid_result.valid is False
    assert invalid_result.error is not None
    assert invalid_result.error_lines
    assert "duplicate request_id" in invalid_result.error
    assert curation_summary.example_count == 2
    assert selected_examples[0]["request_id"] == "req-2"


def test_dataset_service_resolves_dataset_names_inside_datasets_dir(tmp_path):
    """Verify dataset name resolution rejects traversal and non-JSONL files."""
    datasets_dir = tmp_path / "datasets"
    dataset_path = datasets_dir / "valid.jsonl"
    write_jsonl(dataset_path, [example("req-1")])

    resolved_path = dataset_service.resolve_dataset_path("valid.jsonl", datasets_dir)

    assert resolved_path == dataset_path.resolve()
    for unsafe_name in ("..\\secret.jsonl", "../secret.jsonl", "notes.txt"):
        try:
            dataset_service.resolve_dataset_path(unsafe_name, datasets_dir)
        except ValueError:
            pass
        else:
            raise AssertionError(f"unsafe dataset name should fail: {unsafe_name}")


def test_prompt_service_lists_writes_reads_and_renders_without_expected_labels(tmp_path):
    """Verify prompt services manage Markdown files and render safe prompt previews."""
    prompts_dir = tmp_path / "prompts"
    dataset_path = tmp_path / "dataset.jsonl"
    write_jsonl(dataset_path, [example("req-1")])

    prompt_path = prompt_service.write_prompt_file("002_test", "# Prompt\n\nReturn JSON only.", prompts_dir=prompts_dir)
    rendered = prompt_service.render_prompt_preview(
        prompt_path=prompt_path, dataset_path=dataset_path, request_id="req-1"
    )
    combined_content = "\n".join(
        message["content"]
        for request in rendered["rendered_requests"]
        for message in request["message_payload"]["messages"]
    )

    assert PROMPT_LAB_NOTICE.startswith("Prompt Lab is a local developer tool.")
    assert prompt_path.name == "002_test.md"
    assert prompt_service.read_prompt_file(prompt_path) == "# Prompt\n\nReturn JSON only."
    assert [artifact.name for artifact in prompt_service.list_prompt_files(prompts_dir)] == ["002_test.md"]
    assert rendered["rendered_count"] == 1
    assert '"expected"' not in combined_content
    assert '"label_source"' not in combined_content


def test_prompt_service_resolves_prompt_names_and_protects_overwrites(tmp_path):
    """Verify prompt name resolution and save-as overwrite protection."""
    prompts_dir = tmp_path / "prompts"
    prompt_path = prompts_dir / "001_base.md"
    write_prompt(prompt_path)

    resolved_path = prompt_service.resolve_prompt_path("001_base.md", prompts_dir)

    assert resolved_path == prompt_path.resolve()
    for unsafe_name in ("..\\secret.md", "../secret.md", "not-markdown.txt"):
        try:
            prompt_service.resolve_prompt_path(unsafe_name, prompts_dir)
        except ValueError:
            pass
        else:
            raise AssertionError(f"unsafe prompt name should fail: {unsafe_name}")

    try:
        prompt_service.save_prompt_as("001_base.md", "replacement", prompts_dir=prompts_dir)
    except FileExistsError:
        pass
    else:
        raise AssertionError("save_prompt_as should require overwrite confirmation")

    prompt_service.save_prompt_as("001_base.md", "replacement", prompts_dir=prompts_dir, overwrite=True)
    assert prompt_path.read_text(encoding="utf-8") == "replacement"


def test_run_service_lists_runs_and_launches_dry_run(tmp_path):
    """Verify run services list artifacts and launch dry-runs without provider calls."""
    prompt_path = tmp_path / "prompt.md"
    dataset_path = tmp_path / "dataset.jsonl"
    out_dir = tmp_path / "runs" / "dry_run"
    write_prompt(prompt_path)
    write_jsonl(dataset_path, [example("req-1")])
    config = run_eval.EvalConfig(
        prompt_path=prompt_path,
        dataset_path=dataset_path,
        model="gpt-test",
        temperature=0.0,
        out_dir=out_dir,
        max_output_tokens=200,
        response_format="json_object",
        limit=None,
        request_id=None,
        resume=False,
        dry_run=True,
        score=False,
        retry_policy=run_eval.RetryPolicy(max_retries=0, retry_delay_seconds=0),
        config_path=tmp_path / "missing-config.ini",
        timeout_seconds=7,
    )

    payload = run_service.launch_evaluation_run(config)
    runs = run_service.list_runs(tmp_path / "runs")

    assert payload["dry_run"] is True
    assert (out_dir / "rendered_prompts.jsonl").exists()
    assert len(runs) == 1
    assert runs[0].run_id == "dry_run"
    assert runs[0].has_config is True
    assert runs[0].has_raw_outputs is True


def test_run_service_resolves_run_names_inside_runs_dir(tmp_path):
    """Verify run name resolution rejects traversal."""
    runs_dir = tmp_path / "runs"
    run_dir = runs_dir / "run-one"
    run_dir.mkdir(parents=True)

    resolved_path = run_service.resolve_run_path("run-one", runs_dir)

    assert resolved_path == run_dir.resolve()
    for unsafe_name in ("..\\secret", "../secret", ""):
        try:
            run_service.resolve_run_path(unsafe_name, runs_dir, must_exist=False)
        except ValueError:
            pass
        else:
            raise AssertionError(f"unsafe run name should fail: {unsafe_name}")


def test_scoring_and_comparison_services_write_and_read_artifacts(tmp_path):
    """Verify scoring and comparison services operate on saved run directories."""
    dataset_path = tmp_path / "dataset.jsonl"
    write_jsonl(dataset_path, [example("req-1"), example("req-2")])
    run_a = tmp_path / "runs" / "a"
    run_b = tmp_path / "runs" / "b"
    write_raw_outputs(run_a / "raw_outputs.jsonl", ["req-1", "req-2"])
    write_raw_outputs(run_b / "raw_outputs.jsonl", ["req-1", "req-2"])

    scoring_service.score_outputs_file(dataset_path, run_a / "raw_outputs.jsonl", run_a)
    scoring_service.score_outputs_file(dataset_path, run_b / "raw_outputs.jsonl", run_b)
    for run_dir, run_id in ((run_a, "a"), (run_b, "b")):
        (run_dir / "config.json").write_text(
            json.dumps(
                {
                    "run_id": run_id,
                    "model": "gpt-test",
                    "prompt_path": str(tmp_path / "prompt.md"),
                    "prompt_hash": "abc123",
                    "dataset_path": str(dataset_path),
                    "dataset_hash": "dataset123",
                    "temperature": 0.0,
                    "number_of_examples": 2,
                    "timestamp": "2026-07-09T00:00:00Z",
                    "response_format": "json_object",
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )

    metrics = scoring_service.read_run_metrics(run_a)
    failures = scoring_service.read_run_failures(run_a)
    report_path = tmp_path / "comparison.md"
    report = comparison_service.compare_selected_runs([run_a, run_b], out_path=report_path)

    assert metrics["headline"]["category_accuracy"] == 1.0
    assert failures == ()
    assert "# Prompt Run Comparison" in report
    assert report_path.exists()
