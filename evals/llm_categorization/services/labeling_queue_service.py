"""File-based manual labeling queue services for AI problem cases.

The service validates queue files produced by the dataset builder, records
manual labels, marks unusable items, and exports only labeled items as valid
evaluation JSONL examples. It does not open or modify FinScope databases.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from evals.llm_categorization.services import DATASETS_DIR, file_modified_at, resolve_under_root
from evals.llm_categorization.tools import validate_dataset
from evals.llm_categorization.tools.io_utils import JsonlError, load_jsonl, read_jsonl, write_jsonl
from evals.llm_categorization.tools.schemas import DatasetValidationError, validate_evaluation_example

QUEUE_STATUSES = {"pending", "labeled", "unusable"}
QUEUE_LABEL_SOURCES = {"pending_manual_label", "curated_by_researcher"}


@dataclass(frozen=True)
class LabelingQueueArtifact:
    """Represent one labeling queue JSONL artifact."""

    path: Path
    name: str
    size_bytes: int
    modified_at: float


@dataclass(frozen=True)
class LabelingQueueValidationResult:
    """Represent validation status for one labeling queue file."""

    path: Path
    valid: bool
    item_count: int
    labeled_count: int
    pending_count: int
    unusable_count: int
    errors: tuple[str, ...]


def list_labeling_queues(datasets_dir: Path | None = None) -> tuple[LabelingQueueArtifact, ...]:
    """List labeling queue JSONL files under the eval datasets directory."""
    datasets_dir = datasets_dir or DATASETS_DIR
    if not datasets_dir.exists():
        return ()
    artifacts = []
    for path in sorted(datasets_dir.glob("*_labeling_queue.jsonl"), key=lambda item: item.name.lower()):
        if not path.is_file():
            continue
        artifacts.append(
            LabelingQueueArtifact(
                path=path,
                name=path.name,
                size_bytes=path.stat().st_size,
                modified_at=file_modified_at(path),
            )
        )
    return tuple(artifacts)


def resolve_labeling_queue_path(queue_name: str, datasets_dir: Path | None = None) -> Path:
    """Resolve a queue filename safely under the eval datasets directory."""
    datasets_dir = datasets_dir or DATASETS_DIR
    if Path(queue_name).name != queue_name:
        raise ValueError("labeling queue name must not contain path separators")
    if not queue_name.endswith("_labeling_queue.jsonl"):
        raise ValueError("labeling queue name must end with _labeling_queue.jsonl")
    queue_path = resolve_under_root(queue_name, datasets_dir)
    if not queue_path.is_file():
        raise FileNotFoundError(queue_name)
    return queue_path


def read_labeling_queue(path: Path) -> tuple[dict[str, Any], ...]:
    """Read labeling queue items from JSONL."""
    return tuple(load_jsonl(path))


def validate_labeling_queue(path: Path) -> LabelingQueueValidationResult:
    """Validate a manual labeling queue file separately from eval datasets."""
    errors: list[str] = []
    item_count = 0
    status_counts = {"pending": 0, "labeled": 0, "unusable": 0}
    try:
        for line_number, item in read_jsonl(path):
            item_count += 1
            item_errors = validate_queue_item(item)
            status = item.get("label_status")
            if isinstance(status, str) and status in status_counts:
                status_counts[status] += 1
            errors.extend(
                f"line {line_number}, request {item.get('request_id', 'unknown')}: {error}" for error in item_errors
            )
    except JsonlError as exc:
        errors.append(str(exc))
    return LabelingQueueValidationResult(
        path=path,
        valid=not errors,
        item_count=item_count,
        labeled_count=status_counts["labeled"],
        pending_count=status_counts["pending"],
        unusable_count=status_counts["unusable"],
        errors=tuple(errors),
    )


def save_manual_label(
    queue_path: Path,
    request_id: str,
    *,
    category_id: str,
    tag_ids: Sequence[str],
    needs_review: bool,
    label_source: str = "curated_by_researcher",
    notes: str = "",
) -> None:
    """Save a manual ground-truth label for one pending queue item."""
    items = list(read_labeling_queue(queue_path))
    item = find_queue_item(items, request_id)
    validate_export_label(
        item, category_id=category_id, tag_ids=tag_ids, needs_review=needs_review, label_source=label_source
    )
    item["expected"] = {"category_id": category_id, "tag_ids": list(tag_ids), "needs_review": needs_review}
    item["label_status"] = "labeled"
    item["label_source"] = label_source
    coverage = ensure_mapping(item, "coverage")
    coverage["category"] = taxonomy_name(item, "categories", category_id)
    coverage["tags"] = [taxonomy_name(item, "tags", tag_id) for tag_id in tag_ids]
    coverage["ambiguity_type"] = exported_ambiguity_type(str(coverage.get("ambiguity_type") or "other"))
    note_text = "Manual label saved for export."
    if notes.strip():
        note_text = f"{note_text} Curator notes: {notes.strip()}"
    item["notes"] = append_note(str(item.get("notes") or ""), note_text)
    write_jsonl(queue_path, items)


def mark_queue_item_unusable(queue_path: Path, request_id: str, *, reason: str) -> None:
    """Mark a queue item unusable so it cannot be exported."""
    items = list(read_labeling_queue(queue_path))
    item = find_queue_item(items, request_id)
    item["label_status"] = "unusable"
    item["expected"] = None
    item["label_source"] = "pending_manual_label"
    item["notes"] = append_note(str(item.get("notes") or ""), f"Marked unusable: {reason}")
    write_jsonl(queue_path, items)


def export_labeled_queue(
    queue_path: Path,
    out_path: Path,
    *,
    merge_into: Path | None = None,
) -> tuple[dict[str, Any], ...]:
    """Export labeled queue items to valid evaluation JSONL records."""
    items = read_labeling_queue(queue_path)
    records = tuple(export_queue_item(item) for item in items if item.get("label_status") == "labeled")
    if not records:
        raise ValueError("no labeled queue items are available for export")

    merged_records: list[dict[str, Any]] = []
    if merge_into is not None and merge_into.exists():
        merged_records.extend(load_jsonl(merge_into))
    merged_records.extend(records)
    write_jsonl(out_path, merged_records)
    validate_dataset.validate_dataset(out_path)
    return records


def validate_queue_item(item: Mapping[str, Any]) -> list[str]:
    """Return validation errors for one queue item."""
    errors = []
    required_fields = {
        "request_id",
        "transaction",
        "candidate_taxonomy",
        "similar_transactions",
        "ai_observation",
        "label_status",
        "expected",
        "label_source",
        "privacy_level",
        "coverage",
        "notes",
    }
    missing = sorted(required_fields - set(item))
    if missing:
        errors.append(f"missing field(s): {', '.join(missing)}")
    if not isinstance(item.get("request_id"), str) or not item.get("request_id"):
        errors.append("request_id must be a non-empty string")
    status = item.get("label_status")
    if status not in QUEUE_STATUSES:
        errors.append("label_status must be pending, labeled, or unusable")
    if item.get("label_source") not in QUEUE_LABEL_SOURCES:
        errors.append("label_source is not valid for a labeling queue")
    if not isinstance(item.get("ai_observation"), Mapping):
        errors.append("ai_observation must be an object")
    if status == "pending" and item.get("expected") is not None:
        errors.append("pending items must have expected: null")
    if status == "unusable" and item.get("expected") is not None:
        errors.append("unusable items must have expected: null")
    if status == "labeled":
        try:
            validate_labeled_item(item)
        except (DatasetValidationError, ValueError) as exc:
            errors.append(str(exc))
    return errors


def validate_labeled_item(item: Mapping[str, Any]) -> None:
    """Validate a labeled queue item can become an eval example."""
    expected = item.get("expected")
    if not isinstance(expected, Mapping):
        raise ValueError("labeled items must include expected")
    if item.get("label_source") != "curated_by_researcher":
        raise ValueError("labeled items should use label_source curated_by_researcher")
    record = queue_item_to_eval_record(item)
    validate_evaluation_example(record)


def export_queue_item(item: Mapping[str, Any]) -> dict[str, Any]:
    """Convert one labeled queue item into a valid eval example."""
    if item.get("label_status") != "labeled":
        raise ValueError("only labeled queue items can be exported")
    validate_labeled_item(item)
    return queue_item_to_eval_record(item)


def queue_item_to_eval_record(item: Mapping[str, Any]) -> dict[str, Any]:
    """Return one valid eval example from a labeled queue item."""
    expected = dict(ensure_mapping(item, "expected"))
    coverage = dict(ensure_mapping(item, "coverage"))
    category_id = str(expected["category_id"])
    tag_ids = [str(tag_id) for tag_id in expected.get("tag_ids", [])]
    coverage["category"] = taxonomy_name(item, "categories", category_id)
    coverage["tags"] = [taxonomy_name(item, "tags", tag_id) for tag_id in tag_ids]
    coverage["ambiguity_type"] = exported_ambiguity_type(str(coverage.get("ambiguity_type") or "other"))
    notes = append_note(
        str(item.get("notes") or ""),
        "Original AI observation: " + json.dumps(item.get("ai_observation"), ensure_ascii=True, sort_keys=True),
    )
    return {
        "request_id": str(item["request_id"]),
        "transaction": dict(ensure_mapping(item, "transaction")),
        "candidate_taxonomy": dict(ensure_mapping(item, "candidate_taxonomy")),
        "similar_transactions": list(item.get("similar_transactions") or []),
        "expected": expected,
        "label_source": str(item.get("label_source") or "curated_by_researcher"),
        "privacy_level": str(item.get("privacy_level") or "redacted_real"),
        "coverage": coverage,
        "notes": notes,
    }


def validate_export_label(
    item: Mapping[str, Any],
    *,
    category_id: str,
    tag_ids: Sequence[str],
    needs_review: bool,
    label_source: str,
) -> None:
    """Validate a manual label against the queue item taxonomy."""
    if label_source != "curated_by_researcher":
        raise ValueError("manual queue labels must use curated_by_researcher")
    if not isinstance(needs_review, bool):
        raise ValueError("needs_review must be explicit boolean")
    taxonomy = ensure_mapping(item, "candidate_taxonomy")
    category_ids = taxonomy_ids(taxonomy, "categories")
    tag_id_set = taxonomy_ids(taxonomy, "tags")
    if category_id not in category_ids:
        raise ValueError(f"category_id {category_id!r} is not in candidate taxonomy")
    invalid_tags = [tag_id for tag_id in tag_ids if tag_id not in tag_id_set]
    if invalid_tags:
        raise ValueError(f"tag_ids are not in candidate taxonomy: {', '.join(invalid_tags)}")


def find_queue_item(items: Sequence[dict[str, Any]], request_id: str) -> dict[str, Any]:
    """Find a mutable queue item by request ID."""
    for item in items:
        if item.get("request_id") == request_id:
            return item
    raise ValueError(f"request_id not found in labeling queue: {request_id}")


def ensure_mapping(item: Mapping[str, Any], field_name: str) -> Mapping[str, Any]:
    """Return a required mapping field."""
    value = item.get(field_name)
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be an object")
    return value


def taxonomy_ids(taxonomy: Mapping[str, Any], collection_name: str) -> set[str]:
    """Return taxonomy IDs for a collection."""
    collection = taxonomy.get(collection_name)
    if not isinstance(collection, list):
        return set()
    return {str(item["id"]) for item in collection if isinstance(item, Mapping) and item.get("id")}


def taxonomy_name(item: Mapping[str, Any], collection_name: str, item_id: str) -> str:
    """Return taxonomy display name for an ID, falling back to the ID."""
    taxonomy = ensure_mapping(item, "candidate_taxonomy")
    collection = taxonomy.get(collection_name)
    if isinstance(collection, list):
        for taxonomy_item in collection:
            if isinstance(taxonomy_item, Mapping) and taxonomy_item.get("id") == item_id:
                return str(taxonomy_item.get("name") or item_id)
    return item_id


def exported_ambiguity_type(value: str) -> str:
    """Map queue-only AI ambiguity labels to the eval dataset schema."""
    if value in {"ai_unknown", "ai_needs_review", "ai_low_confidence", "ai_corrected_later"}:
        return "other"
    return value


def append_note(existing: str, addition: str) -> str:
    """Append a sentence to notes."""
    return f"{existing.rstrip()} {addition}".strip()
