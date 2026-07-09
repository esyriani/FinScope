"""Run services for local LLM categorization eval artifacts.

The functions list saved runs and launch eval configurations through the shared
runner without requiring Flask or subprocess orchestration.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from evals.llm_categorization.services import RUNS_DIR, file_modified_at, resolve_under_root
from evals.llm_categorization.tools import run_eval


@dataclass(frozen=True)
class RunArtifact:
    """Represent one saved eval run directory."""

    path: Path
    run_id: str
    has_config: bool
    has_raw_outputs: bool
    has_metrics: bool
    model: str | None
    prompt_id: str | None
    example_count: int | None
    modified_at: float


def list_runs(runs_dir: Path | None = None) -> tuple[RunArtifact, ...]:
    """List saved run directories under the eval runs directory."""
    runs_dir = runs_dir or RUNS_DIR
    if not runs_dir.exists():
        return ()
    artifacts = []
    for path in sorted((item for item in runs_dir.iterdir() if item.is_dir()), key=lambda item: item.name.lower()):
        config = read_optional_json_object(path / "config.json")
        metrics = read_optional_json_object(path / "metrics.json")
        headline_run = metrics.get("run", {}) if isinstance(metrics.get("run"), dict) else {}
        artifacts.append(
            RunArtifact(
                path=path,
                run_id=str(config.get("run_id") or path.name),
                has_config=(path / "config.json").exists(),
                has_raw_outputs=(path / "raw_outputs.jsonl").exists(),
                has_metrics=(path / "metrics.json").exists(),
                model=str(config.get("model")) if config.get("model") is not None else None,
                prompt_id=prompt_id_for_run(path, config),
                example_count=example_count_for_run(config, headline_run),
                modified_at=file_modified_at(path),
            )
        )
    return tuple(artifacts)


def resolve_run_path(run_name: str, runs_dir: Path | None = None, *, must_exist: bool = True) -> Path:
    """Resolve one top-level run directory name safely under the runs directory."""
    runs_dir = runs_dir or RUNS_DIR
    if not str(run_name or "").strip():
        raise ValueError("run name is required")
    if Path(run_name).name != run_name:
        raise ValueError("run name must not contain path separators")
    run_path = resolve_under_root(run_name, runs_dir)
    if must_exist and not run_path.is_dir():
        raise FileNotFoundError(run_name)
    return run_path


def read_run_config(run_dir: Path) -> dict[str, Any]:
    """Read a run's config JSON artifact."""
    return read_json_object(run_dir / "config.json")


def launch_evaluation_run(
    config: run_eval.EvalConfig,
    *,
    client_factory: Callable[..., Any] | None = None,
    api_key: str | None = None,
) -> dict[str, Any]:
    """Launch one eval run through the shared runner service."""
    return run_eval.run_evaluation(config, client_factory=client_factory, api_key=api_key)


def prompt_id_for_run(path: Path, config: dict[str, Any]) -> str | None:
    """Return a run prompt ID from config or raw-output metadata when available."""
    prompt_path = config.get("prompt_path")
    if isinstance(prompt_path, str) and prompt_path:
        return Path(prompt_path).stem
    copied_prompt = path / "prompt.md"
    if copied_prompt.exists():
        return copied_prompt.stem
    return None


def example_count_for_run(config: dict[str, Any], metric_run: dict[str, Any]) -> int | None:
    """Return the number of examples recorded for a run."""
    for value in (config.get("number_of_examples"), metric_run.get("example_count")):
        if isinstance(value, int):
            return value
    return None


def read_optional_json_object(path: Path) -> dict[str, Any]:
    """Read an optional JSON object artifact, returning an empty mapping when absent."""
    if not path.exists():
        return {}
    try:
        return read_json_object(path)
    except (OSError, json.JSONDecodeError, ValueError):
        return {}


def read_json_object(path: Path) -> dict[str, Any]:
    """Read a required JSON object artifact."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload
