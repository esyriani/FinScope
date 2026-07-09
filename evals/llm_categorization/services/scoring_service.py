"""Scoring services for saved LLM categorization eval outputs.

The functions expose saved-output scoring and run-artifact readers without
subprocesses, provider calls, or database access.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from evals.llm_categorization.tools import score_outputs
from evals.llm_categorization.tools.io_utils import read_jsonl


def score_outputs_file(dataset_path: Path, outputs_path: Path, out_dir: Path) -> score_outputs.ScoreRun:
    """Score raw outputs against a dataset and write scoring artifacts."""
    score_run = score_outputs.score_run(dataset_path, outputs_path)
    score_outputs.write_score_artifacts(score_run, out_dir)
    return score_run


def read_run_metrics(run_dir: Path) -> dict[str, Any]:
    """Read a run's metrics JSON artifact."""
    return read_json_object(run_dir / "metrics.json")


def read_run_failures(run_dir: Path, *, limit: int | None = None) -> tuple[dict[str, Any], ...]:
    """Read a run's failure records, optionally bounded for preview."""
    if limit is not None and limit <= 0:
        raise ValueError("limit must be positive")
    failures_path = run_dir / "failures.jsonl"
    if not failures_path.exists():
        return ()
    failures = [record for _, record in read_jsonl(failures_path)]
    if limit is not None:
        failures = failures[:limit]
    return tuple(failures)


def rescore_run(
    run_dir: Path,
    *,
    dataset_path: Path | None = None,
    outputs_path: Path | None = None,
) -> score_outputs.ScoreRun:
    """Rescore a saved run using its config metadata or selected dataset file."""
    resolved_dataset_path = dataset_path or dataset_path_for_run(run_dir)
    resolved_outputs_path = outputs_path or run_dir / "raw_outputs.jsonl"
    return score_outputs_file(resolved_dataset_path, resolved_outputs_path, run_dir)


def dataset_path_for_run(run_dir: Path) -> Path:
    """Return the dataset path recorded for a run, preferring selected subsets."""
    selected_dataset_path = run_dir / "dataset.selected.jsonl"
    if selected_dataset_path.exists():
        return selected_dataset_path
    config_path = run_dir / "config.json"
    if not config_path.exists():
        raise ValueError(f"run config not found: {config_path}")
    config = read_json_object(config_path)
    dataset_path = config.get("dataset_path")
    if not isinstance(dataset_path, str) or not dataset_path:
        raise ValueError(f"run config does not contain dataset_path: {config_path}")
    return Path(dataset_path)


def read_json_object(path: Path) -> dict[str, Any]:
    """Read a JSON object artifact."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload
