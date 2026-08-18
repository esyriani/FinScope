"""Category assignment source metadata helpers."""

import json
from collections.abc import Iterable, Mapping, MutableMapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from finance_app.core.constants import (
    CATEGORY_SOURCE_AI,
    CATEGORY_SOURCE_HISTORY,
    CATEGORY_SOURCE_MANUAL,
    CATEGORY_SOURCE_RULE,
    CATEGORY_SOURCE_UNKNOWN,
    CATEGORY_SOURCES,
)
from finance_app.modules.categories.decision import (
    DECISION_SOURCE_MANUAL,
    normalize_decision_source,
)

CATEGORY_SOURCE_LABELS = {
    CATEGORY_SOURCE_UNKNOWN: "Unknown",
    CATEGORY_SOURCE_RULE: "Rule",
    CATEGORY_SOURCE_HISTORY: "Similarity",
    CATEGORY_SOURCE_AI: "AI",
    CATEGORY_SOURCE_MANUAL: "Manual",
}
CATEGORY_SOURCE_BADGE_CLASSES = {
    CATEGORY_SOURCE_UNKNOWN: "text-bg-secondary",
    CATEGORY_SOURCE_RULE: "text-bg-primary",
    CATEGORY_SOURCE_HISTORY: "text-bg-warning",
    CATEGORY_SOURCE_AI: "text-bg-info",
    CATEGORY_SOURCE_MANUAL: "text-bg-success",
}


@dataclass(frozen=True)
class CategoryAssignmentMetadata:
    """Represent persisted metadata for a transaction category assignment.

    The fields mirror the `transactions` category metadata columns. Use
    `to_dict()` when persisting assignment metadata through repository helpers.
    """

    category_source: str
    category_confidence: float | None = None
    category_rule_id: int | None = None
    category_metadata: str | None = None
    categorized_at: str | None = None
    reviewed_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return this assignment metadata as a database-column mapping."""
        return {
            "category_source": self.category_source,
            "category_confidence": self.category_confidence,
            "category_rule_id": self.category_rule_id,
            "category_metadata": self.category_metadata,
            "categorized_at": self.categorized_at,
            "reviewed_at": self.reviewed_at,
        }


@dataclass(frozen=True)
class TransactionCategoryState:
    """Represent the category fields controlled by categorization workflows.

    The object keeps the category label, review flag, assignment provenance,
    and tags together while workflow code decides how to apply a category.
    Use `to_dict()` at boundaries that still consume mutable transaction
    payloads or database row dictionaries.
    """

    category: str
    needs_review: int
    assignment: CategoryAssignmentMetadata
    tags: tuple[str, ...] = ()
    category_id: int | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return this state as the transaction payload fields it controls."""
        return {
            "category": self.category,
            "category_id": self.category_id,
            "needs_review": self.needs_review,
            **self.assignment.to_dict(),
            "tags": list(self.tags),
        }

    def apply_to(self, transaction: MutableMapping[str, Any]) -> None:
        """Apply this category state to a mutable transaction mapping."""
        transaction.update(self.to_dict())


@dataclass(frozen=True)
class TransactionCategorySnapshot:
    """Represent category state before or after a workflow change.

    Snapshots are used internally while building undo records for background
    workflow results.
    """

    category: str | None
    needs_review: int
    assignment: CategoryAssignmentMetadata
    transaction_kind: str
    tags: tuple[str, ...] = ()
    category_id: int | None = None

    @classmethod
    def from_row(cls, row: Mapping[str, Any], tags: Iterable[str] = ()) -> "TransactionCategorySnapshot":
        """Build a snapshot from a database row containing transaction category fields."""
        return cls(
            category=row["category"],
            needs_review=row["needs_review"],
            assignment=CategoryAssignmentMetadata(
                category_source=row["category_source"],
                category_confidence=row["category_confidence"],
                category_rule_id=row["category_rule_id"],
                category_metadata=row.get("category_metadata"),
                categorized_at=row["categorized_at"],
                reviewed_at=row["reviewed_at"],
            ),
            transaction_kind=row["transaction_kind"],
            tags=tuple(tags),
            category_id=row.get("category_id"),
        )

    def prefixed_dict(self, prefix: str) -> dict[str, Any]:
        """Return this snapshot with the undo-state key prefix applied."""
        return {
            f"{prefix}_category": self.category,
            f"{prefix}_category_id": self.category_id,
            f"{prefix}_needs_review": self.needs_review,
            f"{prefix}_category_source": self.assignment.category_source,
            f"{prefix}_category_confidence": self.assignment.category_confidence,
            f"{prefix}_category_rule_id": self.assignment.category_rule_id,
            f"{prefix}_category_metadata": self.assignment.category_metadata,
            f"{prefix}_categorized_at": self.assignment.categorized_at,
            f"{prefix}_reviewed_at": self.assignment.reviewed_at,
            f"{prefix}_transaction_kind": self.transaction_kind,
            f"{prefix}_tags": list(self.tags),
        }


@dataclass(frozen=True)
class TransactionCategoryChange:
    """Represent one category workflow change and its undo snapshot."""

    transaction_id: int
    old_state: TransactionCategorySnapshot
    new_state: TransactionCategorySnapshot

    def to_undo_record(self) -> dict[str, Any]:
        """Return this change as a background-job undo record."""
        return {
            "transaction_id": self.transaction_id,
            **self.old_state.prefixed_dict("old"),
            **self.new_state.prefixed_dict("new"),
        }


def utc_timestamp() -> str:
    """Return the current UTC timestamp for category metadata."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def category_metadata_json(metadata: object) -> str | None:
    """Return deterministic JSON text for persisted categorization evidence.

    Callers may pass a mapping/list payload or an already serialized JSON
    string. Empty values are stored as NULL.
    """
    if metadata is None:
        return None
    if isinstance(metadata, str):
        text = metadata.strip()
        if not text:
            return None
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return text
        if not isinstance(parsed, (dict, list)):
            return text
        metadata = parsed
    return json.dumps(
        normalize_category_metadata(metadata),
        ensure_ascii=True,
        sort_keys=True,
        default=str,
    )


def normalize_category_metadata(metadata: Any) -> Any:
    """Return metadata with controlled JSON audit source values.

    The helper leaves unrelated payload fields untouched and only normalizes
    `decision_source` keys wherever metadata is structured as dictionaries.
    """
    if isinstance(metadata, dict):
        normalized = {key: normalize_category_metadata(value) for key, value in metadata.items()}
        if "decision_source" in normalized:
            normalized["decision_source"] = normalize_decision_source(normalized["decision_source"])
        return normalized
    if isinstance(metadata, list):
        return [normalize_category_metadata(item) for item in metadata]
    return metadata


def category_assignment(
    category: object,
    unknown_category: object,
    source: object,
    confidence: float | None = None,
    rule_id: int | None = None,
    metadata: object | None = None,
) -> CategoryAssignmentMetadata:
    """Build typed metadata for an automatic or manual category assignment.

    Unknown or empty categories intentionally clear assignment provenance and
    timestamps. Known categories record the normalized source, optional
    confidence, optional rule ID, evidence metadata, and the categorization
    timestamp.
    """
    category_metadata = category_metadata_json(metadata)
    if category is None or category == unknown_category:
        return CategoryAssignmentMetadata(
            category_source=CATEGORY_SOURCE_UNKNOWN,
            category_metadata=category_metadata,
        )

    return CategoryAssignmentMetadata(
        category_source=normalize_category_source(source),
        category_confidence=confidence,
        category_rule_id=rule_id,
        category_metadata=category_metadata,
        categorized_at=utc_timestamp(),
        reviewed_at=None,
    )


def category_assignment_metadata(
    category: object,
    unknown_category: object,
    source: object,
    confidence: float | None = None,
    rule_id: int | None = None,
    metadata: object | None = None,
) -> dict[str, Any]:
    """Build normalized metadata mapping for a category assignment."""
    return category_assignment(
        category,
        unknown_category,
        source,
        confidence=confidence,
        rule_id=rule_id,
        metadata=metadata,
    ).to_dict()


def normalize_category_source(source: object) -> str:
    """Return a valid persisted category assignment source."""
    text = str(source or CATEGORY_SOURCE_UNKNOWN).strip().lower()
    return text if text in CATEGORY_SOURCES else CATEGORY_SOURCE_UNKNOWN


def category_source_label(source: object) -> str:
    """Return a compact display label for a persisted category source."""
    normalized_source = normalize_category_source(source)
    return CATEGORY_SOURCE_LABELS[normalized_source]


def category_source_badge_class(source: object) -> str:
    """Return the Bootstrap badge class for a persisted category source."""
    normalized_source = normalize_category_source(source)
    return CATEGORY_SOURCE_BADGE_CLASSES[normalized_source]


def category_confidence_label(confidence: Any) -> str:
    """Return a compact percentage label for optional category confidence."""
    if confidence is None:
        return ""
    try:
        value = float(confidence)
    except (TypeError, ValueError):
        return ""
    return f"{value:.0%}"


def manual_category_assignment(metadata: object | None = None) -> CategoryAssignmentMetadata:
    """Build typed metadata for a user-confirmed category assignment."""
    assigned_at = utc_timestamp()
    category_metadata = metadata or {"decision_source": DECISION_SOURCE_MANUAL}
    return CategoryAssignmentMetadata(
        category_source=CATEGORY_SOURCE_MANUAL,
        category_confidence=1.0,
        category_rule_id=None,
        category_metadata=category_metadata_json(category_metadata),
        categorized_at=assigned_at,
        reviewed_at=assigned_at,
    )


def manual_category_assignment_metadata(metadata: object | None = None) -> dict[str, Any]:
    """Build metadata mapping for a user-confirmed category assignment."""
    return manual_category_assignment(metadata=metadata).to_dict()
