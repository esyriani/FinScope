"""Shared services for local LLM categorization eval artifacts.

The service package keeps file-based eval operations importable from CLI tools
and the developer Prompt Lab UI without importing the FinScope Flask runtime.
"""

from pathlib import Path

EVAL_MODULE_DIR = Path(__file__).resolve().parents[1]
PROMPTS_DIR = EVAL_MODULE_DIR / "prompts"
DATASETS_DIR = EVAL_MODULE_DIR / "datasets"
RUNS_DIR = EVAL_MODULE_DIR / "runs"

PROMPT_LAB_NOTICE = (
    "Prompt Lab is a local developer tool. It reads and writes eval artifacts under "
    "evals/llm_categorization and does not modify production transactions, taxonomy, rules, or finscope.db."
)


def resolve_under_root(path: str | Path, root: str | Path) -> Path:
    """Return a path under an artifact root, rejecting traversal outside it."""
    root_path = Path(root).resolve(strict=False)
    requested_path = Path(path)
    candidate = requested_path if requested_path.is_absolute() else root_path / requested_path
    resolved = candidate.resolve(strict=False)
    try:
        resolved.relative_to(root_path)
    except ValueError as exc:
        raise ValueError(f"path must stay under {root_path}: {path}") from exc
    return resolved


def file_modified_at(path: Path) -> float:
    """Return a file modification timestamp or zero when the path is absent."""
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0
