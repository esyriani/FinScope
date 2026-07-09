"""Coverage-driven dataset builder services.

This module loads file-based dataset build specifications, inspects a SQLite
FinScope database in read-only mode, previews coverage, and writes draft JSONL
datasets for manual curation. It does not call model providers or mutate runtime
FinScope data.
"""

from __future__ import annotations

import json
import random
import re
import sqlite3
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from evals.llm_categorization.services import DATASET_SPECS_DIR, DATASETS_DIR
from evals.llm_categorization.tools.build_dataset_from_db import (
    classification_cues,
    direction_from_amount,
    has_noisy_text,
)
from evals.llm_categorization.tools.inspect_db import (
    RoleCandidates,
    TableInfo,
    infer_roles,
    introspect_schema,
    open_readonly_sqlite,
    quote_identifier,
)
from evals.llm_categorization.tools.io_utils import write_jsonl

SPEC_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$")
LOW_CONFIDENCE_DEFAULT = 0.85
HIGH_CONFIDENCE_RULE_THRESHOLD = 0.95
STABLE_HISTORY_MIN_COUNT = 3
DRAFT_WARNING = "Draft dataset. Manual review is recommended before using this file as validation or test data."
TRUST_PRIORITY = {
    "manual_edit": 4,
    "reviewed": 3,
    "high_confidence_rule": 2,
    "stable_history": 1,
    "unknown": 0,
}

LABEL_SOURCE_POLICIES = {"prefer", "allow", "candidate_only", "exclude"}
LABEL_SOURCE_DEFAULTS = {
    "manual_edit": "prefer",
    "reviewed": "prefer",
    "high_confidence_rule": "allow",
    "stable_history": "allow",
    "curated_by_researcher": "allow",
    "synthetic": "exclude",
    "unknown": "candidate_only",
    "ai": "candidate_only",
    "unresolved": "candidate_only",
}
AI_PROBLEM_DEFAULTS = {
    "include": False,
    "max_examples": 20,
    "include_ai_unknown": True,
    "include_ai_needs_review": True,
    "include_low_confidence": True,
    "low_confidence_threshold": LOW_CONFIDENCE_DEFAULT,
    "include_ai_corrected_later": True,
    "require_manual_label_before_export": True,
}
TARGET_DEFAULTS = {
    "categories": {},
    "tags": {},
    "directions": {"debit": 0, "credit": 0},
    "review": {"needs_review_true": 0, "needs_review_false": 0},
    "tag_shape": {"no_tags": 0, "one_or_more_tags": 0},
    "ambiguity_types": {
        "straightforward": 0,
        "transfer_like": 0,
        "income_like": 0,
        "reimbursement_like": 0,
        "reimbursable_like": 0,
        "rental_like": 0,
        "tax_like": 0,
        "unknown_correct": 0,
        "ai_unknown": 0,
        "ai_needs_review": 0,
        "ai_low_confidence": 0,
        "ai_corrected_later": 0,
    },
}
SELECTION_DEFAULTS = {
    "max_per_near_duplicate_group": 2,
    "prefer_recent_manual_labels": True,
    "include_full_taxonomy": True,
    "write_labeling_queue": True,
    "write_adjudication_queue": True,
}
TOP_LEVEL_KEYS = {
    "name",
    "description",
    "max_examples",
    "seed",
    "redact",
    "label_sources",
    "ai_problem_cases",
    "targets",
    "selection",
}
TX_FIELD_CANDIDATES = {
    "id": ("id", "transaction_id", "tx_id"),
    "description": ("description", "raw_description", "original_description", "memo", "details", "name"),
    "merchant": ("merchant", "merchant_name", "normalized_merchant", "payee", "counterparty", "merchant_id"),
    "merchant_id": ("merchant_id", "merchant_key", "payee_id"),
    "amount": ("amount", "transaction_amount", "signed_amount", "value"),
    "date": ("date", "tx_date", "transaction_date", "posted_at", "posted_date"),
    "category": ("category_id", "category", "category_name"),
    "category_source": ("category_source", "assignment_source", "label_source", "source", "categorized_by"),
    "confidence": ("category_confidence", "confidence", "ai_confidence", "categorization_confidence"),
    "needs_review": ("needs_review", "review_required", "requires_review", "needs_attention"),
    "reviewed_at": ("reviewed_at", "reviewed_on", "verified_at"),
    "reviewed": ("reviewed", "is_reviewed", "verified", "is_verified"),
    "ignored": ("ignored", "is_ignored", "excluded"),
    "account_id": ("account_id",),
    "account": ("account", "account_name", "account_type"),
    "statement_id": ("statement_id",),
    "statement_type_id": ("statement_type_id",),
    "statement_type": ("statement_type", "statement_type_name", "parser_type"),
    "ai_called": ("ai_called", "llm_called", "openai_called", "ai_used"),
    "ai_category": ("ai_category_id", "ai_category", "llm_category_id", "model_category_id"),
    "ai_tags": ("ai_tag_ids", "ai_tags", "llm_tag_ids", "model_tag_ids"),
    "ai_confidence": ("ai_confidence", "llm_confidence", "model_confidence"),
    "ai_reason": ("ai_reason", "llm_reason", "model_reason", "ai_explanation"),
    "ai_needs_review": ("ai_needs_review", "llm_needs_review", "model_needs_review"),
    "ai_corrected_later": ("ai_corrected_later", "manually_corrected_after_ai", "ai_was_corrected"),
}


class DatasetSpecError(ValueError):
    """Represent an invalid dataset build specification."""


@dataclass(frozen=True)
class DatasetBuildSpec:
    """Represent a normalized dataset build specification."""

    path: Path | None
    name: str
    description: str
    max_examples: int
    seed: int
    redact: bool
    label_sources: dict[str, str]
    ai_problem_cases: dict[str, Any]
    targets: dict[str, dict[str, int]]
    selection: dict[str, Any]

    def requested_target_count(self) -> int:
        """Return the total requested examples across explicit targets."""
        return sum(count for section in self.targets.values() for count in section.values())


@dataclass(frozen=True)
class PreviewCandidate:
    """Represent one database transaction considered for dataset building."""

    transaction_id: str
    description: str | None
    merchant: str | None
    amount: float | None
    date: str | None
    account: str | None
    statement_type: str | None
    category_id: str | None
    category_name: str | None
    tag_ids: tuple[str, ...]
    tag_names: tuple[str, ...]
    label_source: str
    needs_review: bool | None
    confidence: float | None
    direction: str
    ambiguity_type: str
    near_duplicate_key: str
    ai_observation: dict[str, Any] | None = None
    adjudication_reason: str | None = None
    invalid_reason: str | None = None


@dataclass(frozen=True)
class CandidatePool:
    """Represent candidate transactions and schema metadata for preview."""

    candidates: tuple[PreviewCandidate, ...]
    categories: tuple[dict[str, str | None], ...]
    tags: tuple[dict[str, str | None], ...]
    excluded_count: int
    unavailable_fields: tuple[str, ...]
    missing_concepts: tuple[str, ...]
    inferred_roles: dict[str, str]


@dataclass(frozen=True)
class TargetPreview:
    """Represent one requested target's preview accounting."""

    target_type: str
    name: str
    requested: int
    found_candidates: int
    eligible_candidates: int
    possible_selected: int
    status: str


@dataclass(frozen=True)
class DatasetBuildPreview:
    """Represent a complete dataset build preview."""

    spec: DatasetBuildSpec
    db_path: Path
    candidate_pool: CandidatePool
    target_previews: tuple[TargetPreview, ...]
    possible_selected_count: int
    status: str

    @property
    def found_candidate_count(self) -> int:
        """Return the number of non-excluded candidates found in the database."""
        return len(self.candidate_pool.candidates)

    @property
    def eligible_candidate_count(self) -> int:
        """Return the number of candidates eligible as trusted labels."""
        return sum(1 for candidate in self.candidate_pool.candidates if candidate_is_eligible(candidate, self.spec))


@dataclass(frozen=True)
class DatasetBuildArtifacts:
    """Represent files written for one draft dataset build."""

    dataset_path: Path
    coverage_report_path: Path
    adjudication_path: Path
    labeling_queue_path: Path
    spec_used_path: Path


@dataclass(frozen=True)
class DatasetBuildResult:
    """Represent a completed draft dataset build."""

    spec: DatasetBuildSpec
    db_path: Path
    candidate_pool: CandidatePool
    selected_candidates: tuple[PreviewCandidate, ...]
    adjudication_candidates: tuple[PreviewCandidate, ...]
    target_previews: tuple[TargetPreview, ...]
    suppressed_duplicate_count: int
    records: tuple[dict[str, Any], ...]
    adjudication_records: tuple[dict[str, Any], ...]
    labeling_queue_records: tuple[dict[str, Any], ...]
    coverage_report: str
    artifacts: DatasetBuildArtifacts


def resolve_dataset_spec_path(spec: str | Path, specs_dir: Path | None = None) -> Path:
    """Resolve a JSON spec path safely under the eval dataset specs directory."""
    specs_dir = (specs_dir or DATASET_SPECS_DIR).resolve(strict=False)
    requested = Path(spec)
    if requested.suffix.lower() in {".yml", ".yaml"}:
        raise DatasetSpecError("YAML dataset specs are not supported by current Python dependencies; use .json.")
    if requested.suffix.lower() != ".json":
        raise DatasetSpecError("dataset spec path must end with .json")

    if requested.is_absolute():
        candidate = requested
    elif len(requested.parts) == 1:
        candidate = specs_dir / requested
    else:
        candidate = (Path.cwd() / requested).resolve(strict=False)
    resolved = candidate.resolve(strict=False)
    try:
        resolved.relative_to(specs_dir)
    except ValueError as exc:
        raise DatasetSpecError(f"dataset spec path must stay under {specs_dir}: {spec}") from exc
    if not resolved.is_file():
        raise FileNotFoundError(resolved)
    return resolved


def load_dataset_spec(path: Path) -> DatasetBuildSpec:
    """Load and validate one JSON dataset build specification."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise DatasetSpecError(f"invalid JSON in dataset spec: {exc.msg}") from exc
    if not isinstance(payload, Mapping):
        raise DatasetSpecError("dataset spec must contain a JSON object")
    return normalize_dataset_spec(payload, path=path)


def normalize_dataset_spec(payload: Mapping[str, Any], *, path: Path | None = None) -> DatasetBuildSpec:
    """Validate a raw dataset build spec and fill safe defaults."""
    unknown_keys = sorted(set(payload) - TOP_LEVEL_KEYS)
    if unknown_keys:
        raise DatasetSpecError(f"unknown dataset spec field(s): {', '.join(unknown_keys)}")

    name = required_string(payload, "name")
    if not SPEC_NAME_RE.fullmatch(name):
        raise DatasetSpecError("dataset spec name must use letters, numbers, underscores, or hyphens")
    description = optional_string(payload, "description", "")
    max_examples = positive_int(payload.get("max_examples", 100), "max_examples")
    seed = int_value(payload.get("seed", 42), "seed")
    redact = bool_value(payload.get("redact", True), "redact")
    label_sources = normalize_string_options(
        mapping_or_default(payload.get("label_sources"), LABEL_SOURCE_DEFAULTS, "label_sources"),
        LABEL_SOURCE_DEFAULTS,
        LABEL_SOURCE_POLICIES,
        "label_sources",
    )
    ai_problem_cases = normalize_ai_problem_cases(payload.get("ai_problem_cases"))
    targets = normalize_targets(payload.get("targets"))
    selection = normalize_selection(payload.get("selection"))
    return DatasetBuildSpec(
        path=path,
        name=name,
        description=description,
        max_examples=max_examples,
        seed=seed,
        redact=redact,
        label_sources=label_sources,
        ai_problem_cases=ai_problem_cases,
        targets=targets,
        selection=selection,
    )


def preview_dataset_build(db_path: Path, spec: DatasetBuildSpec) -> DatasetBuildPreview:
    """Inspect the database read-only and return deterministic coverage preview."""
    pool = build_candidate_pool(db_path, spec)
    selected_candidates = preview_selected_candidates(pool.candidates, spec)
    target_previews = tuple(build_target_previews(pool.candidates, selected_candidates, spec))
    status = preview_status(target_previews, spec)
    return DatasetBuildPreview(
        spec=spec,
        db_path=db_path,
        candidate_pool=pool,
        target_previews=target_previews,
        possible_selected_count=len(selected_candidates),
        status=status,
    )


def build_draft_dataset_from_spec(
    db_path: Path,
    spec: DatasetBuildSpec,
    *,
    datasets_dir: Path | None = None,
) -> DatasetBuildResult:
    """Build draft JSONL, coverage, adjudication, and spec snapshot artifacts."""
    datasets_dir = datasets_dir or DATASETS_DIR
    pool = build_candidate_pool(db_path, spec)
    selected_candidates, suppressed_duplicate_count = select_draft_candidates(pool.candidates, spec)
    adjudication_candidates = select_adjudication_candidates(pool.candidates, spec, selected_candidates)
    labeling_queue_candidates = select_labeling_queue_candidates(pool.candidates, spec, selected_candidates)
    target_previews = tuple(build_target_previews(pool.candidates, selected_candidates, spec))
    records = tuple(dataset_example_json(candidate, pool, spec) for candidate in selected_candidates)
    adjudication_records = tuple(adjudication_record_json(candidate, spec) for candidate in adjudication_candidates)
    labeling_queue_records = tuple(
        labeling_queue_record_json(candidate, pool, spec) for candidate in labeling_queue_candidates
    )

    artifacts = DatasetBuildArtifacts(
        dataset_path=datasets_dir / f"{spec.name}_draft.jsonl",
        coverage_report_path=datasets_dir / f"{spec.name}_coverage_report.md",
        adjudication_path=datasets_dir / f"{spec.name}_adjudication_needed.jsonl",
        labeling_queue_path=datasets_dir / f"{spec.name}_labeling_queue.jsonl",
        spec_used_path=datasets_dir / f"{spec.name}_spec_used.yml",
    )
    coverage_report = render_draft_coverage_report(
        spec=spec,
        db_path=db_path,
        pool=pool,
        selected_candidates=selected_candidates,
        target_previews=target_previews,
        suppressed_duplicate_count=suppressed_duplicate_count,
        records=records,
        labeling_queue_records=labeling_queue_records,
    )
    write_build_artifacts(artifacts, records, adjudication_records, labeling_queue_records, coverage_report, spec)
    return DatasetBuildResult(
        spec=spec,
        db_path=db_path,
        candidate_pool=pool,
        selected_candidates=selected_candidates,
        adjudication_candidates=adjudication_candidates,
        target_previews=target_previews,
        suppressed_duplicate_count=suppressed_duplicate_count,
        records=records,
        adjudication_records=adjudication_records,
        labeling_queue_records=labeling_queue_records,
        coverage_report=coverage_report,
        artifacts=artifacts,
    )


def open_dataset_builder_database(db_path: Path) -> sqlite3.Connection:
    """Open a SQLite database in read-only query-only mode for preview work."""
    return open_readonly_sqlite(db_path)


def build_candidate_pool(db_path: Path, spec: DatasetBuildSpec) -> CandidatePool:
    """Build a conservative real-transaction candidate pool from SQLite."""
    conn = open_dataset_builder_database(db_path)
    try:
        tables = introspect_schema(conn)
        roles = infer_roles(tables)
        return candidate_pool_from_roles(conn, roles, spec)
    finally:
        conn.close()


def candidate_pool_from_roles(
    conn: sqlite3.Connection,
    roles: RoleCandidates,
    spec: DatasetBuildSpec,
) -> CandidatePool:
    """Return candidates and schema availability from inferred table roles."""
    missing = missing_concepts(roles)
    unavailable: set[str] = set()
    inferred_roles = role_names(roles)
    if roles.transactions is None:
        return CandidatePool((), (), (), 0, tuple(sorted(unavailable)), missing, inferred_roles)

    categories = load_taxonomy(conn, roles.categories, unavailable, "category")
    tags = load_taxonomy(conn, roles.tags, unavailable, "tag")
    tag_assignments = load_tag_assignments(conn, roles.transaction_tags, tags, unavailable)
    account_lookup = load_simple_lookup(conn, roles.accounts, ("account_type", "type", "name"))
    statement_type_lookup = load_simple_lookup(conn, roles.statement_types, ("name", "parser_type", "type"))
    statement_to_type = load_statement_type_bridge(conn, roles)
    rows, columns = load_transaction_records(conn, roles.transactions)
    if spec.ai_problem_cases["include"] and not ai_evidence_columns_available(columns):
        unavailable.add("ai_evidence")

    stable_counts = stable_history_counts(rows, columns, categories)
    candidates = []
    excluded_count = 0
    for row in rows:
        candidate = candidate_from_row(
            row,
            columns,
            categories,
            tags,
            tag_assignments,
            account_lookup,
            statement_type_lookup,
            statement_to_type,
            stable_counts,
            spec,
            unavailable,
        )
        if candidate is None:
            continue
        if candidate.invalid_reason is not None:
            excluded_count += 1
            continue
        candidates.append(candidate)
    candidates.sort(
        key=lambda candidate: (candidate.transaction_id, candidate.category_name or "", candidate.direction)
    )
    return CandidatePool(
        candidates=tuple(candidates),
        categories=tuple(categories.values()),
        tags=tuple(tags.values()),
        excluded_count=excluded_count,
        unavailable_fields=tuple(sorted(unavailable)),
        missing_concepts=missing,
        inferred_roles=inferred_roles,
    )


def load_transaction_records(
    conn: sqlite3.Connection,
    transaction_table: TableInfo,
) -> tuple[tuple[sqlite3.Row, ...], Mapping[str, str | None]]:
    """Load raw transaction rows and selected column names."""
    columns = {field: choose_column(transaction_table, candidates) for field, candidates in TX_FIELD_CANDIDATES.items()}
    id_column = columns["id"]
    order_by = f" ORDER BY {quote_identifier(id_column)}" if id_column else ""
    rows = conn.execute(f"SELECT * FROM {quote_identifier(transaction_table.name)}{order_by}").fetchall()
    return tuple(rows), columns


def ai_evidence_columns_available(columns: Mapping[str, str | None]) -> bool:
    """Return whether schema supports any AI evidence detection."""
    return any(
        columns.get(field) is not None
        for field in (
            "category_source",
            "ai_called",
            "ai_category",
            "ai_tags",
            "ai_confidence",
            "ai_reason",
            "ai_needs_review",
            "ai_corrected_later",
        )
    )


def candidate_from_row(
    row: sqlite3.Row,
    columns: Mapping[str, str | None],
    categories: Mapping[str, Mapping[str, str]],
    tags: Mapping[str, Mapping[str, str]],
    tag_assignments: Mapping[str, tuple[str, ...]],
    account_lookup: Mapping[str, str],
    statement_type_lookup: Mapping[str, str],
    statement_to_type: Mapping[str, str],
    stable_counts: Mapping[tuple[str, str], int],
    spec: DatasetBuildSpec,
    unavailable: set[str],
) -> PreviewCandidate | None:
    """Build one preview candidate from an introspected transaction row."""
    if boolish(row_value(row, columns.get("ignored"))) is True:
        return None

    transaction_id = stable_text(row_value(row, columns.get("id"))) or str(abs(hash(tuple(row))))
    amount = numeric_or_none(row_value(row, columns.get("amount")))
    if amount is None:
        unavailable.add("amount")
    category_id, category_name = resolve_category(row_value(row, columns.get("category")), categories)
    tag_ids = tag_assignments.get(transaction_id, ())
    tag_names = tuple(tags[tag_id]["name"] for tag_id in tag_ids if tag_id in tags)
    invalid_reason = invalid_taxonomy_reason(category_id, category_name, tag_ids, categories, tags)
    needs_review = boolish(row_value(row, columns.get("needs_review")))
    reviewed = is_reviewed(row, columns)
    confidence = numeric_or_none(row_value(row, columns.get("confidence")))
    source = normalized_source(row_value(row, columns.get("category_source")))
    ai_observation = ai_observation_from_row(row, columns, categories, tags, source, category_id, tag_ids, spec)
    merchant_key = stable_text(row_value(row, columns.get("merchant_id")) or row_value(row, columns.get("merchant")))
    stable_count = stable_counts.get((merchant_key or "", category_id or category_name or ""), 0)
    label_source = label_source_for_candidate(source, category_name, reviewed, needs_review, confidence, stable_count)
    direction = direction_from_amount(amount) if amount is not None else "unknown"
    description = stable_text(row_value(row, columns.get("description")))
    merchant = stable_text(row_value(row, columns.get("merchant")))
    account = lookup_or_direct(row, columns.get("account_id"), columns.get("account"), account_lookup)
    statement_type = statement_type_for_row(row, columns, statement_type_lookup, statement_to_type)
    ambiguity_type = ambiguity_type_for_candidate(
        description=description,
        merchant=merchant,
        category_name=category_name,
        tag_names=tag_names,
        source=source,
        needs_review=needs_review,
        confidence=confidence,
        spec=spec,
        ai_observation=ai_observation,
    )
    adjudication_reason = adjudication_reason_for_candidate(
        label_source=label_source,
        source=source,
        needs_review=needs_review,
        reviewed=reviewed,
        confidence=confidence,
        stable_count=stable_count,
        spec=spec,
        ai_observation=ai_observation,
    )
    near_duplicate_key = near_duplicate_key_for_candidate(description, merchant, amount, category_id, tag_ids)
    for field, column_name in columns.items():
        if field in {"id", "ignored", "merchant_id", "account_id", "statement_id", "statement_type_id"}:
            continue
        if column_name is None and field in {
            "description",
            "merchant",
            "date",
            "account",
            "statement_type",
            "category",
            "category_source",
            "confidence",
            "needs_review",
        }:
            unavailable.add(field)
    return PreviewCandidate(
        transaction_id=transaction_id,
        description=description,
        merchant=merchant,
        amount=amount,
        date=stable_text(row_value(row, columns.get("date"))),
        account=account,
        statement_type=statement_type,
        category_id=category_id,
        category_name=category_name,
        tag_ids=tag_ids,
        tag_names=tag_names,
        label_source=label_source,
        needs_review=needs_review,
        confidence=confidence,
        direction=direction,
        ambiguity_type=ambiguity_type,
        near_duplicate_key=near_duplicate_key,
        ai_observation=ai_observation,
        adjudication_reason=adjudication_reason,
        invalid_reason=invalid_reason,
    )


def ai_observation_from_row(
    row: sqlite3.Row,
    columns: Mapping[str, str | None],
    categories: Mapping[str, Mapping[str, str | None]],
    tags: Mapping[str, Mapping[str, str | None]],
    source: str,
    current_category_id: str | None,
    current_tag_ids: Sequence[str],
    spec: DatasetBuildSpec,
) -> dict[str, Any] | None:
    """Return the original AI output observation when supported by schema."""
    if not spec.ai_problem_cases["include"]:
        return None

    ai_called = boolish(row_value(row, columns.get("ai_called")))
    ai_category_id, _ = resolve_category(row_value(row, columns.get("ai_category")), categories)
    ai_tag_ids = parse_ai_tag_ids(row_value(row, columns.get("ai_tags")), tags)
    ai_confidence = numeric_or_none(row_value(row, columns.get("ai_confidence")))
    ai_needs_review = boolish(row_value(row, columns.get("ai_needs_review")))
    ai_reason = stable_text(row_value(row, columns.get("ai_reason")))
    ai_corrected_later = boolish(row_value(row, columns.get("ai_corrected_later")))

    if source == "ai":
        ai_called = True if ai_called is None else ai_called
        ai_category_id = ai_category_id or current_category_id
        ai_tag_ids = ai_tag_ids or tuple(current_tag_ids)
        ai_confidence = (
            ai_confidence if ai_confidence is not None else numeric_or_none(row_value(row, columns.get("confidence")))
        )
        ai_needs_review = (
            ai_needs_review if ai_needs_review is not None else boolish(row_value(row, columns.get("needs_review")))
        )

    trusted_current_label = source in {"manual", "reviewed"} or is_reviewed(row, columns)
    if ai_corrected_later is None and trusted_current_label and ai_category_id is not None:
        ai_corrected_later = ai_category_id != current_category_id or tuple(ai_tag_ids) != tuple(current_tag_ids)

    if not ai_called and ai_category_id is None and ai_confidence is None and ai_needs_review is None:
        return None

    failure_type = ai_failure_type(
        ai_category_id=ai_category_id,
        ai_needs_review=ai_needs_review,
        ai_confidence=ai_confidence,
        ai_corrected_later=bool(ai_corrected_later),
        categories=categories,
        spec=spec,
    )
    if failure_type is None:
        return None
    return {
        "category_id": ai_category_id,
        "tag_ids": list(ai_tag_ids),
        "confidence": ai_confidence,
        "needs_review": bool(ai_needs_review) if ai_needs_review is not None else False,
        "reason": ai_reason or "AI observation was inferred from database fields.",
        "failure_type": failure_type,
    }


def parse_ai_tag_ids(value: Any, tags: Mapping[str, Mapping[str, str | None]]) -> tuple[str, ...]:
    """Parse AI tag IDs from JSON arrays or delimited strings."""
    if value is None:
        return ()
    parsed: Any = value
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return ()
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            parsed = [part.strip() for part in re.split(r"[,;|]", stripped) if part.strip()]
    if isinstance(parsed, str):
        parsed = [parsed]
    if not isinstance(parsed, Sequence):
        return ()
    tag_ids = []
    names_to_ids = {str(item.get("name")): item_id for item_id, item in tags.items() if item.get("name")}
    for item in parsed:
        tag_id = stable_text(item)
        if not tag_id:
            continue
        tag_ids.append(names_to_ids.get(tag_id, tag_id))
    return tuple(dict.fromkeys(tag_ids))


def ai_failure_type(
    *,
    ai_category_id: str | None,
    ai_needs_review: bool | None,
    ai_confidence: float | None,
    ai_corrected_later: bool,
    categories: Mapping[str, Mapping[str, str | None]],
    spec: DatasetBuildSpec,
) -> str | None:
    """Return the requested AI problem type for an observation."""
    category_name = categories.get(ai_category_id or "", {}).get("name")
    if ai_corrected_later and spec.ai_problem_cases["include_ai_corrected_later"]:
        return "ai_corrected_later"
    if category_name == "UNKNOWN" and spec.ai_problem_cases["include_ai_unknown"]:
        return "ai_unknown"
    if ai_needs_review is True and spec.ai_problem_cases["include_ai_needs_review"]:
        return "ai_needs_review"
    if (
        ai_confidence is not None
        and ai_confidence < spec.ai_problem_cases["low_confidence_threshold"]
        and spec.ai_problem_cases["include_low_confidence"]
    ):
        return "ai_low_confidence"
    return None


def load_taxonomy(
    conn: sqlite3.Connection,
    table: TableInfo | None,
    unavailable: set[str],
    concept_name: str,
) -> dict[str, dict[str, str | None]]:
    """Load category or tag taxonomy rows when a suitable table exists."""
    if table is None:
        unavailable.add(f"{concept_name}_taxonomy")
        return {}
    id_column = choose_column(table, ("id", f"{concept_name}_id"))
    name_column = choose_column(table, ("name", f"{concept_name}_name", concept_name))
    description_column = choose_column(table, ("description", "desc"))
    instruction_column = choose_column(table, ("instruction", "instructions", "prompt_instruction"))
    if id_column is None or name_column is None:
        unavailable.add(f"{concept_name}_taxonomy")
        return {}
    rows = conn.execute(
        f"SELECT * FROM {quote_identifier(table.name)} ORDER BY {quote_identifier(name_column)}, {quote_identifier(id_column)}"
    ).fetchall()
    taxonomy = {}
    for row in rows:
        item_id = stable_text(row[id_column])
        if not item_id:
            continue
        taxonomy[item_id] = {
            "id": item_id,
            "name": stable_text(row[name_column]) or item_id,
            "description": stable_text(row_value(row, description_column)) or "",
            "instruction": stable_text(row_value(row, instruction_column)),
        }
    return taxonomy


def load_tag_assignments(
    conn: sqlite3.Connection,
    table: TableInfo | None,
    tags: Mapping[str, Mapping[str, str]],
    unavailable: set[str],
) -> dict[str, tuple[str, ...]]:
    """Load transaction-tag assignments by transaction ID."""
    if table is None:
        unavailable.add("transaction_tags")
        return {}
    transaction_column = choose_column(table, ("transaction_id", "tx_id"))
    tag_column = choose_column(table, ("tag_id",))
    if transaction_column is None or tag_column is None:
        unavailable.add("transaction_tags")
        return {}
    rows = conn.execute(
        f"SELECT {quote_identifier(transaction_column)}, {quote_identifier(tag_column)} "
        f"FROM {quote_identifier(table.name)} "
        f"ORDER BY {quote_identifier(transaction_column)}, {quote_identifier(tag_column)}"
    ).fetchall()
    assignments: dict[str, list[str]] = defaultdict(list)
    for row in rows:
        transaction_id = stable_text(row[transaction_column])
        tag_id = stable_text(row[tag_column])
        if transaction_id and tag_id and (not tags or tag_id in tags):
            assignments[transaction_id].append(tag_id)
    return {transaction_id: tuple(tag_ids) for transaction_id, tag_ids in assignments.items()}


def load_simple_lookup(
    conn: sqlite3.Connection,
    table: TableInfo | None,
    label_columns: Sequence[str],
) -> dict[str, str]:
    """Load a simple ID-to-label lookup table when possible."""
    if table is None:
        return {}
    id_column = choose_column(table, ("id",))
    label_column = choose_column(table, label_columns)
    if id_column is None or label_column is None:
        return {}
    rows = conn.execute(
        f"SELECT {quote_identifier(id_column)}, {quote_identifier(label_column)} "
        f"FROM {quote_identifier(table.name)} ORDER BY {quote_identifier(id_column)}"
    ).fetchall()
    return {
        item_id: label
        for row in rows
        if (item_id := stable_text(row[id_column])) and (label := stable_text(row[label_column]))
    }


def load_statement_type_bridge(conn: sqlite3.Connection, roles: RoleCandidates) -> dict[str, str]:
    """Load statement ID to statement-type ID mappings when both tables are inferable."""
    if roles.statements is None:
        return {}
    statement_id_column = choose_column(roles.statements, ("id",))
    statement_type_id_column = choose_column(roles.statements, ("statement_type_id", "type_id"))
    if statement_id_column is None or statement_type_id_column is None:
        return {}
    rows = conn.execute(
        f"SELECT {quote_identifier(statement_id_column)}, {quote_identifier(statement_type_id_column)} "
        f"FROM {quote_identifier(roles.statements.name)} ORDER BY {quote_identifier(statement_id_column)}"
    ).fetchall()
    return {
        statement_id: statement_type_id
        for row in rows
        if (statement_id := stable_text(row[statement_id_column]))
        and (statement_type_id := stable_text(row[statement_type_id_column]))
    }


def stable_history_counts(
    rows: Sequence[sqlite3.Row],
    columns: Mapping[str, str | None],
    categories: Mapping[str, Mapping[str, str]],
) -> dict[tuple[str, str], int]:
    """Return repeated merchant/category counts used for stable-history labels."""
    counts: Counter[tuple[str, str]] = Counter()
    for row in rows:
        merchant_key = stable_text(
            row_value(row, columns.get("merchant_id")) or row_value(row, columns.get("merchant"))
        )
        category_id, category_name = resolve_category(row_value(row, columns.get("category")), categories)
        category_key = category_id or category_name
        if merchant_key and category_key:
            counts[(merchant_key, category_key)] += 1
    return dict(counts)


def build_target_previews(
    candidates: Sequence[PreviewCandidate],
    selected_candidates: Sequence[PreviewCandidate],
    spec: DatasetBuildSpec,
) -> list[TargetPreview]:
    """Return target accounting rows for requested coverage."""
    previews = []
    for target_type, target_values in spec.targets.items():
        for name, requested in sorted(target_values.items()):
            if requested <= 0:
                continue
            found = [candidate for candidate in candidates if candidate_matches_target(candidate, target_type, name)]
            eligible = [candidate for candidate in found if candidate_is_eligible(candidate, spec)]
            possible = sum(
                1 for candidate in selected_candidates if candidate_matches_target(candidate, target_type, name)
            )
            previews.append(
                TargetPreview(
                    target_type=target_type,
                    name=name,
                    requested=requested,
                    found_candidates=len(found),
                    eligible_candidates=len(eligible),
                    possible_selected=min(requested, possible),
                    status=target_status(requested, len(found), possible),
                )
            )
    return previews


def preview_selected_candidates(
    candidates: Sequence[PreviewCandidate],
    spec: DatasetBuildSpec,
) -> tuple[PreviewCandidate, ...]:
    """Return a deterministic preview selection without writing datasets."""
    eligible = [candidate for candidate in candidates if candidate_is_eligible(candidate, spec)]
    rng = random.Random(spec.seed)
    decorated = [(rng.random(), candidate.transaction_id, candidate) for candidate in eligible]
    decorated.sort()
    group_counts: Counter[str] = Counter()
    selected = []
    max_per_group = int(spec.selection["max_per_near_duplicate_group"])
    for _, _, candidate in decorated:
        if len(selected) >= spec.max_examples:
            break
        if group_counts[candidate.near_duplicate_key] >= max_per_group:
            continue
        group_counts[candidate.near_duplicate_key] += 1
        selected.append(candidate)
    return tuple(selected)


def select_draft_candidates(
    candidates: Sequence[PreviewCandidate],
    spec: DatasetBuildSpec,
) -> tuple[tuple[PreviewCandidate, ...], int]:
    """Select draft examples greedily by unmet target coverage."""
    eligible = [candidate for candidate in candidates if candidate_is_eligible(candidate, spec)]
    if not eligible:
        return (), 0

    unmet = positive_target_counts(spec)
    tie_breakers = seeded_tie_breakers(eligible, spec.seed)
    group_counts: Counter[str] = Counter()
    suppressed_duplicate_count = 0
    selected: list[PreviewCandidate] = []
    remaining = set(range(len(eligible)))
    max_per_group = int(spec.selection["max_per_near_duplicate_group"])

    while remaining and len(selected) < spec.max_examples:
        best_index: int | None = None
        best_key: tuple[int, int, float, float, str] | None = None
        for index in sorted(remaining):
            candidate = eligible[index]
            if group_counts[candidate.near_duplicate_key] >= max_per_group:
                suppressed_duplicate_count += 1
                remaining.remove(index)
                break
            gain = unmet_target_gain(candidate, unmet)
            if unmet and gain <= 0:
                continue
            key = (
                gain,
                TRUST_PRIORITY.get(candidate.label_source, -1),
                candidate.confidence if candidate.confidence is not None else 0.0,
                -tie_breakers[candidate.transaction_id],
                candidate.transaction_id,
            )
            if best_key is None or key > best_key:
                best_key = key
                best_index = index
        else:
            if best_index is None:
                if unmet:
                    break
                best_index = best_unmet_free_candidate(eligible, remaining, group_counts, max_per_group, tie_breakers)
                if best_index is None:
                    break

            candidate = eligible[best_index]
            selected.append(candidate)
            group_counts[candidate.near_duplicate_key] += 1
            remaining.remove(best_index)
            decrement_unmet_targets(candidate, unmet)
            if not unmet and positive_target_total(spec) > 0:
                break
            continue
        continue

    return tuple(selected), suppressed_duplicate_count


def best_unmet_free_candidate(
    candidates: Sequence[PreviewCandidate],
    remaining: set[int],
    group_counts: Counter[str],
    max_per_group: int,
    tie_breakers: Mapping[str, float],
) -> int | None:
    """Return the highest-trust candidate when no targets were requested."""
    best_index = None
    best_key: tuple[int, float, float, str] | None = None
    for index in sorted(remaining):
        candidate = candidates[index]
        if group_counts[candidate.near_duplicate_key] >= max_per_group:
            continue
        key = (
            TRUST_PRIORITY.get(candidate.label_source, -1),
            candidate.confidence if candidate.confidence is not None else 0.0,
            -tie_breakers[candidate.transaction_id],
            candidate.transaction_id,
        )
        if best_key is None or key > best_key:
            best_key = key
            best_index = index
    return best_index


def select_adjudication_candidates(
    candidates: Sequence[PreviewCandidate],
    spec: DatasetBuildSpec,
    selected_candidates: Sequence[PreviewCandidate],
) -> tuple[PreviewCandidate, ...]:
    """Return candidate-only rows for manual adjudication or labeling queues."""
    selected_ids = {candidate.transaction_id for candidate in selected_candidates}
    rows = [
        candidate
        for candidate in candidates
        if candidate.transaction_id not in selected_ids
        and (candidate.adjudication_reason is not None or not candidate_is_eligible(candidate, spec))
    ]
    rows.sort(
        key=lambda candidate: (
            candidate.adjudication_reason is None,
            -unmet_target_gain(candidate, positive_target_counts(spec)),
            candidate.transaction_id,
        )
    )
    max_ai_rows = int(spec.ai_problem_cases["max_examples"])
    return tuple(rows[: max(spec.max_examples, max_ai_rows)])


def select_labeling_queue_candidates(
    candidates: Sequence[PreviewCandidate],
    spec: DatasetBuildSpec,
    selected_candidates: Sequence[PreviewCandidate],
) -> tuple[PreviewCandidate, ...]:
    """Return AI problem candidates that require manual labels."""
    if not spec.ai_problem_cases["include"]:
        return ()
    selected_ids = {candidate.transaction_id for candidate in selected_candidates}
    rows = [
        candidate
        for candidate in candidates
        if candidate.transaction_id not in selected_ids
        and candidate.ai_observation is not None
        and not candidate_is_eligible(candidate, spec)
    ]
    rows.sort(key=lambda candidate: (str(candidate.ai_observation["failure_type"]), candidate.transaction_id))
    return tuple(rows[: int(spec.ai_problem_cases["max_examples"])])


def positive_target_counts(spec: DatasetBuildSpec) -> dict[tuple[str, str], int]:
    """Return positive target counts keyed by target type and name."""
    return {
        (target_type, name): count
        for target_type, values in spec.targets.items()
        for name, count in values.items()
        if count > 0
    }


def positive_target_total(spec: DatasetBuildSpec) -> int:
    """Return the number of positive target rows."""
    return len(positive_target_counts(spec))


def seeded_tie_breakers(candidates: Sequence[PreviewCandidate], seed: int) -> dict[str, float]:
    """Return deterministic random tie breakers by transaction ID."""
    rng = random.Random(seed)
    return {
        candidate.transaction_id: rng.random() for candidate in sorted(candidates, key=lambda row: row.transaction_id)
    }


def unmet_target_gain(candidate: PreviewCandidate, unmet: Mapping[tuple[str, str], int]) -> int:
    """Return how many currently unmet target rows the candidate satisfies."""
    return sum(
        1
        for (target_type, name), remaining in unmet.items()
        if remaining > 0 and candidate_matches_target(candidate, target_type, name)
    )


def decrement_unmet_targets(candidate: PreviewCandidate, unmet: dict[tuple[str, str], int]) -> None:
    """Decrement target counts satisfied by a selected candidate."""
    for key, remaining in list(unmet.items()):
        if remaining <= 0:
            del unmet[key]
            continue
        target_type, name = key
        if candidate_matches_target(candidate, target_type, name):
            next_remaining = remaining - 1
            if next_remaining <= 0:
                del unmet[key]
            else:
                unmet[key] = next_remaining


def candidate_matches_target(candidate: PreviewCandidate, target_type: str, name: str) -> bool:
    """Return whether a candidate covers one target row."""
    if target_type == "categories":
        return name in {candidate.category_id, candidate.category_name}
    if target_type == "tags":
        return name in set(candidate.tag_ids) | set(candidate.tag_names)
    if target_type == "directions":
        return candidate.direction == name
    if target_type == "review":
        return (name == "needs_review_true" and candidate.needs_review is True) or (
            name == "needs_review_false" and candidate.needs_review is False
        )
    if target_type == "tag_shape":
        return (name == "no_tags" and not candidate.tag_ids) or (name == "one_or_more_tags" and bool(candidate.tag_ids))
    if target_type == "ambiguity_types":
        return candidate.ambiguity_type == name
    return False


def candidate_is_eligible(candidate: PreviewCandidate, spec: DatasetBuildSpec) -> bool:
    """Return whether a candidate can count as trusted ground truth for export."""
    if candidate.invalid_reason is not None or candidate.adjudication_reason is not None:
        return False
    if candidate.amount is None or candidate.category_id is None:
        return False
    if candidate.label_source == "ai":
        return False
    if (
        candidate.ambiguity_type.startswith("ai_")
        and spec.ai_problem_cases["require_manual_label_before_export"]
        and not (
            candidate.ambiguity_type == "ai_corrected_later" and candidate.label_source in {"manual_edit", "reviewed"}
        )
    ):
        return False
    if candidate.label_source == "unknown" and candidate.category_name == "UNKNOWN":
        return spec.targets["ambiguity_types"].get("unknown_correct", 0) > 0
    return spec.label_sources.get(candidate.label_source, "candidate_only") in {"prefer", "allow"}


def target_status(requested: int, found: int, eligible: int) -> str:
    """Return OK, short, or missing for one requested target."""
    if requested <= 0 or eligible >= requested:
        return "OK"
    if found > 0 or eligible > 0:
        return "short"
    return "missing"


def preview_status(target_previews: Sequence[TargetPreview], spec: DatasetBuildSpec) -> str:
    """Return the overall preview status."""
    if not target_previews:
        return "OK"
    statuses = {target.status for target in target_previews}
    if "missing" in statuses:
        return "missing"
    if "short" in statuses:
        return "short"
    if sum(target.possible_selected for target in target_previews) > spec.max_examples:
        return "short"
    return "OK"


def dataset_example_json(
    candidate: PreviewCandidate,
    pool: CandidatePool,
    spec: DatasetBuildSpec,
) -> dict[str, Any]:
    """Return one strict evaluation JSONL record for a selected draft candidate."""
    category_id = candidate.category_id
    if category_id is None or candidate.amount is None:
        raise DatasetSpecError(f"candidate {candidate.transaction_id} is missing required label or amount")
    taxonomy = candidate_taxonomy_json(candidate, pool, spec)
    return {
        "request_id": f"db-tx-{candidate.transaction_id}",
        "transaction": {
            "description": transaction_description(candidate, spec),
            "merchant": transaction_merchant(candidate, spec),
            "amount": round(candidate.amount, 2),
            "date": candidate.date,
            "account": candidate.account,
            "statement_type": candidate.statement_type,
        },
        "candidate_taxonomy": taxonomy,
        "similar_transactions": [],
        "expected": {
            "category_id": category_id,
            "tag_ids": list(candidate.tag_ids),
            "needs_review": expected_needs_review(candidate),
        },
        "label_source": candidate.label_source,
        "privacy_level": "redacted_real" if spec.redact else "raw_real",
        "coverage": {
            "category": candidate.category_name,
            "tags": list(candidate.tag_names),
            "direction": candidate.direction,
            "statement_type": candidate.statement_type,
            "confidence_band": confidence_band(candidate.confidence),
            "ambiguity_type": schema_ambiguity_type(candidate.ambiguity_type),
        },
        "notes": draft_notes(candidate),
    }


def adjudication_record_json(candidate: PreviewCandidate, spec: DatasetBuildSpec) -> dict[str, Any]:
    """Return one adjudication queue row, redacted according to the spec."""
    return {
        "request_id": f"db-tx-{candidate.transaction_id}",
        "transaction": {
            "description": transaction_description(candidate, spec),
            "merchant": transaction_merchant(candidate, spec),
            "amount": round(candidate.amount, 2) if candidate.amount is not None else None,
            "date": candidate.date,
            "account": candidate.account,
            "statement_type": candidate.statement_type,
        },
        "current_label": {
            "category_id": candidate.category_id,
            "category_name": candidate.category_name,
            "tag_ids": list(candidate.tag_ids),
            "tag_names": list(candidate.tag_names),
            "label_source": candidate.label_source,
            "confidence": candidate.confidence,
            "needs_review": candidate.needs_review,
        },
        "coverage": {
            "direction": candidate.direction,
            "ambiguity_type": candidate.ambiguity_type,
        },
        "reason": candidate.adjudication_reason or "Candidate is not eligible as trusted ground truth.",
        "privacy_level": "redacted_real" if spec.redact else "raw_real",
    }


def labeling_queue_record_json(
    candidate: PreviewCandidate,
    pool: CandidatePool,
    spec: DatasetBuildSpec,
) -> dict[str, Any]:
    """Return one pending manual-label queue item for an AI problem case."""
    if candidate.ai_observation is None:
        raise DatasetSpecError(f"candidate {candidate.transaction_id} has no AI observation")
    return {
        "request_id": f"ai-problem-{candidate.transaction_id}",
        "transaction": {
            "description": transaction_description(candidate, spec),
            "merchant": transaction_merchant(candidate, spec),
            "amount": round(candidate.amount, 2) if candidate.amount is not None else 0.0,
            "date": candidate.date,
            "account": candidate.account,
            "statement_type": candidate.statement_type,
        },
        "candidate_taxonomy": {"categories": list(pool.categories), "tags": list(pool.tags)},
        "similar_transactions": [],
        "ai_observation": candidate.ai_observation,
        "label_status": "pending",
        "expected": None,
        "label_source": "pending_manual_label",
        "privacy_level": "redacted_real" if spec.redact else "raw_real",
        "coverage": {
            "category": None,
            "tags": [],
            "direction": candidate.direction,
            "statement_type": candidate.statement_type,
            "confidence_band": confidence_band(numeric_or_none(candidate.ai_observation.get("confidence"))),
            "ambiguity_type": candidate.ai_observation["failure_type"],
        },
        "notes": ai_problem_notes(candidate.ai_observation),
    }


def ai_problem_notes(ai_observation: Mapping[str, Any]) -> str:
    """Return a concise labeling note for an AI problem queue item."""
    failure_type = ai_observation.get("failure_type")
    if failure_type == "ai_unknown":
        return "AI returned UNKNOWN. Requires manual ground-truth label before use."
    if failure_type == "ai_needs_review":
        return "AI marked the transaction as needing review. Requires manual ground-truth label before use."
    if failure_type == "ai_low_confidence":
        return "AI confidence was below the configured threshold. Requires manual ground-truth label before use."
    if failure_type == "ai_corrected_later":
        return "AI output appears to have been corrected later. Confirm the final manual label before use."
    return "AI problem case requires manual ground-truth label before use."


def candidate_taxonomy_json(
    candidate: PreviewCandidate,
    pool: CandidatePool,
    spec: DatasetBuildSpec,
) -> dict[str, list[dict[str, str | None]]]:
    """Return full or minimal candidate taxonomy for one example."""
    if spec.selection["include_full_taxonomy"]:
        return {"categories": list(pool.categories), "tags": list(pool.tags)}

    categories = [item for item in pool.categories if item["id"] == candidate.category_id]
    tags = [item for item in pool.tags if item["id"] in set(candidate.tag_ids)]
    return {"categories": categories, "tags": tags}


def transaction_description(candidate: PreviewCandidate, spec: DatasetBuildSpec) -> str:
    """Return redacted or raw transaction description for a draft example."""
    if not spec.redact:
        return candidate.description or "transaction"
    return redacted_text(candidate, "transaction")


def transaction_merchant(candidate: PreviewCandidate, spec: DatasetBuildSpec) -> str | None:
    """Return redacted or raw merchant context for a draft example."""
    if candidate.merchant is None:
        return None
    if not spec.redact:
        return candidate.merchant
    return redacted_text(candidate, "merchant")


def redacted_text(candidate: PreviewCandidate, label: str) -> str:
    """Return privacy-minimized text preserving classification cues."""
    cues = classification_cues(
        " ".join(
            value
            for value in (
                candidate.description,
                candidate.merchant,
                candidate.category_name,
                " ".join(candidate.tag_names),
            )
            if value
        )
    )
    if cues:
        return f"redacted {label}: {', '.join(cues)}"
    return f"redacted {label}"


def expected_needs_review(candidate: PreviewCandidate) -> bool:
    """Infer expected needs_review conservatively for selected high-trust labels."""
    if candidate.category_name == "UNKNOWN":
        return True
    clear_ambiguity_types = {
        "straightforward",
        "transfer_like",
        "income_like",
        "reimbursement_like",
        "reimbursable_like",
        "rental_like",
        "tax_like",
    }
    if candidate.label_source in {"manual_edit", "reviewed", "high_confidence_rule", "stable_history"}:
        return candidate.ambiguity_type not in clear_ambiguity_types
    return True


def confidence_band(confidence: float | None) -> str:
    """Return the dataset confidence band for one candidate."""
    if confidence is None:
        return "unknown"
    if confidence >= HIGH_CONFIDENCE_RULE_THRESHOLD:
        return "high"
    if confidence >= LOW_CONFIDENCE_DEFAULT:
        return "medium"
    return "low"


def schema_ambiguity_type(ambiguity_type: str) -> str:
    """Map builder-only ambiguity labels to the current dataset schema."""
    if ambiguity_type in {"ai_unknown", "ai_needs_review", "ai_low_confidence", "ai_corrected_later"}:
        return "other"
    return ambiguity_type


def draft_notes(candidate: PreviewCandidate) -> str:
    """Return concise draft curation notes."""
    notes = [
        "Draft generated from read-only database preview.",
        f"Label source: {candidate.label_source}.",
        f"Ambiguity type: {candidate.ambiguity_type}.",
    ]
    if candidate.confidence is not None:
        notes.append(f"Stored confidence band: {confidence_band(candidate.confidence)}.")
    if candidate.ai_observation is not None:
        notes.append(
            f"AI observation preserved: {json.dumps(candidate.ai_observation, ensure_ascii=True, sort_keys=True)}."
        )
    return " ".join(notes)


def render_draft_coverage_report(
    *,
    spec: DatasetBuildSpec,
    db_path: Path,
    pool: CandidatePool,
    selected_candidates: Sequence[PreviewCandidate],
    target_previews: Sequence[TargetPreview],
    suppressed_duplicate_count: int,
    records: Sequence[Mapping[str, Any]],
    labeling_queue_records: Sequence[Mapping[str, Any]],
) -> str:
    """Render the draft dataset coverage report."""
    lines = [
        "# Draft Dataset Coverage Report",
        "",
        DRAFT_WARNING,
        "",
        f"- Spec: `{spec.name}`",
        f"- Database: `{db_path}`",
        f"- Selected examples count: {len(selected_candidates)}",
        f"- Candidate pool count: {len(pool.candidates)}",
        f"- Labeling queue count: {len(labeling_queue_records)}",
        f"- Suppressed duplicate groups: {suppressed_duplicate_count}",
        "",
        "## Target coverage",
        "",
    ]
    if target_previews:
        lines.extend(
            [
                "| Target | Requested | Found | Selected | Status |",
                "| --- | ---: | ---: | ---: | --- |",
            ]
        )
        for target in target_previews:
            lines.append(
                f"| {target.target_type}.{target.name} | {target.requested} | {target.found_candidates} | "
                f"{target.possible_selected} | {target.status} |"
            )
    else:
        lines.append("- No non-zero targets requested.")

    lines.extend(counter_section("Selected examples by category", count_nested(records, ("coverage", "category"))))
    lines.extend(counter_section("Selected examples by tag", count_tags(records)))
    lines.extend(counter_section("Debit/credit counts", count_nested(records, ("coverage", "direction"))))
    lines.extend(counter_section("needs-review counts", count_nested(records, ("expected", "needs_review"))))
    lines.extend(counter_section("No-tag versus tagged counts", count_tag_shape(records)))
    lines.extend(counter_section("Label source counts", count_nested(records, ("label_source",))))
    lines.extend(counter_section("Privacy level counts", count_nested(records, ("privacy_level",))))
    lines.extend(["", "## AI problem cases", ""])
    if "ai_evidence" in pool.unavailable_fields:
        lines.append("- AI evidence could not be found in the inferred schema.")
    elif labeling_queue_records:
        lines.extend(
            counter_section(
                "AI problem labeling queue", count_nested(labeling_queue_records, ("coverage", "ambiguity_type"))
            )
        )
    else:
        lines.append("- No pending AI problem cases were added to the labeling queue.")
    lines.extend(["## Coverage shortages", ""])
    shortages = [target for target in target_previews if target.status != "OK"]
    if shortages:
        lines.extend(f"- {target.target_type}.{target.name}: {target.status}" for target in shortages)
    else:
        lines.append("- None detected from selected draft examples.")
    lines.extend(["", "## Missing categories/tags", ""])
    missing_categories = missing_taxonomy_names(pool.categories, records, ("coverage", "category"))
    missing_tags = missing_taxonomy_names(pool.tags, records, ("coverage", "tags"))
    lines.append(f"- Missing categories: {format_missing(missing_categories)}")
    lines.append(f"- Missing tags: {format_missing(missing_tags)}")
    lines.extend(["", "## Recommendations", ""])
    if shortages:
        lines.extend(
            f"- Add manually labeled or clearly synthetic examples for `{target.target_type}.{target.name}`; "
            "do not fabricate real transactions."
            for target in shortages
        )
    else:
        lines.append("- Manually review the draft before using it as validation or test data.")
    return "\n".join(lines)


def counter_section(title: str, counter: Counter[str]) -> list[str]:
    """Render a Markdown counter section."""
    lines = ["", f"## {title}", ""]
    if not counter:
        lines.append("- None.")
    else:
        lines.extend(f"- `{label}`: {count}" for label, count in sorted(counter.items()))
    return lines


def count_nested(records: Sequence[Mapping[str, Any]], path: Sequence[str]) -> Counter[str]:
    """Count nested values from generated dataset records."""
    counter: Counter[str] = Counter()
    for record in records:
        value: Any = record
        for key in path:
            if not isinstance(value, Mapping):
                value = None
                break
            value = value.get(key)
        counter[str(value)] += 1
    return counter


def count_tags(records: Sequence[Mapping[str, Any]]) -> Counter[str]:
    """Count selected coverage tags."""
    counter: Counter[str] = Counter()
    for record in records:
        coverage = record.get("coverage")
        if not isinstance(coverage, Mapping):
            continue
        tags = coverage.get("tags")
        if isinstance(tags, Sequence) and not isinstance(tags, str):
            for tag in tags:
                counter[str(tag)] += 1
    return counter


def count_tag_shape(records: Sequence[Mapping[str, Any]]) -> Counter[str]:
    """Count examples with and without expected tags."""
    counter: Counter[str] = Counter()
    for record in records:
        expected = record.get("expected")
        tag_ids = expected.get("tag_ids") if isinstance(expected, Mapping) else []
        counter["one_or_more_tags" if tag_ids else "no_tags"] += 1
    return counter


def missing_taxonomy_names(
    taxonomy_items: Sequence[Mapping[str, str | None]],
    records: Sequence[Mapping[str, Any]],
    coverage_path: Sequence[str],
) -> tuple[str, ...]:
    """Return taxonomy item names not represented in generated coverage."""
    all_names = {str(item["name"]) for item in taxonomy_items if item.get("name")}
    represented: set[str] = set()
    for record in records:
        value: Any = record
        for key in coverage_path:
            if not isinstance(value, Mapping):
                value = None
                break
            value = value.get(key)
        if isinstance(value, Sequence) and not isinstance(value, str):
            represented.update(str(item) for item in value)
        elif value is not None:
            represented.add(str(value))
    return tuple(sorted(all_names - represented))


def format_missing(values: Sequence[str]) -> str:
    """Return a compact missing coverage list."""
    return "None" if not values else ", ".join(values)


def write_build_artifacts(
    artifacts: DatasetBuildArtifacts,
    records: Sequence[Mapping[str, Any]],
    adjudication_records: Sequence[Mapping[str, Any]],
    labeling_queue_records: Sequence[Mapping[str, Any]],
    coverage_report: str,
    spec: DatasetBuildSpec,
) -> None:
    """Write all draft build artifacts."""
    write_jsonl(artifacts.dataset_path, records)
    write_jsonl(artifacts.adjudication_path, adjudication_records)
    write_jsonl(artifacts.labeling_queue_path, labeling_queue_records)
    artifacts.coverage_report_path.parent.mkdir(parents=True, exist_ok=True)
    artifacts.coverage_report_path.write_text(f"{coverage_report}\n", encoding="utf-8", newline="\n")
    artifacts.spec_used_path.write_text(dump_simple_yaml(spec_snapshot(spec)), encoding="utf-8", newline="\n")


def spec_snapshot(spec: DatasetBuildSpec) -> dict[str, Any]:
    """Return a serializable normalized spec snapshot."""
    return {
        "name": spec.name,
        "description": spec.description,
        "max_examples": spec.max_examples,
        "seed": spec.seed,
        "redact": spec.redact,
        "label_sources": spec.label_sources,
        "ai_problem_cases": spec.ai_problem_cases,
        "targets": spec.targets,
        "selection": spec.selection,
    }


def dump_simple_yaml(value: Any, *, indent: int = 0) -> str:
    """Render a small YAML subset without adding a YAML dependency."""
    prefix = " " * indent
    if isinstance(value, Mapping):
        lines = []
        for key, item in value.items():
            if isinstance(item, Mapping):
                lines.append(f"{prefix}{key}:")
                lines.append(dump_simple_yaml(item, indent=indent + 2))
            elif isinstance(item, list | tuple):
                lines.append(f"{prefix}{key}:")
                lines.append(dump_simple_yaml(item, indent=indent + 2))
            else:
                lines.append(f"{prefix}{key}: {yaml_scalar(item)}")
        return "\n".join(lines) + ("\n" if indent == 0 else "")
    if isinstance(value, list | tuple):
        if not value:
            return f"{prefix}[]"
        return "\n".join(f"{prefix}- {yaml_scalar(item)}" for item in value)
    return f"{prefix}{yaml_scalar(value)}"


def yaml_scalar(value: Any) -> str:
    """Render a scalar for the simple YAML snapshot."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float) and not isinstance(value, bool):
        return str(value)
    return json.dumps(str(value), ensure_ascii=True)


