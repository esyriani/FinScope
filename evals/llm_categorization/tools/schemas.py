"""Typed structures and validators for offline LLM categorization evals.

The schema mirrors the prompt-evaluation boundary rather than the production
database schema. It validates curated JSONL examples, candidate taxonomy IDs,
model-like outputs, and future scoring records without importing FinScope
runtime modules or opening runtime databases.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from enum import Enum
from math import isfinite
from typing import Any

ALLOWED_AMBIGUITY_TYPES = frozenset(
    {
        "straightforward",
        "ambiguous_merchant",
        "noisy_description",
        "transfer_like",
        "reimbursement_like",
        "reimbursable_like",
        "rental_like",
        "tax_like",
        "income_like",
        "misleading_history",
        "weak_history",
        "unknown_correct",
        "other",
    }
)
ALLOWED_CONFIDENCE_BANDS = frozenset({"low", "medium", "high", "unknown"})
ALLOWED_DIRECTIONS = frozenset({"debit", "credit", "zero", "unknown"})
ALLOWED_EVIDENCE_TYPES = frozenset({"manual", "rule", "history", "ai", "unknown"})
ALLOWED_LABEL_SOURCES = frozenset(
    {
        "manual_edit",
        "reviewed",
        "high_confidence_rule",
        "stable_history",
        "curated_by_researcher",
        "synthetic",
        "unknown",
    }
)
ALLOWED_PRIVACY_LEVELS = frozenset({"raw_real", "redacted_real", "synthetic"})
BUILTIN_CONCEPT_NAMES = frozenset({"UNKNOWN", "Transfers", "Reimbursement", "Income", "Rental", "Reimbursable"})
TOP_LEVEL_FIELDS = frozenset(
    {
        "request_id",
        "transaction",
        "candidate_taxonomy",
        "similar_transactions",
        "expected",
        "label_source",
        "privacy_level",
        "coverage",
        "notes",
    }
)
TRANSACTION_FIELDS = frozenset({"description", "merchant", "amount", "date", "account", "statement_type"})
TAXONOMY_FIELDS = frozenset({"categories", "tags"})
TAXONOMY_ITEM_FIELDS = frozenset({"id", "name", "description", "instruction"})
SIMILAR_TRANSACTION_REQUIRED_FIELDS = frozenset({"description", "amount", "category_id", "tag_ids", "evidence_type"})
SIMILAR_TRANSACTION_FIELDS = SIMILAR_TRANSACTION_REQUIRED_FIELDS | {"confidence"}
EXPECTED_FIELDS = frozenset({"category_id", "tag_ids", "needs_review"})
COVERAGE_FIELDS = frozenset({"category", "tags", "direction", "statement_type", "confidence_band", "ambiguity_type"})
UNKNOWN_CATEGORY_NAME = "UNKNOWN"


class DatasetValidationError(ValueError):
    """Represent an invalid curated evaluation example."""

    def __init__(self, message: str, *, line_number: int | None = None, request_id: str | None = None) -> None:
        """Build a validation error with optional line and request context."""
        self.line_number = line_number
        self.request_id = request_id
        self.message = message
        context = []
        if line_number is not None:
            context.append(f"line {line_number}")
        if request_id:
            context.append(f"request {request_id}")
        prefix = f"{', '.join(context)}: " if context else ""
        super().__init__(f"{prefix}{message}")


class FailureMode(str, Enum):
    """Controlled failure labels for future scoring output."""

    INVALID_JSON = "invalid_json"
    INVALID_SCHEMA = "invalid_schema"
    SCHEMA_INVALID = "schema_invalid"
    INVALID_CATEGORY_ID = "invalid_category_id"
    INVALID_TAG_ID = "invalid_tag_id"
    WRONG_CATEGORY = "wrong_category"
    WRONG_TAGS = "wrong_tags"
    MISSING_TAG = "missing_tag"
    EXTRA_TAG = "extra_tag"
    FALSE_UNKNOWN = "false_unknown"
    MISSED_UNKNOWN = "missed_unknown"
    UNSAFE_AUTO_ASSIGNMENT = "unsafe_auto_assignment"
    HIGH_CONFIDENCE_WRONG = "high_confidence_wrong"
    OVER_REVIEW = "over_review"
    UNDER_REVIEW = "under_review"
    IGNORED_SIMILAR_HISTORY = "ignored_similar_history"
    OVERUSED_SIMILAR_HISTORY = "overused_similar_history"
    DIRECTION_ERROR = "direction_error"
    TRANSFER_INCOME_CONFUSION = "transfer_income_confusion"
    REIMBURSEMENT_CONFUSION = "reimbursement_confusion"
    RENTAL_HOUSING_CONFUSION = "rental_housing_confusion"
    TAX_OVER_TAGGING = "tax_over_tagging"
    POOR_CONFIDENCE_CALIBRATION = "poor_confidence_calibration"
    NEEDS_REVIEW_MISMATCH = "needs_review_mismatch"
    TAXONOMY_GAP = "taxonomy_gap"
    RULE_GAP = "rule_gap"
    DATA_QUALITY_ISSUE = "data_quality_issue"


@dataclass(frozen=True)
class CandidateTaxonomyItem:
    """Represent one category or tag option exposed to a prompt candidate."""

    id: str
    name: str
    description: str
    instruction: str | None


@dataclass(frozen=True)
class CandidateTaxonomy:
    """Represent candidate category and tag IDs available to an example."""

    categories: tuple[CandidateTaxonomyItem, ...]
    tags: tuple[CandidateTaxonomyItem, ...]

    def category_ids(self) -> set[str]:
        """Return valid candidate category IDs."""
        return {item.id for item in self.categories}

    def tag_ids(self) -> set[str]:
        """Return valid candidate tag IDs."""
        return {item.id for item in self.tags}

    def category_name(self, category_id: str) -> str | None:
        """Return the category name for an ID, if present."""
        return _item_name(self.categories, category_id)

    def tag_name(self, tag_id: str) -> str | None:
        """Return the tag name for an ID, if present."""
        return _item_name(self.tags, tag_id)

    def has_unknown_category(self) -> bool:
        """Return whether the candidate category set contains UNKNOWN."""
        return any(item.name == UNKNOWN_CATEGORY_NAME for item in self.categories)


@dataclass(frozen=True)
class TransactionInput:
    """Represent the transaction input for one eval example."""

    description: str
    merchant: str | None
    amount: float
    date: str | None
    account: str | None
    statement_type: str | None


@dataclass(frozen=True)
class SimilarTransactionEvidence:
    """Represent local historical evidence supplied to a prompt candidate."""

    description: str
    amount: float
    category_id: str
    tag_ids: tuple[str, ...]
    evidence_type: str
    confidence: float | None


@dataclass(frozen=True)
class ExpectedLabel:
    """Represent the curated taxonomy label for one evaluation example."""

    category_id: str
    tag_ids: tuple[str, ...]
    needs_review: bool


@dataclass(frozen=True)
class CoverageMetadata:
    """Represent methodology coverage labels for one evaluation example."""

    category: str | None
    tags: tuple[str, ...]
    direction: str
    statement_type: str | None
    confidence_band: str
    ambiguity_type: str | None


@dataclass(frozen=True)
class EvaluationExample:
    """Represent one curated JSONL example used for prompt evaluation."""

    request_id: str
    transaction: TransactionInput
    candidate_taxonomy: CandidateTaxonomy
    similar_transactions: tuple[SimilarTransactionEvidence, ...]
    expected: ExpectedLabel
    label_source: str
    privacy_level: str
    coverage: CoverageMetadata
    notes: str
    line_number: int | None = None


@dataclass(frozen=True)
class ModelOutput:
    """Represent one structured categorization output from a prompt candidate."""

    request_id: str
    category_id: str
    tag_ids: tuple[str, ...]
    needs_review: bool
    confidence: float
    supported_by_similar_transactions: bool
    reason: str


@dataclass(frozen=True)
class ScoringOutput:
    """Represent per-example scoring details for a model output."""

    request_id: str
    valid_json: bool
    schema_valid: bool
    valid_taxonomy_ids: bool
    category_correct: bool | None
    exact_taxonomy_match: bool | None
    unsafe_auto_assignment: bool
    high_confidence_wrong: bool
    failure_modes: tuple[FailureMode, ...]


@dataclass(frozen=True)
class AggregateMetrics:
    """Represent aggregate prompt-candidate metrics for future reports."""

    example_count: int
    valid_json_rate: float
    schema_valid_rate: float
    valid_taxonomy_id_rate: float
    category_accuracy: float | None
    known_category_accuracy: float | None
    exact_taxonomy_match_rate: float | None
    tag_micro_precision: float | None
    tag_micro_recall: float | None
    tag_micro_f1: float | None
    tag_macro_precision: float | None
    tag_macro_recall: float | None
    tag_macro_f1: float | None
    unknown_precision: float | None
    unknown_recall: float | None
    false_unknown_rate: float | None
    missed_unknown_rate: float | None
    needs_review_precision: float | None
    needs_review_recall: float | None
    needs_review_f1: float | None
    unsafe_auto_assignment_rate: float | None
    high_confidence_wrong_rate: float | None
    confidence_calibration_by_band: Mapping[str, float]
    failure_mode_counts: Mapping[str, int]


def validate_evaluation_example(payload: Mapping[str, Any], *, line_number: int | None = None) -> EvaluationExample:
    """Validate and normalize one JSONL evaluation example."""
    _reject_unknown_fields(payload, TOP_LEVEL_FIELDS, "example", line_number=line_number, request_id=None)
    request_id = _required_non_empty_str(payload, "request_id", line_number=line_number, request_id=None)
    candidate_taxonomy = validate_candidate_taxonomy(
        _required_mapping(payload, "candidate_taxonomy", line_number=line_number, request_id=request_id),
        line_number=line_number,
        request_id=request_id,
    )
    transaction = validate_transaction_input(
        _required_mapping(payload, "transaction", line_number=line_number, request_id=request_id),
        line_number=line_number,
        request_id=request_id,
    )
    similar_transactions = _validate_similar_transaction_evidence_list(
        _required_list(payload, "similar_transactions", line_number=line_number, request_id=request_id),
        candidate_taxonomy,
        line_number=line_number,
        request_id=request_id,
    )
    expected = validate_expected_label(
        _required_mapping(payload, "expected", line_number=line_number, request_id=request_id),
        candidate_taxonomy,
        line_number=line_number,
        request_id=request_id,
    )
    coverage = validate_coverage_metadata(
        _required_mapping(payload, "coverage", line_number=line_number, request_id=request_id),
        transaction,
        line_number=line_number,
        request_id=request_id,
    )
    label_source = _required_enum(
        payload, "label_source", ALLOWED_LABEL_SOURCES, line_number=line_number, request_id=request_id
    )
    privacy_level = _required_enum(
        payload, "privacy_level", ALLOWED_PRIVACY_LEVELS, line_number=line_number, request_id=request_id
    )
    notes = _required_str(payload, "notes", line_number=line_number, request_id=request_id, allow_empty=True)
    return EvaluationExample(
        request_id=request_id,
        transaction=transaction,
        candidate_taxonomy=candidate_taxonomy,
        similar_transactions=similar_transactions,
        expected=expected,
        label_source=label_source,
        privacy_level=privacy_level,
        coverage=coverage,
        notes=notes,
        line_number=line_number,
    )


def validate_transaction_input(
    payload: Mapping[str, Any], *, line_number: int | None = None, request_id: str | None = None
) -> TransactionInput:
    """Validate the transaction input block for one evaluation example."""
    _reject_unknown_fields(payload, TRANSACTION_FIELDS, "transaction", line_number=line_number, request_id=request_id)
    return TransactionInput(
        description=_required_non_empty_str(payload, "description", line_number=line_number, request_id=request_id),
        merchant=_required_nullable_str(payload, "merchant", line_number=line_number, request_id=request_id),
        amount=_required_number(payload, "amount", line_number=line_number, request_id=request_id),
        date=_required_nullable_date(payload, "date", line_number=line_number, request_id=request_id),
        account=_required_nullable_str(payload, "account", line_number=line_number, request_id=request_id),
        statement_type=_required_nullable_str(
            payload, "statement_type", line_number=line_number, request_id=request_id
        ),
    )


def validate_candidate_taxonomy(
    payload: Mapping[str, Any], *, line_number: int | None = None, request_id: str | None = None
) -> CandidateTaxonomy:
    """Validate candidate category and tag options for one example."""
    _reject_unknown_fields(
        payload, TAXONOMY_FIELDS, "candidate_taxonomy", line_number=line_number, request_id=request_id
    )
    categories = _validate_taxonomy_items(
        _required_list(payload, "categories", line_number=line_number, request_id=request_id),
        field_name="candidate_taxonomy.categories",
        allow_empty=False,
        line_number=line_number,
        request_id=request_id,
    )
    tags = _validate_taxonomy_items(
        _required_list(payload, "tags", line_number=line_number, request_id=request_id),
        field_name="candidate_taxonomy.tags",
        allow_empty=True,
        line_number=line_number,
        request_id=request_id,
    )
    return CandidateTaxonomy(categories=categories, tags=tags)


def validate_similar_transaction_evidence(
    payload: Mapping[str, Any],
    candidate_taxonomy: CandidateTaxonomy,
    *,
    line_number: int | None = None,
    request_id: str | None = None,
) -> SimilarTransactionEvidence:
    """Validate one similar-transaction evidence block."""
    _reject_unknown_fields(
        payload,
        SIMILAR_TRANSACTION_FIELDS,
        "similar_transactions[]",
        line_number=line_number,
        request_id=request_id,
    )
    for field_name in SIMILAR_TRANSACTION_REQUIRED_FIELDS:
        _require_present(payload, field_name, line_number=line_number, request_id=request_id)
    category_id = _required_non_empty_str(payload, "category_id", line_number=line_number, request_id=request_id)
    if category_id not in candidate_taxonomy.category_ids():
        raise DatasetValidationError(
            f"similar transaction category_id {category_id!r} is not in candidate taxonomy",
            line_number=line_number,
            request_id=request_id,
        )
    tag_ids = _str_sequence(
        payload.get("tag_ids"),
        field_name="similar_transactions[].tag_ids",
        line_number=line_number,
        request_id=request_id,
    )
    _require_known_tag_ids(tag_ids, candidate_taxonomy, line_number=line_number, request_id=request_id)
    return SimilarTransactionEvidence(
        description=_required_non_empty_str(payload, "description", line_number=line_number, request_id=request_id),
        amount=_required_number(payload, "amount", line_number=line_number, request_id=request_id),
        category_id=category_id,
        tag_ids=tag_ids,
        evidence_type=_required_enum(
            payload, "evidence_type", ALLOWED_EVIDENCE_TYPES, line_number=line_number, request_id=request_id
        ),
        confidence=_optional_probability(payload, "confidence", line_number=line_number, request_id=request_id),
    )


def validate_expected_label(
    payload: Mapping[str, Any],
    candidate_taxonomy: CandidateTaxonomy,
    *,
    line_number: int | None = None,
    request_id: str | None = None,
) -> ExpectedLabel:
    """Validate the curated expected label for one evaluation example."""
    _reject_unknown_fields(payload, EXPECTED_FIELDS, "expected", line_number=line_number, request_id=request_id)
    category_id = _required_non_empty_str(payload, "category_id", line_number=line_number, request_id=request_id)
    if category_id not in candidate_taxonomy.category_ids():
        raise DatasetValidationError(
            f"expected.category_id {category_id!r} is not in candidate taxonomy",
            line_number=line_number,
            request_id=request_id,
        )
    category_name = candidate_taxonomy.category_name(category_id)
    if category_name == UNKNOWN_CATEGORY_NAME and not candidate_taxonomy.has_unknown_category():
        raise DatasetValidationError(
            "expected.category_id references UNKNOWN but UNKNOWN is absent from candidate taxonomy",
            line_number=line_number,
            request_id=request_id,
        )
    tag_ids = _str_sequence(
        payload.get("tag_ids"), field_name="expected.tag_ids", line_number=line_number, request_id=request_id
    )
    _require_known_tag_ids(tag_ids, candidate_taxonomy, line_number=line_number, request_id=request_id)
    return ExpectedLabel(
        category_id=category_id,
        tag_ids=tag_ids,
        needs_review=_required_bool(payload, "needs_review", line_number=line_number, request_id=request_id),
    )


def validate_coverage_metadata(
    payload: Mapping[str, Any],
    transaction: TransactionInput,
    *,
    line_number: int | None = None,
    request_id: str | None = None,
) -> CoverageMetadata:
    """Validate methodology coverage metadata for one evaluation example."""
    _reject_unknown_fields(payload, COVERAGE_FIELDS, "coverage", line_number=line_number, request_id=request_id)
    direction = _required_enum(payload, "direction", ALLOWED_DIRECTIONS, line_number=line_number, request_id=request_id)
    expected_direction = direction_from_amount(transaction.amount)
    if direction != expected_direction:
        raise DatasetValidationError(
            f"coverage.direction {direction!r} is inconsistent with signed amount; expected {expected_direction!r}",
            line_number=line_number,
            request_id=request_id,
        )
    ambiguity_type = _required_nullable_enum(
        payload,
        "ambiguity_type",
        ALLOWED_AMBIGUITY_TYPES,
        line_number=line_number,
        request_id=request_id,
    )
    return CoverageMetadata(
        category=_required_nullable_str(payload, "category", line_number=line_number, request_id=request_id),
        tags=_str_sequence(
            payload.get("tags"), field_name="coverage.tags", line_number=line_number, request_id=request_id
        ),
        direction=direction,
        statement_type=_required_nullable_str(
            payload, "statement_type", line_number=line_number, request_id=request_id
        ),
        confidence_band=_required_enum(
            payload, "confidence_band", ALLOWED_CONFIDENCE_BANDS, line_number=line_number, request_id=request_id
        ),
        ambiguity_type=ambiguity_type,
    )


def validate_model_output(
    payload: Mapping[str, Any],
    candidate_taxonomy: CandidateTaxonomy | None = None,
    *,
    line_number: int | None = None,
    request_id: str | None = None,
) -> ModelOutput:
    """Validate one structured model output without scoring it."""
    category_id = _required_non_empty_str(payload, "category_id", line_number=line_number, request_id=request_id)
    tag_ids = _str_sequence(
        payload.get("tag_ids"), field_name="tag_ids", line_number=line_number, request_id=request_id
    )
    if candidate_taxonomy is not None:
        if category_id not in candidate_taxonomy.category_ids():
            raise DatasetValidationError(
                f"model category_id {category_id!r} is not in candidate taxonomy",
                line_number=line_number,
                request_id=request_id,
            )
        _require_known_tag_ids(tag_ids, candidate_taxonomy, line_number=line_number, request_id=request_id)
    return ModelOutput(
        request_id=_required_non_empty_str(payload, "request_id", line_number=line_number, request_id=request_id),
        category_id=category_id,
        tag_ids=tag_ids,
        needs_review=_required_bool(payload, "needs_review", line_number=line_number, request_id=request_id),
        confidence=_required_probability(payload, "confidence", line_number=line_number, request_id=request_id),
        supported_by_similar_transactions=_required_bool(
            payload, "supported_by_similar_transactions", line_number=line_number, request_id=request_id
        ),
        reason=_required_non_empty_str(payload, "reason", line_number=line_number, request_id=request_id),
    )


def direction_from_amount(amount: float) -> str:
    """Return the expected coverage direction from a signed transaction amount."""
    if amount > 0:
        return "debit"
    if amount < 0:
        return "credit"
    return "zero"


def _validate_similar_transaction_evidence_list(
    value: object,
    candidate_taxonomy: CandidateTaxonomy,
    *,
    line_number: int | None,
    request_id: str | None,
) -> tuple[SimilarTransactionEvidence, ...]:
    """Validate the list of similar-transaction evidence objects."""
    if not _is_sequence(value):
        raise DatasetValidationError(
            "similar_transactions must be a list",
            line_number=line_number,
            request_id=request_id,
        )
    evidence = []
    for item in value:
        if not isinstance(item, Mapping):
            raise DatasetValidationError(
                "similar_transactions entries must be objects",
                line_number=line_number,
                request_id=request_id,
            )
        evidence.append(
            validate_similar_transaction_evidence(
                item,
                candidate_taxonomy,
                line_number=line_number,
                request_id=request_id,
            )
        )
    return tuple(evidence)


def _validate_taxonomy_items(
    value: object,
    *,
    field_name: str,
    allow_empty: bool,
    line_number: int | None,
    request_id: str | None,
) -> tuple[CandidateTaxonomyItem, ...]:
    """Validate a category or tag candidate list."""
    if not _is_sequence(value):
        raise DatasetValidationError(f"{field_name} must be a list", line_number=line_number, request_id=request_id)
    if not value and not allow_empty:
        raise DatasetValidationError(f"{field_name} must not be empty", line_number=line_number, request_id=request_id)

    items = []
    ids: set[str] = set()
    names: set[str] = set()
    for raw_item in value:
        if not isinstance(raw_item, Mapping):
            raise DatasetValidationError(
                f"{field_name} entries must be objects",
                line_number=line_number,
                request_id=request_id,
            )
        _reject_unknown_fields(
            raw_item, TAXONOMY_ITEM_FIELDS, f"{field_name}[]", line_number=line_number, request_id=request_id
        )
        item = CandidateTaxonomyItem(
            id=_required_non_empty_str(raw_item, "id", line_number=line_number, request_id=request_id),
            name=_required_non_empty_str(raw_item, "name", line_number=line_number, request_id=request_id),
            description=_required_str(
                raw_item, "description", line_number=line_number, request_id=request_id, allow_empty=True
            ),
            instruction=_required_nullable_str(raw_item, "instruction", line_number=line_number, request_id=request_id),
        )
        if item.id in ids:
            raise DatasetValidationError(
                f"{field_name} contains duplicate id {item.id!r}",
                line_number=line_number,
                request_id=request_id,
            )
        if item.name in names:
            raise DatasetValidationError(
                f"{field_name} contains duplicate name {item.name!r}",
                line_number=line_number,
                request_id=request_id,
            )
        ids.add(item.id)
        names.add(item.name)
        items.append(item)
    return tuple(items)


def _require_known_tag_ids(
    tag_ids: Sequence[str],
    candidate_taxonomy: CandidateTaxonomy,
    *,
    line_number: int | None,
    request_id: str | None,
) -> None:
    """Raise if any tag ID is absent from the candidate taxonomy."""
    known_tag_ids = candidate_taxonomy.tag_ids()
    invalid_tag_ids = [tag_id for tag_id in tag_ids if tag_id not in known_tag_ids]
    if invalid_tag_ids:
        raise DatasetValidationError(
            f"tag IDs are not in candidate taxonomy: {invalid_tag_ids}",
            line_number=line_number,
            request_id=request_id,
        )


def _item_name(items: Sequence[CandidateTaxonomyItem], item_id: str) -> str | None:
    """Return the candidate item name for an ID, if present."""
    for item in items:
        if item.id == item_id:
            return item.name
    return None


def _reject_unknown_fields(
    payload: Mapping[str, Any],
    allowed_fields: frozenset[str],
    context: str,
    *,
    line_number: int | None,
    request_id: str | None,
) -> None:
    """Raise when a payload contains keys outside the declared schema."""
    unknown_fields = sorted(set(payload) - allowed_fields)
    if unknown_fields:
        raise DatasetValidationError(
            f"{context} contains unknown field(s): {', '.join(unknown_fields)}",
            line_number=line_number,
            request_id=request_id,
        )


def _require_present(
    payload: Mapping[str, Any], field_name: str, *, line_number: int | None, request_id: str | None
) -> None:
    """Raise when a required field is absent."""
    if field_name not in payload:
        raise DatasetValidationError(
            f"{field_name} is required",
            line_number=line_number,
            request_id=request_id,
        )


def _required_mapping(
    payload: Mapping[str, Any], field_name: str, *, line_number: int | None, request_id: str | None
) -> Mapping[str, Any]:
    """Return a required object field from a JSON payload."""
    _require_present(payload, field_name, line_number=line_number, request_id=request_id)
    value = payload.get(field_name)
    if not isinstance(value, Mapping):
        raise DatasetValidationError(
            f"{field_name} must be an object",
            line_number=line_number,
            request_id=request_id,
        )
    return value


def _required_list(
    payload: Mapping[str, Any], field_name: str, *, line_number: int | None, request_id: str | None
) -> Sequence[Any]:
    """Return a required JSON list field."""
    _require_present(payload, field_name, line_number=line_number, request_id=request_id)
    value = payload.get(field_name)
    if not _is_sequence(value):
        raise DatasetValidationError(f"{field_name} must be a list", line_number=line_number, request_id=request_id)
    return value


def _required_non_empty_str(
    payload: Mapping[str, Any], field_name: str, *, line_number: int | None, request_id: str | None = None
) -> str:
    """Return a required non-empty string field."""
    value = _required_str(payload, field_name, line_number=line_number, request_id=request_id, allow_empty=False)
    return value.strip()


def _required_str(
    payload: Mapping[str, Any],
    field_name: str,
    *,
    line_number: int | None,
    request_id: str | None = None,
    allow_empty: bool,
) -> str:
    """Return a required string field."""
    _require_present(payload, field_name, line_number=line_number, request_id=request_id)
    value = payload.get(field_name)
    if not isinstance(value, str):
        raise DatasetValidationError(f"{field_name} must be a string", line_number=line_number, request_id=request_id)
    if not allow_empty and not value.strip():
        raise DatasetValidationError(
            f"{field_name} must be a non-empty string",
            line_number=line_number,
            request_id=request_id,
        )
    return value


def _required_nullable_str(
    payload: Mapping[str, Any], field_name: str, *, line_number: int | None, request_id: str | None = None
) -> str | None:
    """Return a required nullable string field."""
    _require_present(payload, field_name, line_number=line_number, request_id=request_id)
    value = payload.get(field_name)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise DatasetValidationError(
            f"{field_name} must be null, 'unknown', or a non-empty string",
            line_number=line_number,
            request_id=request_id,
        )
    return value.strip()


def _required_nullable_date(
    payload: Mapping[str, Any], field_name: str, *, line_number: int | None, request_id: str | None = None
) -> str | None:
    """Return a required nullable YYYY-MM-DD date field."""
    value = _required_nullable_str(payload, field_name, line_number=line_number, request_id=request_id)
    if value in (None, "unknown"):
        return value
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise DatasetValidationError(
            f"{field_name} must be YYYY-MM-DD, null, or 'unknown'",
            line_number=line_number,
            request_id=request_id,
        ) from exc
    return parsed.isoformat()


def _required_enum(
    payload: Mapping[str, Any],
    field_name: str,
    allowed_values: frozenset[str],
    *,
    line_number: int | None,
    request_id: str | None,
) -> str:
    """Return a required controlled string field."""
    value = _required_non_empty_str(payload, field_name, line_number=line_number, request_id=request_id)
    if value not in allowed_values:
        raise DatasetValidationError(
            f"{field_name} must be one of {sorted(allowed_values)}",
            line_number=line_number,
            request_id=request_id,
        )
    return value


def _required_nullable_enum(
    payload: Mapping[str, Any],
    field_name: str,
    allowed_values: frozenset[str],
    *,
    line_number: int | None,
    request_id: str | None,
) -> str | None:
    """Return a required nullable controlled string field."""
    value = _required_nullable_str(payload, field_name, line_number=line_number, request_id=request_id)
    if value is None:
        return None
    if value not in allowed_values:
        raise DatasetValidationError(
            f"{field_name} must be null or one of {sorted(allowed_values)}",
            line_number=line_number,
            request_id=request_id,
        )
    return value


def _required_bool(
    payload: Mapping[str, Any], field_name: str, *, line_number: int | None, request_id: str | None
) -> bool:
    """Return a required boolean field."""
    _require_present(payload, field_name, line_number=line_number, request_id=request_id)
    value = payload.get(field_name)
    if not isinstance(value, bool):
        raise DatasetValidationError(f"{field_name} must be a boolean", line_number=line_number, request_id=request_id)
    return value


def _required_number(
    payload: Mapping[str, Any], field_name: str, *, line_number: int | None, request_id: str | None
) -> float:
    """Return a required finite JSON number field."""
    _require_present(payload, field_name, line_number=line_number, request_id=request_id)
    value = payload.get(field_name)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(float(value)):
        raise DatasetValidationError(
            f"{field_name} must be a finite number",
            line_number=line_number,
            request_id=request_id,
        )
    return float(value)


def _required_probability(
    payload: Mapping[str, Any], field_name: str, *, line_number: int | None, request_id: str | None
) -> float:
    """Return a required probability value in the inclusive 0..1 range."""
    _require_present(payload, field_name, line_number=line_number, request_id=request_id)
    value = _probability_value(payload.get(field_name))
    if value is None:
        raise DatasetValidationError(
            f"{field_name} must be a number from 0.0 to 1.0",
            line_number=line_number,
            request_id=request_id,
        )
    return value


def _optional_probability(
    payload: Mapping[str, Any], field_name: str, *, line_number: int | None, request_id: str | None
) -> float | None:
    """Return an optional probability value in the inclusive 0..1 range."""
    if field_name not in payload:
        return None
    value = _probability_value(payload.get(field_name))
    if value is None:
        raise DatasetValidationError(
            f"{field_name} must be a number from 0.0 to 1.0",
            line_number=line_number,
            request_id=request_id,
        )
    return value


def _probability_value(value: object) -> float | None:
    """Return a float probability if the value is valid."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    numeric_value = float(value)
    if isfinite(numeric_value) and 0.0 <= numeric_value <= 1.0:
        return numeric_value
    return None


def _str_sequence(
    value: object, *, field_name: str, line_number: int | None, request_id: str | None
) -> tuple[str, ...]:
    """Validate a list of string IDs or coverage tags."""
    if not _is_sequence(value):
        raise DatasetValidationError(f"{field_name} must be a list", line_number=line_number, request_id=request_id)
    items = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise DatasetValidationError(
                f"{field_name} entries must be non-empty strings",
                line_number=line_number,
                request_id=request_id,
            )
        items.append(item.strip())
    if len(items) != len(set(items)):
        raise DatasetValidationError(
            f"{field_name} contains duplicate values", line_number=line_number, request_id=request_id
        )
    return tuple(items)


def _is_sequence(value: object) -> bool:
    """Return whether a JSON value is a list-like sequence."""
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray))
