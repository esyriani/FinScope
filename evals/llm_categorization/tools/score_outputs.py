"""Score saved LLM categorization outputs against validated eval datasets.

The scorer consumes JSONL files already written by an offline prompt run. It
parses raw model text, validates the structured output contract, computes
per-example and aggregate metrics, and writes review artifacts. It does not call
model providers or read/write FinScope runtime data.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from evals.llm_categorization.tools.io_utils import JsonlError, read_jsonl, write_jsonl
from evals.llm_categorization.tools.schemas import UNKNOWN_CATEGORY_NAME, DatasetValidationError, EvaluationExample
from evals.llm_categorization.tools.summarize_dataset import load_validated_records

MODEL_OUTPUT_FIELDS = frozenset(
    {
        "request_id",
        "category_id",
        "tag_ids",
        "confidence",
        "needs_review",
        "supported_by_similar_transactions",
        "reason",
    }
)
SCORER_VERSION = "001"
REQUIRED_RAW_OUTPUT_FIELDS = frozenset({"request_id", "raw_output", "model", "prompt_id"})
RAW_OUTPUT_FIELDS = REQUIRED_RAW_OUTPUT_FIELDS | frozenset(
    {
        "prompt_path",
        "prompt_hash",
        "dataset_hash",
        "timestamp",
        "token_usage",
        "duration_ms",
        "attempt_count",
        "response_format",
    }
)
CONFIDENCE_BANDS = (
    ("0.00-0.49", 0.0, 0.49),
    ("0.50-0.69", 0.5, 0.69),
    ("0.70-0.84", 0.7, 0.84),
    ("0.85-0.94", 0.85, 0.94),
    ("0.95-1.00", 0.95, 1.0),
)
SMALL_DATASET_CALIBRATION_THRESHOLD = 50
REPRESENTATIVE_FAILURE_LIMIT = 10


@dataclass(frozen=True)
class ParsedOutput:
    """Represent one parsed raw-output row or a missing dataset output."""

    source_line_number: int | None
    wrapper_request_id: str | None
    output_request_id: str | None
    model: str | None
    prompt_id: str | None
    raw_output: str | None
    parsed_output: dict[str, Any] | None
    valid_json: bool
    schema_valid: bool
    valid_category_id: bool
    valid_tag_ids: bool
    valid_taxonomy_ids: bool
    category_id: str | None
    tag_ids: tuple[str, ...]
    confidence: float | None
    needs_review: bool | None
    supported_by_similar_transactions: bool | None
    reason: str | None
    dataset_match: bool
    errors: tuple[str, ...]
    failure_modes: tuple[str, ...]


@dataclass(frozen=True)
class ScoredOutput:
    """Represent per-example semantic scoring results."""

    request_id: str
    parsed: ParsedOutput
    expected_category_id: str
    expected_category_name: str | None
    expected_tag_ids: tuple[str, ...]
    expected_needs_review: bool
    predicted_category_name: str | None
    predicted_tag_ids_for_metrics: tuple[str, ...]
    category_correct: bool
    known_category_correct: bool | None
    tag_true_positives: int
    tag_false_positives: int
    tag_false_negatives: int
    tag_precision: float | None
    tag_recall: float | None
    tag_f1: float | None
    exact_taxonomy_match: bool
    needs_review_correct: bool
    unknown_true_positive: bool
    false_unknown: bool
    missed_unknown: bool
    unsafe_auto_assignment: bool
    high_confidence_wrong: bool
    false_positive_tags: tuple[str, ...]
    missing_tags: tuple[str, ...]
    supported_by_similar_transactions_correct: bool | None
    over_review: bool
    under_review: bool
    failure_modes: tuple[str, ...]


@dataclass(frozen=True)
class CalibrationResult:
    """Represent confidence calibration metrics and reporting details."""

    score: float
    method: str
    note: str
    bands: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class ScoreRun:
    """Represent all scoring artifacts before serialization."""

    dataset_path: Path
    outputs_path: Path
    parsed_outputs: tuple[ParsedOutput, ...]
    scored_outputs: tuple[ScoredOutput, ...]
    unmatched_outputs: tuple[ParsedOutput, ...]
    duplicate_output_count: int
    metrics: dict[str, Any]


def load_raw_outputs(path: Path, examples_by_request_id: Mapping[str, EvaluationExample]) -> tuple[ParsedOutput, ...]:
    """Load and parse saved raw model outputs."""
    parsed_outputs = []
    for line_number, payload in read_jsonl(path):
        parsed_outputs.append(parse_raw_output_record(line_number, payload, examples_by_request_id))
    return tuple(parsed_outputs)


def parse_raw_output_record(
    line_number: int,
    payload: Mapping[str, Any],
    examples_by_request_id: Mapping[str, EvaluationExample],
) -> ParsedOutput:
    """Parse and validate one raw-output JSONL row."""
    wrapper_errors = raw_wrapper_errors(payload)
    wrapper_request_id = payload.get("request_id") if isinstance(payload.get("request_id"), str) else None
    raw_output = payload.get("raw_output") if isinstance(payload.get("raw_output"), str) else None
    model = payload.get("model") if isinstance(payload.get("model"), str) else None
    prompt_id = payload.get("prompt_id") if isinstance(payload.get("prompt_id"), str) else None
    example = examples_by_request_id.get(wrapper_request_id) if wrapper_request_id is not None else None
    dataset_match = example is not None

    errors = list(wrapper_errors)
    failure_modes: list[str] = []
    valid_json = False
    parsed_payload: dict[str, Any] | None = None
    output_request_id: str | None = None
    category_id: str | None = None
    tag_ids: tuple[str, ...] = ()
    confidence: float | None = None
    needs_review: bool | None = None
    supported_by_similar_transactions: bool | None = None
    reason: str | None = None

    if not dataset_match:
        errors.append("request_id does not match a dataset example")

    if raw_output is None:
        errors.append("raw_output must be a string")
        failure_modes.append("invalid_schema")
    else:
        try:
            parsed_json = json.loads(raw_output)
            valid_json = True
        except json.JSONDecodeError as exc:
            errors.append(f"raw_output is not valid JSON: {exc.msg}")
            failure_modes.append("invalid_json")
        else:
            if isinstance(parsed_json, dict):
                parsed_payload = dict(parsed_json)
                model_errors, model_values = validate_model_output_shape(parsed_payload)
                errors.extend(model_errors)
                output_request_id = model_values.get("request_id")
                category_id = model_values.get("category_id")
                tag_ids = model_values.get("tag_ids", ())
                confidence = model_values.get("confidence")
                needs_review = model_values.get("needs_review")
                supported_by_similar_transactions = model_values.get("supported_by_similar_transactions")
                reason = model_values.get("reason")
                if output_request_id is not None and wrapper_request_id is not None:
                    if output_request_id != wrapper_request_id:
                        errors.append("model output request_id does not match raw-output wrapper request_id")
                if output_request_id is not None and output_request_id not in examples_by_request_id:
                    errors.append("model output request_id does not match a dataset example")
            else:
                errors.append("raw_output JSON must be an object")

    schema_valid = valid_json and parsed_payload is not None and not model_schema_errors(errors)
    valid_category_id = False
    valid_tag_ids = False
    if schema_valid and example is not None:
        valid_category_id = category_id in example.candidate_taxonomy.category_ids()
        valid_tag_ids = all(tag_id in example.candidate_taxonomy.tag_ids() for tag_id in tag_ids)
        if not valid_category_id:
            failure_modes.append("invalid_category_id")
        if not valid_tag_ids:
            failure_modes.append("invalid_tag_id")

    if valid_json and not schema_valid:
        failure_modes.append("invalid_schema")

    return ParsedOutput(
        source_line_number=line_number,
        wrapper_request_id=wrapper_request_id,
        output_request_id=output_request_id,
        model=model,
        prompt_id=prompt_id,
        raw_output=raw_output,
        parsed_output=parsed_payload,
        valid_json=valid_json,
        schema_valid=schema_valid,
        valid_category_id=valid_category_id,
        valid_tag_ids=valid_tag_ids,
        valid_taxonomy_ids=valid_category_id and valid_tag_ids,
        category_id=category_id,
        tag_ids=tag_ids,
        confidence=confidence,
        needs_review=needs_review,
        supported_by_similar_transactions=supported_by_similar_transactions,
        reason=reason,
        dataset_match=dataset_match,
        errors=tuple(dedupe(errors)),
        failure_modes=tuple(dedupe(failure_modes)),
    )


def raw_wrapper_errors(payload: Mapping[str, Any]) -> list[str]:
    """Return non-semantic raw-output wrapper validation errors."""
    errors = []
    extra_fields = sorted(set(payload) - RAW_OUTPUT_FIELDS)
    if extra_fields:
        errors.append(f"raw-output row contains unknown fields: {', '.join(extra_fields)}")
    for field_name in REQUIRED_RAW_OUTPUT_FIELDS:
        if field_name not in payload:
            errors.append(f"raw-output row missing required field: {field_name}")
        elif not isinstance(payload[field_name], str):
            errors.append(f"raw-output row field must be a string: {field_name}")
        elif payload[field_name] == "":
            errors.append(f"raw-output row field must not be empty: {field_name}")
    return errors


def validate_model_output_shape(payload: Mapping[str, Any]) -> tuple[list[str], dict[str, Any]]:
    """Validate required model output fields and return normalized values."""
    errors = []
    values: dict[str, Any] = {}
    extra_fields = sorted(set(payload) - MODEL_OUTPUT_FIELDS)
    if extra_fields:
        errors.append(f"model output contains unknown fields: {', '.join(extra_fields)}")

    for field_name in MODEL_OUTPUT_FIELDS:
        if field_name not in payload:
            errors.append(f"model output missing required field: {field_name}")

    request_id = payload.get("request_id")
    if isinstance(request_id, str) and request_id:
        values["request_id"] = request_id
    elif "request_id" in payload:
        errors.append("model output request_id must be a non-empty string")

    category_id = payload.get("category_id")
    if isinstance(category_id, str) and category_id:
        values["category_id"] = category_id
    elif "category_id" in payload:
        errors.append("model output category_id must be a non-empty string")

    tag_ids = payload.get("tag_ids")
    if isinstance(tag_ids, list) and all(isinstance(tag_id, str) and tag_id for tag_id in tag_ids):
        if len(tag_ids) == len(set(tag_ids)):
            values["tag_ids"] = tuple(tag_ids)
        else:
            errors.append("model output tag_ids must not contain duplicates")
    elif "tag_ids" in payload:
        errors.append("model output tag_ids must be a list of non-empty strings")

    confidence = payload.get("confidence")
    if is_probability(confidence):
        values["confidence"] = float(confidence)
    elif "confidence" in payload:
        errors.append("model output confidence must be numeric and between 0 and 1")

    needs_review = payload.get("needs_review")
    if isinstance(needs_review, bool):
        values["needs_review"] = needs_review
    elif "needs_review" in payload:
        errors.append("model output needs_review must be boolean")

    supported = payload.get("supported_by_similar_transactions")
    if isinstance(supported, bool):
        values["supported_by_similar_transactions"] = supported
    elif "supported_by_similar_transactions" in payload:
        errors.append("model output supported_by_similar_transactions must be boolean")

    reason = payload.get("reason")
    if isinstance(reason, str):
        values["reason"] = reason
    elif "reason" in payload:
        errors.append("model output reason must be a string")

    return errors, values


def is_probability(value: object) -> bool:
    """Return whether a value is a finite probability and not a boolean."""
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and 0 <= value <= 1


def model_schema_errors(errors: Sequence[str]) -> bool:
    """Return whether accumulated errors invalidate the model output schema."""
    schema_error_prefixes = (
        "request_id does not match",
        "model output",
        "raw_output JSON must be an object",
        "raw_output must be a string",
        "raw-output row field must be a string: request_id",
        "raw-output row field must be a string: raw_output",
        "raw-output row field must not be empty: request_id",
        "raw-output row field must not be empty: raw_output",
        "raw-output row missing required field: request_id",
        "raw-output row missing required field: raw_output",
    )
    return any(error.startswith(schema_error_prefixes) for error in errors)


def missing_output_record(request_id: str) -> ParsedOutput:
    """Return a parsed-output placeholder for a missing dataset output."""
    return ParsedOutput(
        source_line_number=None,
        wrapper_request_id=request_id,
        output_request_id=None,
        model=None,
        prompt_id=None,
        raw_output=None,
        parsed_output=None,
        valid_json=False,
        schema_valid=False,
        valid_category_id=False,
        valid_tag_ids=False,
        valid_taxonomy_ids=False,
        category_id=None,
        tag_ids=(),
        confidence=None,
        needs_review=None,
        supported_by_similar_transactions=None,
        reason=None,
        dataset_match=True,
        errors=("missing raw output for dataset example",),
        failure_modes=("invalid_schema",),
    )


def score_example(example: EvaluationExample, parsed: ParsedOutput) -> ScoredOutput:
    """Compute semantic scores for one dataset example and parsed output."""
    expected_tags = set(example.expected.tag_ids)
    valid_predicted_tags = {tag_id for tag_id in parsed.tag_ids if tag_id in example.candidate_taxonomy.tag_ids()}
    predicted_category_name = (
        example.candidate_taxonomy.category_name(parsed.category_id) if parsed.category_id is not None else None
    )
    expected_category_name = example.candidate_taxonomy.category_name(example.expected.category_id)

    category_correct = parsed.valid_taxonomy_ids and parsed.category_id == example.expected.category_id
    if expected_category_name == UNKNOWN_CATEGORY_NAME:
        known_category_correct = None
    else:
        known_category_correct = category_correct

    if parsed.valid_taxonomy_ids:
        tag_true_positives = len(expected_tags & valid_predicted_tags)
        false_positive_tags = tuple(sorted(valid_predicted_tags - expected_tags))
        missing_tags = tuple(sorted(expected_tags - valid_predicted_tags))
    else:
        tag_true_positives = 0
        false_positive_tags = tuple(sorted(valid_predicted_tags))
        missing_tags = tuple(sorted(expected_tags))
    tag_false_positives = len(false_positive_tags)
    tag_false_negatives = len(missing_tags)
    tag_precision, tag_recall, tag_f1 = precision_recall_f1(
        tag_true_positives,
        tag_false_positives,
        tag_false_negatives,
    )
    exact_taxonomy_match = category_correct and not false_positive_tags and not missing_tags
    taxonomy_wrong = not exact_taxonomy_match
    predicted_unknown = parsed.valid_taxonomy_ids and predicted_category_name == UNKNOWN_CATEGORY_NAME
    expected_unknown = expected_category_name == UNKNOWN_CATEGORY_NAME
    unknown_true_positive = predicted_unknown and expected_unknown
    false_unknown = predicted_unknown and not expected_unknown
    missed_unknown = expected_unknown and not predicted_unknown
    needs_review_correct = parsed.schema_valid and parsed.needs_review == example.expected.needs_review
    over_review = parsed.schema_valid and parsed.needs_review is True and not example.expected.needs_review
    under_review = parsed.schema_valid and parsed.needs_review is False and example.expected.needs_review
    unsafe_auto_assignment = taxonomy_wrong and parsed.needs_review is False
    high_confidence_wrong = taxonomy_wrong and parsed.confidence is not None and parsed.confidence >= 0.95
    supported_correct = supported_by_similar_transactions_correct(example, parsed)

    failure_modes = list(parsed.failure_modes)
    if parsed.valid_category_id and parsed.category_id != example.expected.category_id:
        failure_modes.append("wrong_category")
    if missing_tags:
        failure_modes.append("missing_tag")
    if false_positive_tags:
        failure_modes.append("extra_tag")
    if false_unknown:
        failure_modes.append("false_unknown")
    if missed_unknown:
        failure_modes.append("missed_unknown")
    if unsafe_auto_assignment:
        failure_modes.append("unsafe_auto_assignment")
    if high_confidence_wrong:
        failure_modes.append("high_confidence_wrong")
    if over_review:
        failure_modes.append("over_review")
    if under_review:
        failure_modes.append("under_review")
    if tax_over_tagging(example, parsed):
        failure_modes.append("tax_over_tagging")
    if reimbursable_tag_confusion(example, parsed):
        failure_modes.append("reimbursement_confusion")
    failure_modes.extend(similar_history_failure_modes(example, parsed))
    failure_modes.extend(inferable_failure_modes(example, parsed, predicted_category_name, expected_category_name))

    return ScoredOutput(
        request_id=example.request_id,
        parsed=parsed,
        expected_category_id=example.expected.category_id,
        expected_category_name=expected_category_name,
        expected_tag_ids=example.expected.tag_ids,
        expected_needs_review=example.expected.needs_review,
        predicted_category_name=predicted_category_name,
        predicted_tag_ids_for_metrics=tuple(sorted(valid_predicted_tags)),
        category_correct=category_correct,
        known_category_correct=known_category_correct,
        tag_true_positives=tag_true_positives,
        tag_false_positives=tag_false_positives,
        tag_false_negatives=tag_false_negatives,
        tag_precision=tag_precision,
        tag_recall=tag_recall,
        tag_f1=tag_f1,
        exact_taxonomy_match=exact_taxonomy_match,
        needs_review_correct=needs_review_correct,
        unknown_true_positive=unknown_true_positive,
        false_unknown=false_unknown,
        missed_unknown=missed_unknown,
        unsafe_auto_assignment=unsafe_auto_assignment,
        high_confidence_wrong=high_confidence_wrong,
        false_positive_tags=false_positive_tags,
        missing_tags=missing_tags,
        supported_by_similar_transactions_correct=supported_correct,
        over_review=over_review,
        under_review=under_review,
        failure_modes=tuple(dedupe(failure_modes)),
    )


def supported_by_similar_transactions_correct(example: EvaluationExample, parsed: ParsedOutput) -> bool | None:
    """Return support-flag correctness when similar history is present."""
    if not example.similar_transactions or not parsed.schema_valid:
        return None
    return parsed.supported_by_similar_transactions == expected_supported_by_similar_transactions(example)


def expected_supported_by_similar_transactions(example: EvaluationExample) -> bool:
    """Infer whether similar history supports the expected taxonomy exactly."""
    expected_tags = set(example.expected.tag_ids)
    return any(
        similar.category_id == example.expected.category_id and set(similar.tag_ids) == expected_tags
        for similar in example.similar_transactions
    )


def similar_history_failure_modes(example: EvaluationExample, parsed: ParsedOutput) -> list[str]:
    """Return failure modes for similar-history support flags."""
    if not example.similar_transactions or not parsed.schema_valid:
        return []
    expected_supported = expected_supported_by_similar_transactions(example)
    if expected_supported and parsed.supported_by_similar_transactions is False:
        return ["ignored_similar_history"]
    if not expected_supported and parsed.supported_by_similar_transactions is True:
        return ["overused_similar_history"]
    return []


def inferable_failure_modes(
    example: EvaluationExample,
    parsed: ParsedOutput,
    predicted_category_name: str | None,
    expected_category_name: str | None,
) -> list[str]:
    """Return best-effort interpretable failure modes from category and tag names."""
    if (
        not parsed.valid_category_id
        or predicted_category_name is None
        or predicted_category_name == expected_category_name
    ):
        return []

    failure_modes = []
    if direction_error(example, predicted_category_name):
        failure_modes.append("direction_error")
    if {predicted_category_name, expected_category_name} == {"Transfers", "Income"}:
        failure_modes.append("transfer_income_confusion")
    if predicted_category_name == "Reimbursement" or expected_category_name == "Reimbursement":
        failure_modes.append("reimbursement_confusion")
    if {predicted_category_name, expected_category_name} == {"Rental", "Housing"}:
        failure_modes.append("rental_housing_confusion")
    return failure_modes


def direction_error(example: EvaluationExample, predicted_category_name: str) -> bool:
    """Return whether the category is directionally implausible from signed amount."""
    if example.coverage.direction == "debit" and predicted_category_name in {"Income", "Reimbursement"}:
        return True
    return example.coverage.direction == "credit" and predicted_category_name not in {
        "Income",
        "Reimbursement",
        "Transfers",
        "UNKNOWN",
    }


def tax_over_tagging(example: EvaluationExample, parsed: ParsedOutput) -> bool:
    """Return whether a Tax tag was predicted when not expected."""
    tax_tag_ids = {
        item.id
        for item in example.candidate_taxonomy.tags
        if item.name == "Tax" and item.id not in example.expected.tag_ids
    }
    return any(tag_id in tax_tag_ids for tag_id in parsed.tag_ids)


def reimbursable_tag_confusion(example: EvaluationExample, parsed: ParsedOutput) -> bool:
    """Return whether the Reimbursable tag was incorrectly added or missed."""
    reimbursable_tag_ids = {item.id for item in example.candidate_taxonomy.tags if item.name == "Reimbursable"}
    if not reimbursable_tag_ids:
        return False
    expected_has_reimbursable = any(tag_id in reimbursable_tag_ids for tag_id in example.expected.tag_ids)
    predicted_has_reimbursable = any(tag_id in reimbursable_tag_ids for tag_id in parsed.tag_ids)
    return expected_has_reimbursable != predicted_has_reimbursable


def precision_recall_f1(true_positives: int, false_positives: int, false_negatives: int) -> tuple[float | None, ...]:
    """Return precision, recall, and F1 for count inputs."""
    precision = true_positives / (true_positives + false_positives) if true_positives + false_positives else None
    recall = true_positives / (true_positives + false_negatives) if true_positives + false_negatives else None
    if precision is None or recall is None or precision + recall == 0:
        f1 = None if precision is None and recall is None else 0.0
    else:
        f1 = 2 * precision * recall / (precision + recall)
    return round_optional(precision), round_optional(recall), round_optional(f1)


def score_run(dataset_path: Path, outputs_path: Path) -> ScoreRun:
    """Parse outputs, score all dataset examples, and compute aggregate metrics."""
    _, examples = load_validated_records(dataset_path)
    examples_by_request_id = {example.request_id: example for example in examples}
    parsed_raw_outputs = load_raw_outputs(outputs_path, examples_by_request_id)
    parsed_by_request_id: dict[str, ParsedOutput] = {}
    unmatched_outputs = []
    duplicate_output_count = 0
    for parsed in parsed_raw_outputs:
        if parsed.wrapper_request_id not in examples_by_request_id:
            unmatched_outputs.append(parsed)
            continue
        if parsed.wrapper_request_id in parsed_by_request_id:
            duplicate_output_count += 1
            continue
        parsed_by_request_id[parsed.wrapper_request_id] = parsed

    scored_outputs = tuple(
        score_example(example, parsed_by_request_id.get(example.request_id, missing_output_record(example.request_id)))
        for example in examples
    )
    parsed_outputs = tuple(parsed_raw_outputs) + tuple(
        scored.parsed for scored in scored_outputs if scored.parsed.source_line_number is None
    )
    metrics = compute_metrics(
        dataset_path=dataset_path,
        outputs_path=outputs_path,
        scored_outputs=scored_outputs,
        parsed_raw_outputs=parsed_raw_outputs,
        unmatched_outputs=tuple(unmatched_outputs),
        duplicate_output_count=duplicate_output_count,
    )
    return ScoreRun(
        dataset_path=dataset_path,
        outputs_path=outputs_path,
        parsed_outputs=parsed_outputs,
        scored_outputs=scored_outputs,
        unmatched_outputs=tuple(unmatched_outputs),
        duplicate_output_count=duplicate_output_count,
        metrics=metrics,
    )


def compute_metrics(
    *,
    dataset_path: Path,
    outputs_path: Path,
    scored_outputs: Sequence[ScoredOutput],
    parsed_raw_outputs: Sequence[ParsedOutput],
    unmatched_outputs: Sequence[ParsedOutput],
    duplicate_output_count: int,
) -> dict[str, Any]:
    """Compute aggregate metrics for a scored run."""
    example_count = len(scored_outputs)
    valid_json_count = sum(scored.parsed.valid_json for scored in scored_outputs)
    schema_valid_count = sum(scored.parsed.schema_valid for scored in scored_outputs)
    valid_category_id_count = sum(scored.parsed.valid_category_id for scored in scored_outputs)
    valid_tag_id_count = sum(scored.parsed.valid_tag_ids for scored in scored_outputs)
    valid_taxonomy_id_count = sum(scored.parsed.valid_taxonomy_ids for scored in scored_outputs)
    category_correct_count = sum(scored.category_correct for scored in scored_outputs)
    known_category_outputs = [scored for scored in scored_outputs if scored.known_category_correct is not None]
    exact_match_count = sum(scored.exact_taxonomy_match for scored in scored_outputs)
    tag_tp = sum(scored.tag_true_positives for scored in scored_outputs)
    tag_fp = sum(scored.tag_false_positives for scored in scored_outputs)
    tag_fn = sum(scored.tag_false_negatives for scored in scored_outputs)
    tag_micro_precision, tag_micro_recall, tag_micro_f1 = precision_recall_f1(tag_tp, tag_fp, tag_fn)
    tag_macro_precision, tag_macro_recall, tag_macro_f1 = tag_macro_metrics(scored_outputs)
    unknown_tp = sum(scored.unknown_true_positive for scored in scored_outputs)
    false_unknown_count = sum(scored.false_unknown for scored in scored_outputs)
    missed_unknown_count = sum(scored.missed_unknown for scored in scored_outputs)
    expected_unknown_count = sum(scored.expected_category_name == UNKNOWN_CATEGORY_NAME for scored in scored_outputs)
    predicted_unknown_count = unknown_tp + false_unknown_count
    non_unknown_count = example_count - expected_unknown_count
    needs_review_tp = sum(
        scored.parsed.schema_valid and scored.parsed.needs_review is True and scored.expected_needs_review
        for scored in scored_outputs
    )
    needs_review_fp = sum(scored.over_review for scored in scored_outputs)
    needs_review_fn = sum(
        scored.expected_needs_review and not (scored.parsed.schema_valid and scored.parsed.needs_review is True)
        for scored in scored_outputs
    )
    needs_review_precision, needs_review_recall, needs_review_f1 = precision_recall_f1(
        needs_review_tp,
        needs_review_fp,
        needs_review_fn,
    )
    unsafe_count = sum(scored.unsafe_auto_assignment for scored in scored_outputs)
    high_confidence_wrong_count = sum(scored.high_confidence_wrong for scored in scored_outputs)
    over_review_count = sum(scored.over_review for scored in scored_outputs)
    under_review_count = sum(scored.under_review for scored in scored_outputs)
    calibration = confidence_calibration(scored_outputs)
    failure_mode_counts = Counter(failure_mode for scored in scored_outputs for failure_mode in scored.failure_modes)
    failure_mode_counts.update(failure_mode for parsed in unmatched_outputs for failure_mode in parsed.failure_modes)
    invalid_output_rate = safe_rate(example_count - valid_taxonomy_id_count, example_count)
    category_accuracy = safe_rate(category_correct_count, example_count)
    exact_match_rate = safe_rate(exact_match_count, example_count)
    metrics = {
        "run": {
            "dataset": str(dataset_path),
            "outputs": str(outputs_path),
            "example_count": example_count,
            "raw_output_count": len(parsed_raw_outputs),
            "missing_output_count": sum(scored.parsed.source_line_number is None for scored in scored_outputs),
            "unmatched_output_count": len(unmatched_outputs),
            "duplicate_output_count": duplicate_output_count,
            "models": sorted({parsed.model for parsed in parsed_raw_outputs if parsed.model is not None}),
            "prompt_ids": sorted({parsed.prompt_id for parsed in parsed_raw_outputs if parsed.prompt_id is not None}),
        },
        "headline": {
            "composite_score": None,
            "valid_json_rate": safe_rate(valid_json_count, example_count),
            "schema_valid_rate": safe_rate(schema_valid_count, example_count),
            "valid_category_id_rate": safe_rate(valid_category_id_count, example_count),
            "valid_tag_id_rate": safe_rate(valid_tag_id_count, example_count),
            "valid_taxonomy_id_rate": safe_rate(valid_taxonomy_id_count, example_count),
            "category_accuracy": category_accuracy,
            "known_category_accuracy": safe_rate(
                sum(scored.known_category_correct is True for scored in known_category_outputs),
                len(known_category_outputs),
            ),
            "exact_taxonomy_match_rate": exact_match_rate,
            "tag_micro_precision": tag_micro_precision,
            "tag_micro_recall": tag_micro_recall,
            "tag_micro_f1": tag_micro_f1,
            "tag_macro_precision": tag_macro_precision,
            "tag_macro_recall": tag_macro_recall,
            "tag_macro_f1": tag_macro_f1,
            "unknown_precision": safe_rate(unknown_tp, predicted_unknown_count),
            "unknown_recall": safe_rate(unknown_tp, expected_unknown_count),
            "false_unknown_rate": safe_rate(false_unknown_count, non_unknown_count),
            "missed_unknown_rate": safe_rate(missed_unknown_count, expected_unknown_count),
            "needs_review_precision": needs_review_precision,
            "needs_review_recall": needs_review_recall,
            "needs_review_f1": needs_review_f1,
            "unsafe_auto_assignment_rate": safe_rate(unsafe_count, example_count),
            "high_confidence_wrong_rate": safe_rate(high_confidence_wrong_count, example_count),
            "over_review_count": over_review_count,
            "under_review_count": under_review_count,
            "confidence_calibration_score": calibration.score,
            "invalid_output_rate": invalid_output_rate,
        },
        "confidence_calibration": {
            "method": calibration.method,
            "note": calibration.note,
            "bands": list(calibration.bands),
        },
        "counts": {
            "valid_json": valid_json_count,
            "schema_valid": schema_valid_count,
            "valid_category_id": valid_category_id_count,
            "valid_tag_id": valid_tag_id_count,
            "valid_taxonomy_id": valid_taxonomy_id_count,
            "category_correct": category_correct_count,
            "exact_taxonomy_match": exact_match_count,
            "tag_true_positives": tag_tp,
            "tag_false_positives": tag_fp,
            "tag_false_negatives": tag_fn,
            "unknown_true_positives": unknown_tp,
            "false_unknown": false_unknown_count,
            "missed_unknown": missed_unknown_count,
            "unsafe_auto_assignment": unsafe_count,
            "high_confidence_wrong": high_confidence_wrong_count,
        },
        "failure_mode_counts": dict(sorted(failure_mode_counts.items())),
    }
    metrics["headline"]["composite_score"] = composite_score(metrics["headline"])
    return metrics


def tag_macro_metrics(scored_outputs: Sequence[ScoredOutput]) -> tuple[float | None, ...]:
    """Compute macro tag precision, recall, and F1 across represented tag IDs."""
    tag_ids = sorted(
        {
            tag_id
            for scored in scored_outputs
            for tag_id in (*scored.expected_tag_ids, *scored.predicted_tag_ids_for_metrics)
        }
    )
    if not tag_ids:
        return None, None, None

    precisions = []
    recalls = []
    f1s = []
    for tag_id in tag_ids:
        tp = sum(
            tag_id in scored.expected_tag_ids and tag_id in scored.predicted_tag_ids_for_metrics
            for scored in scored_outputs
        )
        fp = sum(
            tag_id not in scored.expected_tag_ids and tag_id in scored.predicted_tag_ids_for_metrics
            for scored in scored_outputs
        )
        fn = sum(
            tag_id in scored.expected_tag_ids and tag_id not in scored.predicted_tag_ids_for_metrics
            for scored in scored_outputs
        )
        precision, recall, f1 = precision_recall_f1(tp, fp, fn)
        precisions.append(precision or 0.0)
        recalls.append(recall or 0.0)
        f1s.append(f1 or 0.0)
    return (
        round_metric(sum(precisions) / len(precisions)),
        round_metric(sum(recalls) / len(recalls)),
        round_metric(sum(f1s) / len(f1s)),
    )


def confidence_calibration(scored_outputs: Sequence[ScoredOutput]) -> CalibrationResult:
    """Compute confidence calibration by configured bands."""
    bands = []
    total_weighted_error = 0.0
    total_count = 0
    populated_band_count = 0
    for label, lower, upper in CONFIDENCE_BANDS:
        band_outputs = [
            scored
            for scored in scored_outputs
            if scored.parsed.schema_valid
            and scored.parsed.confidence is not None
            and lower <= scored.parsed.confidence <= upper
        ]
        count = len(band_outputs)
        if count:
            populated_band_count += 1
        average_confidence = sum(scored.parsed.confidence or 0.0 for scored in band_outputs) / count if count else None
        exact_accuracy = safe_rate(sum(scored.exact_taxonomy_match for scored in band_outputs), count)
        calibration_error = (
            abs(average_confidence - exact_accuracy)
            if average_confidence is not None and exact_accuracy is not None
            else None
        )
        if calibration_error is not None:
            total_weighted_error += calibration_error * count
            total_count += count
        bands.append(
            {
                "band": label,
                "count": count,
                "average_confidence": round_optional(average_confidence),
                "exact_taxonomy_accuracy": round_optional(exact_accuracy),
                "calibration_error": round_optional(calibration_error),
            }
        )

    if len(scored_outputs) < SMALL_DATASET_CALIBRATION_THRESHOLD or populated_band_count < 3:
        high_confidence_outputs = [
            scored
            for scored in scored_outputs
            if scored.parsed.schema_valid and scored.parsed.confidence is not None and scored.parsed.confidence >= 0.95
        ]
        if high_confidence_outputs:
            proxy_score = safe_rate(
                sum(scored.exact_taxonomy_match for scored in high_confidence_outputs), len(high_confidence_outputs)
            )
            method = "proxy_high_confidence_correctness"
        else:
            proxy_score = safe_rate(sum(scored.exact_taxonomy_match for scored in scored_outputs), len(scored_outputs))
            method = "proxy_exact_taxonomy_rate_no_high_confidence"
        return CalibrationResult(
            score=round_metric(proxy_score or 0.0),
            method=method,
            note=(
                "Small datasets or sparse confidence bands do not support robust calibration; "
                "this score is a simple proxy and should not be treated as authoritative."
            ),
            bands=tuple(bands),
        )

    weighted_error = total_weighted_error / total_count if total_count else 1.0
    return CalibrationResult(
        score=round_metric(max(0.0, 1.0 - weighted_error)),
        method="weighted_band_calibration_error",
        note="Score is one minus weighted absolute error between average confidence and empirical exact-match accuracy.",
        bands=tuple(bands),
    )


def composite_score(headline: Mapping[str, Any]) -> float:
    """Return the non-authoritative composite score from Task 1 methodology."""
    score = (
        0.40 * metric_value(headline.get("category_accuracy"))
        + 0.25 * metric_value(headline.get("tag_micro_f1"))
        + 0.15 * metric_value(headline.get("exact_taxonomy_match_rate"))
        + 0.10 * metric_value(headline.get("needs_review_f1"))
        + 0.10 * metric_value(headline.get("confidence_calibration_score"))
        - 0.25 * metric_value(headline.get("unsafe_auto_assignment_rate"))
        - 0.10 * metric_value(headline.get("invalid_output_rate"))
    )
    return round_metric(score)


def metric_value(value: object) -> float:
    """Return a numeric metric value, treating unavailable metrics as zero."""
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0.0


def safe_rate(numerator: int, denominator: int) -> float | None:
    """Return a rounded rate, or None when the denominator is zero."""
    if denominator == 0:
        return None
    return round_metric(numerator / denominator)


def round_metric(value: float) -> float:
    """Round metric values for deterministic artifacts."""
    return round(value, 6)


def round_optional(value: float | None) -> float | None:
    """Round optional metric values for deterministic artifacts."""
    return None if value is None else round_metric(value)


def serialize_parsed_output(parsed: ParsedOutput) -> dict[str, Any]:
    """Serialize a parsed output record to JSON-compatible data."""
    return {
        "source_line_number": parsed.source_line_number,
        "request_id": parsed.wrapper_request_id,
        "output_request_id": parsed.output_request_id,
        "model": parsed.model,
        "prompt_id": parsed.prompt_id,
        "raw_output": parsed.raw_output,
        "parsed_output": parsed.parsed_output,
        "valid_json": parsed.valid_json,
        "schema_valid": parsed.schema_valid,
        "valid_category_id": parsed.valid_category_id,
        "valid_tag_ids": parsed.valid_tag_ids,
        "valid_taxonomy_ids": parsed.valid_taxonomy_ids,
        "dataset_match": parsed.dataset_match,
        "errors": list(parsed.errors),
        "failure_modes": list(parsed.failure_modes),
    }


def serialize_scored_output(scored: ScoredOutput) -> dict[str, Any]:
    """Serialize a scored output record to JSON-compatible data."""
    return {
        "request_id": scored.request_id,
        "expected": {
            "category_id": scored.expected_category_id,
            "category_name": scored.expected_category_name,
            "tag_ids": list(scored.expected_tag_ids),
            "needs_review": scored.expected_needs_review,
        },
        "predicted": {
            "category_id": scored.parsed.category_id,
            "category_name": scored.predicted_category_name,
            "tag_ids": list(scored.parsed.tag_ids),
            "confidence": scored.parsed.confidence,
            "needs_review": scored.parsed.needs_review,
            "supported_by_similar_transactions": scored.parsed.supported_by_similar_transactions,
            "reason": scored.parsed.reason,
        },
        "validity": {
            "valid_json": scored.parsed.valid_json,
            "schema_valid": scored.parsed.schema_valid,
            "valid_category_id": scored.parsed.valid_category_id,
            "valid_tag_ids": scored.parsed.valid_tag_ids,
            "valid_taxonomy_ids": scored.parsed.valid_taxonomy_ids,
        },
        "scores": {
            "category_correct": scored.category_correct,
            "known_category_correct": scored.known_category_correct,
            "tag_true_positives": scored.tag_true_positives,
            "tag_false_positives": scored.tag_false_positives,
            "tag_false_negatives": scored.tag_false_negatives,
            "tag_precision": round_optional(scored.tag_precision),
            "tag_recall": round_optional(scored.tag_recall),
            "tag_f1": round_optional(scored.tag_f1),
            "exact_taxonomy_match": scored.exact_taxonomy_match,
            "needs_review_correct": scored.needs_review_correct,
            "unknown_true_positive": scored.unknown_true_positive,
            "false_unknown": scored.false_unknown,
            "missed_unknown": scored.missed_unknown,
            "unsafe_auto_assignment": scored.unsafe_auto_assignment,
            "high_confidence_wrong": scored.high_confidence_wrong,
            "false_positive_tags": list(scored.false_positive_tags),
            "missing_tags": list(scored.missing_tags),
            "supported_by_similar_transactions_correct": scored.supported_by_similar_transactions_correct,
        },
        "errors": list(scored.parsed.errors),
        "failure_modes": list(scored.failure_modes),
    }


def failure_records(score_run_: ScoreRun) -> list[dict[str, Any]]:
    """Return failure records from scored and unmatched outputs."""
    failures = [
        serialize_scored_output(scored)
        for scored in score_run_.scored_outputs
        if scored.failure_modes or scored.parsed.errors
    ]
    for parsed in score_run_.unmatched_outputs:
        failures.append(
            {
                "request_id": parsed.wrapper_request_id,
                "expected": None,
                "predicted": {
                    "request_id": parsed.output_request_id,
                    "category_id": parsed.category_id,
                    "tag_ids": list(parsed.tag_ids),
                    "confidence": parsed.confidence,
                    "needs_review": parsed.needs_review,
                    "supported_by_similar_transactions": parsed.supported_by_similar_transactions,
                    "reason": parsed.reason,
                },
                "validity": {
                    "valid_json": parsed.valid_json,
                    "schema_valid": parsed.schema_valid,
                    "valid_category_id": parsed.valid_category_id,
                    "valid_tag_ids": parsed.valid_tag_ids,
                    "valid_taxonomy_ids": parsed.valid_taxonomy_ids,
                    "dataset_match": parsed.dataset_match,
                },
                "scores": None,
                "errors": list(parsed.errors),
                "failure_modes": list(parsed.failure_modes),
            }
        )
    return failures


def write_score_artifacts(score_run_: ScoreRun, out_dir: Path) -> None:
    """Write parsed outputs, scored outputs, failures, metrics, and report."""
    out_dir.mkdir(parents=True, exist_ok=True)
    write_jsonl(
        out_dir / "parsed_outputs.jsonl", (serialize_parsed_output(parsed) for parsed in score_run_.parsed_outputs)
    )
    write_jsonl(
        out_dir / "scored_outputs.jsonl",
        (serialize_scored_output(scored) for scored in score_run_.scored_outputs),
    )
    failures = failure_records(score_run_)
    write_jsonl(out_dir / "failures.jsonl", failures)
    (out_dir / "metrics.json").write_text(
        f"{json.dumps(score_run_.metrics, ensure_ascii=True, indent=2, sort_keys=True)}\n",
        encoding="utf-8",
        newline="\n",
    )
    (out_dir / "report.md").write_text(
        f"{render_report(score_run_, failures)}\n",
        encoding="utf-8",
        newline="\n",
    )


def render_report(score_run_: ScoreRun, failures: Sequence[Mapping[str, Any]]) -> str:
    """Render the Markdown scoring report."""
    metrics = score_run_.metrics
    headline = metrics["headline"]
    lines = [
        "# LLM Categorization Scoring Report",
        "",
        "## Run summary",
        "",
        f"- Dataset: `{metrics['run']['dataset']}`",
        f"- Outputs: `{metrics['run']['outputs']}`",
        f"- Examples: {metrics['run']['example_count']}",
        f"- Raw outputs: {metrics['run']['raw_output_count']}",
        f"- Missing outputs: {metrics['run']['missing_output_count']}",
        f"- Unmatched outputs: {metrics['run']['unmatched_output_count']}",
        f"- Duplicate outputs ignored: {metrics['run']['duplicate_output_count']}",
        f"- Models: {', '.join(metrics['run']['models']) if metrics['run']['models'] else '(none)'}",
        f"- Prompt IDs: {', '.join(metrics['run']['prompt_ids']) if metrics['run']['prompt_ids'] else '(none)'}",
        "",
        "## Headline metrics",
        "",
        "| Metric | Value |",
        "| --- | ---: |",
        *metric_table_rows(
            headline,
            [
                "composite_score",
                "category_accuracy",
                "known_category_accuracy",
                "exact_taxonomy_match_rate",
                "tag_micro_f1",
                "needs_review_f1",
                "confidence_calibration_score",
                "unsafe_auto_assignment_rate",
                "high_confidence_wrong_rate",
                "invalid_output_rate",
            ],
        ),
        "",
        "The composite score is a convenience signal only; review safety and failure modes before ranking prompts.",
        "",
        "## Validity metrics",
        "",
        "| Metric | Value |",
        "| --- | ---: |",
        *metric_table_rows(
            headline,
            [
                "valid_json_rate",
                "schema_valid_rate",
                "valid_category_id_rate",
                "valid_tag_id_rate",
                "valid_taxonomy_id_rate",
            ],
        ),
        "",
        "## Category metrics",
        "",
        "| Metric | Value |",
        "| --- | ---: |",
        *metric_table_rows(headline, ["category_accuracy", "known_category_accuracy", "exact_taxonomy_match_rate"]),
        "",
        "## Tag metrics",
        "",
        "| Metric | Value |",
        "| --- | ---: |",
        *metric_table_rows(
            headline,
            [
                "tag_micro_precision",
                "tag_micro_recall",
                "tag_micro_f1",
                "tag_macro_precision",
                "tag_macro_recall",
                "tag_macro_f1",
            ],
        ),
        "",
        "## UNKNOWN behavior",
        "",
        "| Metric | Value |",
        "| --- | ---: |",
        *metric_table_rows(
            headline, ["unknown_precision", "unknown_recall", "false_unknown_rate", "missed_unknown_rate"]
        ),
        "",
        "## needs_review behavior",
        "",
        "| Metric | Value |",
        "| --- | ---: |",
        *metric_table_rows(headline, ["needs_review_precision", "needs_review_recall", "needs_review_f1"]),
        f"- Over-review count: {headline['over_review_count']}",
        f"- Under-review count: {headline['under_review_count']}",
        "",
        "## Confidence calibration",
        "",
        f"- Method: {metrics['confidence_calibration']['method']}",
        f"- Note: {metrics['confidence_calibration']['note']}",
        "",
        "| Band | Count | Avg confidence | Exact accuracy | Calibration error |",
        "| --- | ---: | ---: | ---: | ---: |",
        *calibration_table_rows(metrics["confidence_calibration"]["bands"]),
        "",
        "## Failure-mode counts",
        "",
        *counter_lines(metrics["failure_mode_counts"]),
        "",
        "## Representative failures",
        "",
        *representative_failure_lines(failures),
    ]
    return "\n".join(lines)


def metric_table_rows(metrics: Mapping[str, Any], keys: Sequence[str]) -> list[str]:
    """Render selected metrics as Markdown table rows."""
    return [f"| {key} | {format_metric(metrics.get(key))} |" for key in keys]


def calibration_table_rows(bands: Sequence[Mapping[str, Any]]) -> list[str]:
    """Render calibration bands as Markdown table rows."""
    return [
        "| {band} | {count} | {avg} | {accuracy} | {error} |".format(
            band=band["band"],
            count=band["count"],
            avg=format_metric(band["average_confidence"]),
            accuracy=format_metric(band["exact_taxonomy_accuracy"]),
            error=format_metric(band["calibration_error"]),
        )
        for band in bands
    ]


def counter_lines(counter: Mapping[str, int]) -> list[str]:
    """Render a counter as Markdown bullets."""
    if not counter:
        return ["- (none)"]
    return [f"- {name}: {count}" for name, count in sorted(counter.items())]


def representative_failure_lines(failures: Sequence[Mapping[str, Any]]) -> list[str]:
    """Render a bounded list of representative failures."""
    if not failures:
        return ["- (none)"]
    lines = []
    for failure in failures[:REPRESENTATIVE_FAILURE_LIMIT]:
        request_id = failure.get("request_id")
        modes = ", ".join(failure.get("failure_modes") or [])
        errors = "; ".join(failure.get("errors") or [])
        lines.append(f"- `{request_id}`: {modes or '(no failure mode)'}{f' - {errors}' if errors else ''}")
    remaining = len(failures) - REPRESENTATIVE_FAILURE_LIMIT
    if remaining > 0:
        lines.append(f"- ... {remaining} more failure(s)")
    return lines


def format_metric(value: object) -> str:
    """Format a metric value for Markdown."""
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.6f}"
    return str(value)


def dedupe(values: Iterable[str]) -> list[str]:
    """Return values in first-seen order without duplicates."""
    seen = set()
    deduped = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        deduped.append(value)
    return deduped


def build_parser() -> argparse.ArgumentParser:
    """Create the command-line parser."""
    parser = argparse.ArgumentParser(description="Score saved LLM categorization raw outputs.")
    parser.add_argument("--dataset", required=True, type=Path, help="Validated labeled JSONL dataset.")
    parser.add_argument("--outputs", required=True, type=Path, help="Raw model outputs JSONL file.")
    parser.add_argument("--out-dir", required=True, type=Path, help="Directory for scoring artifacts.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the scoring CLI."""
    parser = build_parser()
    args = parser.parse_args(argv)
    from evals.llm_categorization.services.scoring_service import score_outputs_file

    try:
        score_outputs_file(args.dataset, args.outputs, args.out_dir)
    except (DatasetValidationError, JsonlError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(f"Wrote scoring artifacts to {args.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