def render_preview_report(preview: DatasetBuildPreview) -> str:
    """Render a deterministic Markdown dataset build preview."""
    lines = [
        "# Dataset Build Preview",
        "",
        f"- Spec: `{preview.spec.name}`",
        f"- Description: {preview.spec.description or 'n/a'}",
        f"- Database: `{preview.db_path}`",
        f"- Max examples: {preview.spec.max_examples}",
        f"- Seed: {preview.spec.seed}",
        f"- Requested target count: {preview.spec.requested_target_count()}",
        f"- Found candidates: {preview.found_candidate_count}",
        f"- Eligible candidates: {preview.eligible_candidate_count}",
        f"- Possible selected count: {preview.possible_selected_count}",
        f"- Overall status: {preview.status}",
        "",
        "## Inferred schema roles",
        "",
    ]
    if preview.candidate_pool.inferred_roles:
        lines.extend(f"- {role}: `{table}`" for role, table in sorted(preview.candidate_pool.inferred_roles.items()))
    else:
        lines.append("- No relevant tables inferred.")
    lines.extend(["", "## Missing or unavailable", ""])
    if preview.candidate_pool.missing_concepts:
        lines.extend(f"- Missing concept: {concept}" for concept in preview.candidate_pool.missing_concepts)
    if preview.candidate_pool.unavailable_fields:
        lines.extend(f"- Unavailable field: {field}" for field in preview.candidate_pool.unavailable_fields)
    if not preview.candidate_pool.missing_concepts and not preview.candidate_pool.unavailable_fields:
        lines.append("- None detected.")
    lines.extend(["", "## Target preview", ""])
    if not preview.target_previews:
        lines.append("- No non-zero targets requested.")
    else:
        lines.extend(
            [
                "| Target | Requested | Found candidates | Eligible candidates | Possible selected | Status |",
                "| --- | ---: | ---: | ---: | ---: | --- |",
            ]
        )
        for target in preview.target_previews:
            lines.append(
                f"| {target.target_type}.{target.name} | {target.requested} | {target.found_candidates} | "
                f"{target.eligible_candidates} | {target.possible_selected} | {target.status} |"
            )
    lines.extend(["", "## Candidate notes", ""])
    lines.append(f"- Excluded invalid taxonomy candidates: {preview.candidate_pool.excluded_count}")
    lines.append(
        "- AI problem cases require manual labels before export: "
        f"{preview.spec.ai_problem_cases['require_manual_label_before_export']}"
    )
    return "\n".join(lines)


