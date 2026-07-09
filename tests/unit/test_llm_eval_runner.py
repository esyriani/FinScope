"""Unit tests for LLM categorization eval runner."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from evals.llm_categorization.tools import run_eval


class SequencedOpenAIClientFactory:
    """OpenAI-compatible fake that returns one configured response per call."""

    def __init__(self, raw_outputs: list[dict[str, object]]) -> None:
        """Store fake model outputs and initialize call capture."""
        self.raw_outputs = raw_outputs
        self.constructor_calls: list[dict[str, object]] = []
        self.created_calls: list[dict[str, object]] = []

    def __call__(self, api_key: str, timeout: int) -> object:
        """Return a fake client and capture construction arguments."""
        self.constructor_calls.append({"api_key": api_key, "timeout": timeout})
        return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=self.create)))

    def create(self, **kwargs: object) -> object:
        """Return the next fake response."""
        self.created_calls.append(kwargs)
        index = len(self.created_calls) - 1
        content = json.dumps(self.raw_outputs[index], sort_keys=True)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
            usage=SimpleNamespace(prompt_tokens=10 + index, completion_tokens=5, total_tokens=15 + index),
        )


def taxonomy() -> dict[str, object]:
    """Return a candidate taxonomy for runner tests."""
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


def example(request_id: str, *, amount: float = 23.45, needs_review: bool = False) -> dict[str, object]:
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
        "expected": {"category_id": "cat_food", "tag_ids": ["tag_reimbursable"], "needs_review": needs_review},
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
    """Write JSONL records for tests."""
    path.write_text("\n".join(json.dumps(record, sort_keys=True) for record in records), encoding="utf-8")


def read_jsonl(path: Path) -> list[dict[str, object]]:
    """Read JSONL records for assertions."""
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_prompt(path: Path) -> None:
    """Write a prompt candidate for runner tests."""
    path.write_text("# Prompt\n\nReturn JSON only.", encoding="utf-8")


def eval_config(
    *,
    prompt_path: Path,
    dataset_path: Path,
    out_dir: Path,
    dry_run: bool = False,
    score: bool = False,
    resume: bool = False,
    limit: int | None = None,
    request_id: str | None = None,
) -> run_eval.EvalConfig:
    """Build a runner config for tests."""
    return run_eval.EvalConfig(
        prompt_path=prompt_path,
        dataset_path=dataset_path,
        model="gpt-test",
        temperature=0.0,
        out_dir=out_dir,
        max_output_tokens=200,
        response_format="json_object",
        limit=limit,
        request_id=request_id,
        resume=resume,
        dry_run=dry_run,
        score=score,
        retry_policy=run_eval.RetryPolicy(max_retries=0, retry_delay_seconds=0),
        config_path=out_dir / "missing-config.ini",
        timeout_seconds=7,
    )


def test_run_eval_dry_run_writes_rendered_prompts_without_api_key(tmp_path):
    """Verify dry-run renders prompts and writes reproducibility artifacts without API calls."""
    prompt_path = tmp_path / "prompt.md"
    dataset_path = tmp_path / "dataset.jsonl"
    out_dir = tmp_path / "dry-run"
    write_prompt(prompt_path)
    write_jsonl(dataset_path, [example("req-1"), example("req-2")])

    config = eval_config(
        prompt_path=prompt_path,
        dataset_path=dataset_path,
        out_dir=out_dir,
        dry_run=True,
        score=True,
        limit=1,
    )
    payload = run_eval.run_evaluation(config)

    rendered_prompts = read_jsonl(out_dir / "rendered_prompts.jsonl")
    raw_outputs = (out_dir / "raw_outputs.jsonl").read_text(encoding="utf-8")
    dataset_meta = json.loads((out_dir / "dataset.meta.json").read_text(encoding="utf-8"))

    assert payload["dry_run"] is True
    assert payload["number_of_examples"] == 1
    assert payload["scorer_version"] is None
    assert rendered_prompts[0]["request_id"] == "req-1"
    assert raw_outputs == ""
    assert dataset_meta["selected_request_ids"] == ["req-1"]
    assert (out_dir / "prompt.md").read_text(encoding="utf-8") == "# Prompt\n\nReturn JSON only."


def test_run_eval_calls_fake_openai_writes_raw_outputs_and_scores(tmp_path):
    """Verify provider calls, raw-output metadata, and automatic scoring."""
    prompt_path = tmp_path / "prompt.md"
    dataset_path = tmp_path / "dataset.jsonl"
    out_dir = tmp_path / "run"
    write_prompt(prompt_path)
    write_jsonl(dataset_path, [example("req-1"), example("req-2")])
    fake_client = SequencedOpenAIClientFactory([model_output("req-1"), model_output("req-2")])

    config = eval_config(prompt_path=prompt_path, dataset_path=dataset_path, out_dir=out_dir, score=True)
    payload = run_eval.run_evaluation(config, client_factory=fake_client, api_key="sk-test")

    raw_outputs = read_jsonl(out_dir / "raw_outputs.jsonl")
    metrics = json.loads((out_dir / "metrics.json").read_text(encoding="utf-8"))
    combined_messages = "\n".join(
        message["content"] for call in fake_client.created_calls for message in call["messages"]  # type: ignore[index]
    )

    assert payload["scorer_version"] == "001"
    assert fake_client.constructor_calls == [{"api_key": "sk-test", "timeout": 7}]
    assert len(fake_client.created_calls) == 2
    assert fake_client.created_calls[0]["model"] == "gpt-test"
    assert fake_client.created_calls[0]["temperature"] == 0.0
    assert fake_client.created_calls[0]["response_format"] == {"type": "json_object"}
    assert fake_client.created_calls[0]["max_completion_tokens"] == 200
    assert '"expected":' not in combined_messages
    assert '"label_source":' not in combined_messages
    assert raw_outputs[0]["prompt_hash"] == payload["prompt_hash"]
    assert raw_outputs[0]["dataset_hash"] == payload["dataset_hash"]
    assert raw_outputs[0]["token_usage"] == {"completion_tokens": 5, "prompt_tokens": 10, "total_tokens": 15}
    assert raw_outputs[0]["attempt_count"] == 1
    assert metrics["headline"]["category_accuracy"] == 1.0
    assert metrics["headline"]["exact_taxonomy_match_rate"] == 1.0
    assert (out_dir / "config.json").exists()
    assert (out_dir / "report.md").exists()


def test_run_eval_resume_skips_completed_request_ids(tmp_path):
    """Verify resume appends only missing request IDs to raw_outputs.jsonl."""
    prompt_path = tmp_path / "prompt.md"
    dataset_path = tmp_path / "dataset.jsonl"
    out_dir = tmp_path / "run"
    write_prompt(prompt_path)
    write_jsonl(dataset_path, [example("req-1"), example("req-2")])
    out_dir.mkdir()
    write_jsonl(
        out_dir / "raw_outputs.jsonl",
        [
            {
                "request_id": "req-1",
                "raw_output": json.dumps(model_output("req-1"), sort_keys=True),
                "model": "gpt-test",
                "prompt_id": "prompt",
            }
        ],
    )
    fake_client = SequencedOpenAIClientFactory([model_output("req-2")])

    config = eval_config(
        prompt_path=prompt_path,
        dataset_path=dataset_path,
        out_dir=out_dir,
        resume=True,
        score=False,
    )
    payload = run_eval.run_evaluation(config, client_factory=fake_client, api_key="sk-test")

    raw_outputs = read_jsonl(out_dir / "raw_outputs.jsonl")

    assert payload["completed_before_resume"] == 1
    assert payload["examples_run"] == 1
    assert len(fake_client.created_calls) == 1
    assert [row["request_id"] for row in raw_outputs] == ["req-1", "req-2"]
