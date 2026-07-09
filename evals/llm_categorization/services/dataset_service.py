"""Dataset services for local LLM categorization eval artifacts.

The functions wrap JSONL validation and summary helpers so CLI commands and the
Prompt Lab UI can inspect datasets without subprocesses or database access.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from evals.llm_categorization.services import DATASETS_DIR, file_modified_at, resolve_under_root
from evals.llm_categorization.tools import summarize_dataset, validate_dataset
from evals.llm_categorization.tools.io_utils import JsonlError
from evals.llm_categorization.tools.schemas import DatasetValidationError


@dataclass(frozen=True)
class DatasetArtifact:
    """Represent one dataset JSONL artifact."""

    path: Path
    name: str
    size_bytes: int
    modified_at: float


@dataclass(frozen=True)
class DatasetValidationResult:
    """Represent validation success or failure for one dataset."""

    path: Path
    valid: bool
    summary: validate_dataset.DatasetSummary | None
    error: str | None
    error_lines: tuple[str, ...]


def list_datasets(datasets_dir: Path | None = None) -> tuple[DatasetArtifact, ...]:
    """List JSONL datasets under the eval datasets directory."""
    datasets_dir = datasets_dir or DATASETS_DIR
    if not datasets_dir.exists():
        return ()
    artifacts = []
    for path in sorted(datasets_dir.glob("*.jsonl"), key=lambda item: item.name.lower()):
        if not path.is_file():
            continue
        artifacts.append(
            DatasetArtifact(
                path=path,
                name=path.name,
                size_bytes=path.stat().st_size,
                modified_at=file_modified_at(path),
            )
        )
    return tuple(artifacts)


def validate_dataset_file(path: Path) -> DatasetValidationResult:
    """Validate one JSONL dataset and return a structured result."""
    try:
        summary = validate_dataset.validate_dataset(path)
    except (DatasetValidationError, JsonlError, OSError) as exc:
        error = str(exc)
        return DatasetValidationResult(
            path=path,
            valid=False,
            summary=None,
            error=error,
            error_lines=validation_error_lines(error),
        )
    return DatasetValidationResult(path=path, valid=True, summary=summary, error=None, error_lines=())


def read_dataset_summary(path: Path) -> summarize_dataset.DatasetCurationSummary:
    """Return a curation summary for a strictly valid dataset."""
    _, examples = summarize_dataset.load_validated_records(path)
    return summarize_dataset.summarize_examples(path, examples)


def read_dataset_summaries(
    datasets_dir: Path | None = None,
) -> tuple[tuple[DatasetArtifact, DatasetValidationResult], ...]:
    """Return validation summaries for all JSONL dataset artifacts."""
    return tuple((artifact, validate_dataset_file(artifact.path)) for artifact in list_datasets(datasets_dir))


def resolve_dataset_path(dataset_name: str, datasets_dir: Path | None = None) -> Path:
    """Resolve one top-level dataset name safely under the datasets directory."""
    datasets_dir = datasets_dir or DATASETS_DIR
    if Path(dataset_name).name != dataset_name:
        raise ValueError("dataset name must not contain path separators")
    if Path(dataset_name).suffix.lower() != ".jsonl":
        raise ValueError("dataset name must end with .jsonl")
    dataset_path = resolve_under_root(dataset_name, datasets_dir)
    if not dataset_path.is_file():
        raise FileNotFoundError(dataset_name)
    return dataset_path


def read_dataset_examples(
    path: Path,
    *,
    request_id: str | None = None,
    limit: int | None = None,
) -> tuple[dict[str, Any], ...]:
    """Read validated dataset examples, optionally filtered by request ID or count."""
    if limit is not None and limit <= 0:
        raise ValueError("limit must be positive")
    records, examples = summarize_dataset.load_validated_records(path)
    paired_records = list(zip(records, examples))
    if request_id is not None:
        matches = [record for record, example in paired_records if example.request_id == request_id]
        if not matches:
            raise ValueError(f"request_id not found in dataset: {request_id}")
        return tuple(dict(record) for record in matches)
    if limit is not None:
        paired_records = paired_records[:limit]
    return tuple(dict(record) for record, _ in paired_records)


def validation_error_lines(error: str) -> tuple[str, ...]:
    """Return display-ready validation error lines."""
    lines = []
    for line in error.splitlines():
        stripped = line.strip()
        if not stripped or stripped.endswith("validation error(s):"):
            continue
        lines.append(stripped.removeprefix("- "))
    return tuple(lines or ([error] if error else ()))