def missing_concepts(roles: RoleCandidates) -> tuple[str, ...]:
    """Return missing relevant schema concepts."""
    missing = []
    for role_name in (
        "transactions",
        "categories",
        "tags",
        "transaction_tags",
        "category_rules",
        "audit",
    ):
        if getattr(roles, role_name) is None:
            missing.append(role_name)
    return tuple(missing)


def role_names(roles: RoleCandidates) -> dict[str, str]:
    """Return inferred role names for reporting."""
    names = {}
    for role_name in (
        "transactions",
        "categories",
        "tags",
        "transaction_tags",
        "category_rules",
        "accounts",
        "statements",
        "statement_types",
        "audit",
    ):
        table = getattr(roles, role_name)
        if table is not None:
            names[role_name] = table.name
    return names


def normalize_ai_problem_cases(value: Any) -> dict[str, Any]:
    """Normalize the ai_problem_cases spec section."""
    raw = mapping_or_default(value, AI_PROBLEM_DEFAULTS, "ai_problem_cases")
    normalized = dict(AI_PROBLEM_DEFAULTS)
    unknown_keys = sorted(set(raw) - set(AI_PROBLEM_DEFAULTS))
    if unknown_keys:
        raise DatasetSpecError(f"unknown ai_problem_cases field(s): {', '.join(unknown_keys)}")
    for key in (
        "include",
        "include_ai_unknown",
        "include_ai_needs_review",
        "include_low_confidence",
        "include_ai_corrected_later",
        "require_manual_label_before_export",
    ):
        if key in raw:
            normalized[key] = bool_value(raw[key], f"ai_problem_cases.{key}")
    if "max_examples" in raw:
        normalized["max_examples"] = nonnegative_int(raw["max_examples"], "ai_problem_cases.max_examples")
    if "low_confidence_threshold" in raw:
        threshold = numeric_between_zero_and_one(
            raw["low_confidence_threshold"], "ai_problem_cases.low_confidence_threshold"
        )
        normalized["low_confidence_threshold"] = threshold
    return normalized


def normalize_targets(value: Any) -> dict[str, dict[str, int]]:
    """Normalize coverage target counts."""
    raw = mapping_or_default(value, TARGET_DEFAULTS, "targets")
    unknown_sections = sorted(set(raw) - set(TARGET_DEFAULTS))
    if unknown_sections:
        raise DatasetSpecError(f"unknown targets section(s): {', '.join(unknown_sections)}")
    normalized = {section: dict(defaults) for section, defaults in TARGET_DEFAULTS.items()}
    for section, section_value in raw.items():
        if not isinstance(section_value, Mapping):
            raise DatasetSpecError(f"targets.{section} must be an object")
        if section in {"categories", "tags"}:
            normalized[section] = normalize_counts(section_value, f"targets.{section}")
            continue
        unknown_keys = sorted(set(section_value) - set(TARGET_DEFAULTS[section]))
        if unknown_keys:
            raise DatasetSpecError(f"unknown targets.{section} value(s): {', '.join(unknown_keys)}")
        normalized[section].update(normalize_counts(section_value, f"targets.{section}"))
    return normalized


def normalize_selection(value: Any) -> dict[str, Any]:
    """Normalize the selection spec section."""
    raw = mapping_or_default(value, SELECTION_DEFAULTS, "selection")
    unknown_keys = sorted(set(raw) - set(SELECTION_DEFAULTS))
    if unknown_keys:
        raise DatasetSpecError(f"unknown selection field(s): {', '.join(unknown_keys)}")
    normalized = dict(SELECTION_DEFAULTS)
    if "max_per_near_duplicate_group" in raw:
        normalized["max_per_near_duplicate_group"] = positive_int(
            raw["max_per_near_duplicate_group"], "selection.max_per_near_duplicate_group"
        )
    for key in (
        "prefer_recent_manual_labels",
        "include_full_taxonomy",
        "write_labeling_queue",
        "write_adjudication_queue",
    ):
        if key in raw:
            normalized[key] = bool_value(raw[key], f"selection.{key}")
    return normalized


def normalize_string_options(
    raw: Mapping[str, Any],
    defaults: Mapping[str, str],
    allowed_values: set[str],
    label: str,
) -> dict[str, str]:
    """Normalize a string-option mapping."""
    unknown_keys = sorted(set(raw) - set(defaults))
    if unknown_keys:
        raise DatasetSpecError(f"unknown {label} value(s): {', '.join(unknown_keys)}")
    normalized = dict(defaults)
    for key, value in raw.items():
        if not isinstance(value, str) or value not in allowed_values:
            raise DatasetSpecError(f"{label}.{key} must be one of: {', '.join(sorted(allowed_values))}")
        normalized[key] = value
    return normalized


def normalize_counts(raw: Mapping[str, Any], label: str) -> dict[str, int]:
    """Normalize a mapping of non-negative target counts."""
    counts = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not key:
            raise DatasetSpecError(f"{label} keys must be non-empty strings")
        counts[key] = nonnegative_int(value, f"{label}.{key}")
    return counts


def mapping_or_default(value: Any, defaults: Mapping[str, Any], label: str) -> Mapping[str, Any]:
    """Return a mapping value or defaults for a missing section."""
    if value is None:
        return defaults
    if not isinstance(value, Mapping):
        raise DatasetSpecError(f"{label} must be an object")
    return value


def required_string(payload: Mapping[str, Any], key: str) -> str:
    """Return a required non-empty string field."""
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise DatasetSpecError(f"{key} is required and must be a non-empty string")
    return value.strip()


def optional_string(payload: Mapping[str, Any], key: str, default: str) -> str:
    """Return an optional string field."""
    value = payload.get(key, default)
    if not isinstance(value, str):
        raise DatasetSpecError(f"{key} must be a string")
    return value.strip()


def bool_value(value: Any, label: str) -> bool:
    """Return a strict boolean value."""
    if not isinstance(value, bool):
        raise DatasetSpecError(f"{label} must be a boolean")
    return value


def int_value(value: Any, label: str) -> int:
    """Return a strict integer value, excluding booleans."""
    if not isinstance(value, int) or isinstance(value, bool):
        raise DatasetSpecError(f"{label} must be an integer")
    return value


def positive_int(value: Any, label: str) -> int:
    """Return a strict positive integer."""
    parsed = int_value(value, label)
    if parsed <= 0:
        raise DatasetSpecError(f"{label} must be positive")
    return parsed


def nonnegative_int(value: Any, label: str) -> int:
    """Return a strict non-negative integer."""
    parsed = int_value(value, label)
    if parsed < 0:
        raise DatasetSpecError(f"{label} must be non-negative")
    return parsed


def numeric_between_zero_and_one(value: Any, label: str) -> float:
    """Return a numeric value between 0 and 1."""
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise DatasetSpecError(f"{label} must be numeric")
    parsed = float(value)
    if parsed < 0.0 or parsed > 1.0:
        raise DatasetSpecError(f"{label} must be between 0 and 1")
    return parsed


def choose_column(table: TableInfo, names: Sequence[str]) -> str | None:
    """Choose the first matching column by case-insensitive name."""
    by_lower = {column.name.lower(): column.name for column in table.columns}
    for name in names:
        if name.lower() in by_lower:
            return by_lower[name.lower()]
    return None


def row_value(row: sqlite3.Row, column_name: str | None) -> Any:
    """Return a row value for an optional column."""
    if column_name is None:
        return None
    return row[column_name]


def stable_text(value: Any) -> str | None:
    """Return a stable stripped string or None."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def numeric_or_none(value: Any) -> float | None:
    """Return a numeric float value or None."""
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def boolish(value: Any) -> bool | None:
    """Return a bool for common database boolean representations."""
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "y", "review", "reviewed"}:
        return True
    if normalized in {"0", "false", "no", "n", ""}:
        return False
    return None


def resolve_category(
    value: Any,
    categories: Mapping[str, Mapping[str, str]],
) -> tuple[str | None, str | None]:
    """Resolve a category database value to candidate taxonomy ID and name."""
    text = stable_text(value)
    if not text:
        return None, None
    if text in categories:
        return text, categories[text]["name"]
    for item_id, item in categories.items():
        if item["name"] == text:
            return item_id, item["name"]
    return text, text


def invalid_taxonomy_reason(
    category_id: str | None,
    category_name: str | None,
    tag_ids: Sequence[str],
    categories: Mapping[str, Mapping[str, str]],
    tags: Mapping[str, Mapping[str, str]],
) -> str | None:
    """Return an invalid taxonomy reason, or None."""
    if category_id is not None and not categories:
        return "category taxonomy is unavailable"
    if categories and category_id is not None and category_id not in categories:
        return "category is not present in candidate taxonomy"
    invalid_tags = [tag_id for tag_id in tag_ids if tags and tag_id not in tags]
    if invalid_tags:
        return "tag is not present in candidate taxonomy"
    return None


def is_reviewed(row: sqlite3.Row, columns: Mapping[str, str | None]) -> bool:
    """Return whether a row appears explicitly reviewed."""
    reviewed_at = stable_text(row_value(row, columns.get("reviewed_at")))
    reviewed = boolish(row_value(row, columns.get("reviewed")))
    return bool(reviewed_at) or reviewed is True


def normalized_source(value: Any) -> str:
    """Normalize a stored categorization source value."""
    text = (stable_text(value) or "").lower().replace("-", "_").replace(" ", "_")
    if text in {"manual", "manual_edit", "user", "edited"}:
        return "manual"
    if text in {"rule", "rules", "deterministic_rule"}:
        return "rule"
    if text in {"history", "stable_history", "merchant_history"}:
        return "history"
    if text in {"ai", "llm", "openai", "model"}:
        return "ai"
    if text in {"reviewed", "verified"}:
        return "reviewed"
    return text or "unknown"


def label_source_for_candidate(
    source: str,
    category_name: str | None,
    reviewed: bool,
    needs_review: bool | None,
    confidence: float | None,
    stable_count: int,
) -> str:
    """Return the normalized dataset label source for one candidate."""
    if category_name == "UNKNOWN" or category_name is None:
        return "unknown"
    if source == "manual":
        return "manual_edit"
    if reviewed or source == "reviewed":
        return "reviewed"
    if source == "rule" and (confidence or 0.0) >= HIGH_CONFIDENCE_RULE_THRESHOLD and needs_review is not True:
        return "high_confidence_rule"
    if source == "history" and stable_count >= STABLE_HISTORY_MIN_COUNT:
        return "stable_history"
    if source == "ai":
        return "ai"
    return "unknown"


def ambiguity_type_for_candidate(
    *,
    description: str | None,
    merchant: str | None,
    category_name: str | None,
    tag_names: Sequence[str],
    source: str,
    needs_review: bool | None,
    confidence: float | None,
    spec: DatasetBuildSpec,
    ai_observation: Mapping[str, Any] | None,
) -> str:
    """Infer the coverage ambiguity type for one candidate."""
    lower_tags = {tag.lower() for tag in tag_names}
    text = " ".join(value for value in (description, merchant, category_name, " ".join(tag_names)) if value)
    lower_category = (category_name or "").lower()
    cues = set(classification_cues(text))
    if ai_observation is not None:
        return str(ai_observation["failure_type"])
    if category_name == "UNKNOWN":
        return "unknown_correct"
    if "reimbursable" in lower_tags:
        return "reimbursable_like"
    if "tax" in lower_tags or "tax" in cues or "tax" in lower_category:
        return "tax_like"
    if "reimbursement" in cues or "reimbursement" in lower_category:
        return "reimbursement_like"
    if "rental" in cues or "rental" in lower_category:
        return "rental_like"
    if "transfer" in cues or "transfer" in lower_category:
        return "transfer_like"
    if "salary" in cues or "income" in lower_category:
        return "income_like"
    if has_noisy_text(text):
        return "noisy_description"
    return "straightforward" if cues else "ambiguous_merchant"


def adjudication_reason_for_candidate(
    *,
    label_source: str,
    source: str,
    needs_review: bool | None,
    reviewed: bool,
    confidence: float | None,
    stable_count: int,
    spec: DatasetBuildSpec,
    ai_observation: Mapping[str, Any] | None,
) -> str | None:
    """Return why a candidate should not be trusted without manual adjudication."""
    if (
        ai_observation is not None
        and spec.ai_problem_cases["require_manual_label_before_export"]
        and not (
            ai_observation.get("failure_type") == "ai_corrected_later" and label_source in {"manual_edit", "reviewed"}
        )
    ):
        return "AI problem candidate requires manual label before export"
    if label_source == "ai" and spec.label_sources.get("ai") == "candidate_only":
        return "AI-only category assignment without explicit review"
    if source == "ai" and spec.ai_problem_cases["require_manual_label_before_export"]:
        return "AI problem candidate requires manual label before export"
    if needs_review is True and not reviewed and label_source not in {"manual_edit", "unresolved"}:
        return "transaction is marked as needing review"
    if source == "history" and stable_count < STABLE_HISTORY_MIN_COUNT:
        return "historical label is not stable enough"
    if (
        confidence is not None
        and confidence < LOW_CONFIDENCE_DEFAULT
        and not reviewed
        and label_source != "manual_edit"
    ):
        return "stored confidence is low"
    return None


def lookup_or_direct(
    row: sqlite3.Row,
    id_column: str | None,
    direct_column: str | None,
    lookup: Mapping[str, str],
) -> str | None:
    """Return a lookup label or direct text for an optional relationship."""
    identifier = stable_text(row_value(row, id_column))
    if identifier and identifier in lookup:
        return lookup[identifier]
    return stable_text(row_value(row, direct_column))


def statement_type_for_row(
    row: sqlite3.Row,
    columns: Mapping[str, str | None],
    statement_type_lookup: Mapping[str, str],
    statement_to_type: Mapping[str, str],
) -> str | None:
    """Return a statement type label from direct or relationship columns."""
    direct = stable_text(row_value(row, columns.get("statement_type")))
    if direct:
        return direct
    type_id = stable_text(row_value(row, columns.get("statement_type_id")))
    if type_id and type_id in statement_type_lookup:
        return statement_type_lookup[type_id]
    statement_id = stable_text(row_value(row, columns.get("statement_id")))
    if statement_id and (bridged_type_id := statement_to_type.get(statement_id)):
        return statement_type_lookup.get(bridged_type_id)
    return None


def near_duplicate_key_for_candidate(
    description: str | None,
    merchant: str | None,
    amount: float | None,
    category_id: str | None,
    tag_ids: Sequence[str],
) -> str:
    """Return an approximate near-duplicate grouping key."""
    text = re.sub(r"[^a-z0-9]+", " ", (merchant or description or "").lower()).strip()
    rounded_amount = "unknown" if amount is None else f"{amount:.2f}"
    return f"{text}|{rounded_amount}|{category_id or 'unknown'}|{','.join(sorted(tag_ids))}"
