"""Prompt evaluation utilities for LLM categorization.

The helpers in this module load sanitized product examples, build the same
prompt messages used by the application, score ID-based model results, and
write repeatable evaluation artifacts. They deliberately do not open network
connections; callers inject a provider function when running an opt-in eval.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from finance_app.core.builtin_taxonomy import BUILTIN_CATEGORIES, BUILTIN_TAGS
from finance_app.core.constants import UNKNOWN_CATEGORY
from finance_app.core.taxonomy import load_category_seed
from finance_app.modules.categories.llm_prompts import build_llm_messages

DATASET_PATH = Path(__file__).with_name("datasets") / "validation.jsonl"
RUNS_DIR = Path(__file__).with_name("runs")
DEFAULT_VERIFY_THRESHOLD = 0.95
DEFAULT_REVIEW_THRESHOLD = 0.85


@dataclass(frozen=True)
class PromptEvalTaxonomy:
    """Deterministic taxonomy rows used by prompt eval examples."""

    category_options: list[str]
    tag_options: list[str]
    category_rows: list[dict[str, Any]]
    tag_rows: list[dict[str, Any]]


@dataclass(frozen=True)
class PromptEvalRecord:
    """Scored result for one prompt evaluation case."""

    case: dict[str, Any]
    messages: list[dict[str, str]]
    raw_output: Any
    decoded: dict[str, Any]
    metrics: dict[str, Any]
    error: str = ""


@dataclass(frozen=True)
class PromptEvalRun:
    """Aggregated prompt evaluation output."""

    records: list[PromptEvalRecord]
    summary: dict[str, Any]
    metadata: dict[str, Any]


PromptEvalProvider = Callable[[Mapping[str, Any], Sequence[Mapping[str, str]]], Any]


def load_prompt_eval_cases(path: str | Path = DATASET_PATH) -> list[dict[str, Any]]:
    """Load sanitized prompt-eval cases from a JSONL dataset."""
    cases: list[dict[str, Any]] = []
    for line_number, raw_line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        try:
            case = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON on eval case line {line_number}: {exc}") from exc
        if not isinstance(case, dict):
            raise ValueError(f"Eval case line {line_number} must be a JSON object.")
        cases.append(case)

    taxonomy = prompt_eval_taxonomy()
    for case in cases:
        validate_prompt_eval_case(case, taxonomy)
    return cases


def prompt_eval_taxonomy() -> PromptEvalTaxonomy:
    """Return deterministic category and tag rows matching FinScope seed order."""
    seed = load_category_seed()
    category_rows = taxonomy_rows_with_ids(
        list(BUILTIN_CATEGORIES),
        seed["categories"],
    )
    tag_rows = taxonomy_rows_with_ids(
        list(BUILTIN_TAGS),
        seed["tags"],
    )
    return PromptEvalTaxonomy(
        category_options=[row["name"] for row in category_rows],
        tag_options=[row["name"] for row in tag_rows],
        category_rows=category_rows,
        tag_rows=tag_rows,
    )


def taxonomy_rows_with_ids(
    builtin_rows: Sequence[Mapping[str, Any]],
    seed_rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Return built-in and seed taxonomy rows with deterministic IDs."""
    rows: list[dict[str, Any]] = []
    seen_names: set[str] = set()
    for row in [*builtin_rows, *seed_rows]:
        name = str(row.get("name") or "").strip()
        if not name or name.casefold() in seen_names:
            continue
        seen_names.add(name.casefold())
        rows.append(
            {
                "id": len(rows) + 1,
                "name": name,
                "description": row.get("description") or "",
                "instruction": row.get("instruction") or "",
            }
        )
    return rows


def validate_prompt_eval_case(case: Mapping[str, Any], taxonomy: PromptEvalTaxonomy | None = None) -> None:
    """Validate one prompt-eval case against the deterministic taxonomy."""
    taxonomy = taxonomy or prompt_eval_taxonomy()
    require_text(case, "id")
    transaction = require_mapping(case, "transaction")
    expected = require_mapping(case, "expected")
    require_text(transaction, "merchant_key")
    require_text(expected, "category")
    expected_category = str(expected["category"])
    if expected_category not in taxonomy.category_options:
        raise ValueError(f"{case['id']}: unknown expected category {expected_category!r}.")

    for tag in expected.get("tags", []):
        if tag not in taxonomy.tag_options:
            raise ValueError(f"{case['id']}: unknown expected tag {tag!r}.")

    for category in transaction.get("candidate_categories", []):
        if category not in taxonomy.category_options:
            raise ValueError(f"{case['id']}: unknown candidate category {category!r}.")
    for tag in transaction.get("candidate_tags", []):
        if tag not in taxonomy.tag_options:
            raise ValueError(f"{case['id']}: unknown candidate tag {tag!r}.")

    if "needs_review" not in expected or not isinstance(expected["needs_review"], bool):
        raise ValueError(f"{case['id']}: expected.needs_review must be a boolean.")


def require_mapping(case: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    """Return a required nested mapping from a case."""
    value = case.get(key)
    if not isinstance(value, dict):
        raise ValueError(f"{case.get('id', '<unknown>')}: {key} must be an object.")
    return value


def require_text(case: Mapping[str, Any], key: str) -> str:
    """Return a required non-empty string from a case."""
    value = str(case.get(key) or "").strip()
    if not value:
        raise ValueError(f"{case.get('id', '<unknown>')}: {key} must be a non-empty string.")
    return value


def build_prompt_eval_messages(
    case: Mapping[str, Any],
    taxonomy: PromptEvalTaxonomy | None = None,
    verify_threshold: float = DEFAULT_VERIFY_THRESHOLD,
    review_threshold: float = DEFAULT_REVIEW_THRESHOLD,
) -> list[dict[str, str]]:
    """Build production LLM messages for one sanitized eval case."""
    taxonomy = taxonomy or prompt_eval_taxonomy()
    return build_llm_messages(
        [prompt_eval_transaction(case)],
        prompt_eval_rules(case),
        taxonomy.category_options,
        taxonomy.tag_options,
        taxonomy.category_rows,
        taxonomy.tag_rows,
        verify_threshold,
        review_threshold,
    )


def prompt_eval_transaction(case: Mapping[str, Any]) -> dict[str, Any]:
    """Return the transaction mapping passed to production prompt builders."""
    transaction = dict(require_mapping(case, "transaction"))
    return {
        "llm_request_id": str(case["id"]),
        "merchant_key": transaction.get("merchant_key"),
        "description": transaction.get("raw_description") or transaction.get("merchant_key"),
        "amount": transaction.get("amount"),
        "transaction_kind": transaction.get("transaction_kind"),
        "category": transaction.get("category") or UNKNOWN_CATEGORY,
        "rule_evidence": transaction.get("rule_evidence"),
        "historical_evidence": transaction.get("historical_evidence"),
        "llm_candidate_categories": transaction.get("candidate_categories"),
        "llm_candidate_tags": transaction.get("candidate_tags"),
    }


def prompt_eval_rules(case: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Return manual rule context from an eval case, if any."""
    rules = []
    for index, rule in enumerate(case.get("manual_rules") or [], start=1):
        rules.append(
            {
                "id": index,
                "keyword": rule["keyword"],
                "category": rule["category"],
                "tags": list(rule.get("tags") or []),
                "direction": rule.get("direction") or "any",
                "source": "manual",
            }
        )
    return rules


def expected_prompt_eval_result(
    case: Mapping[str, Any],
    taxonomy: PromptEvalTaxonomy | None = None,
) -> dict[str, Any]:
    """Return an ID-based ideal result for dry-run harness validation."""
    taxonomy = taxonomy or prompt_eval_taxonomy()
    expected = require_mapping(case, "expected")
    confidence = expected.get("min_confidence", 0.95)
    if expected.get("max_confidence") is not None:
        confidence = min(float(confidence), float(expected["max_confidence"]))
    return {
        "request_id": str(case["id"]),
        "category_id": taxonomy_id(taxonomy.category_rows, str(expected["category"])),
        "tag_ids": [taxonomy_id(taxonomy.tag_rows, str(tag)) for tag in expected.get("tags", [])],
        "confidence": confidence,
        "needs_review": expected["needs_review"],
        "supported_by_similar_transactions": False,
        "reason": str(case.get("rationale") or "Expected eval answer."),
    }


def taxonomy_id(rows: Sequence[Mapping[str, Any]], name: str) -> int:
    """Return the deterministic ID for a taxonomy row name."""
    for row in rows:
        if row["name"] == name:
            return int(row["id"])
    raise ValueError(f"Unknown taxonomy row {name!r}.")


def evaluate_prompt_eval_cases(
    cases: Sequence[Mapping[str, Any]],
    provider: PromptEvalProvider,
    model: str = "unknown-model",
    verify_threshold: float = DEFAULT_VERIFY_THRESHOLD,
    review_threshold: float = DEFAULT_REVIEW_THRESHOLD,
) -> PromptEvalRun:
    """Run and score prompt-eval cases with an injected provider."""
    taxonomy = prompt_eval_taxonomy()
    records: list[PromptEvalRecord] = []
    for case in cases:
        messages = build_prompt_eval_messages(case, taxonomy, verify_threshold, review_threshold)
        raw_output: Any = None
        error = ""
        try:
            raw_output = provider(case, messages)
        except Exception as exc:  # pragma: no cover - exercised by opt-in provider failures.
            error = f"{type(exc).__name__}: {exc}"
        decoded, metrics = score_prompt_eval_result(
            case,
            raw_output,
            taxonomy,
            verify_threshold=verify_threshold,
            provider_error=error,
        )
        records.append(
            PromptEvalRecord(
                case=dict(case),
                messages=messages,
                raw_output=raw_output,
                decoded=decoded,
                metrics=metrics,
                error=error,
            )
        )

    return PromptEvalRun(
        records=records,
        summary=summarize_prompt_eval_records(records),
        metadata={
            "model": model,
            "case_count": len(records),
            "verify_threshold": verify_threshold,
            "review_threshold": review_threshold,
        },
    )


def score_prompt_eval_result(
    case: Mapping[str, Any],
    raw_output: Any,
    taxonomy: PromptEvalTaxonomy,
    verify_threshold: float = DEFAULT_VERIFY_THRESHOLD,
    provider_error: str = "",
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Decode and score one model output against an eval case."""
    expected = require_mapping(case, "expected")
    expected_tags = set(expected.get("tags") or [])
    decoded = decode_prompt_eval_result(raw_output, taxonomy)
    valid = not provider_error and decoded["valid_schema"]
    actual_category = decoded.get("category") if valid else None
    actual_tags = set(decoded.get("tags") or []) if valid else set()
    category_match = actual_category == expected["category"]
    tag_matches = actual_tags == expected_tags
    exact_match = bool(valid and category_match and tag_matches)
    needs_review_match = bool(valid and decoded.get("needs_review") == expected["needs_review"])
    confidence = decoded.get("confidence") if valid else None
    high_confidence_wrong = bool(confidence is not None and confidence >= verify_threshold and not exact_match)
    unsafe_auto_assignment = bool(valid and not decoded.get("needs_review") and not exact_match)

    failure_modes = []
    if provider_error:
        failure_modes.append("provider_error")
    if not decoded["valid_schema"]:
        failure_modes.append("invalid_schema")
    if valid and not category_match:
        failure_modes.append("wrong_category")
    if valid and not tag_matches:
        missing_tags = expected_tags - actual_tags
        extra_tags = actual_tags - expected_tags
        if missing_tags:
            failure_modes.append("missing_tag")
        if extra_tags:
            failure_modes.append("extra_tag")
    if valid and decoded.get("category") == UNKNOWN_CATEGORY and expected["category"] != UNKNOWN_CATEGORY:
        failure_modes.append("false_unknown")
    if valid and decoded.get("category") != UNKNOWN_CATEGORY and expected["category"] == UNKNOWN_CATEGORY:
        failure_modes.append("missed_unknown")
    if valid and not needs_review_match:
        failure_modes.append("needs_review_mismatch")
    if high_confidence_wrong:
        failure_modes.append("high_confidence_wrong")
    if unsafe_auto_assignment:
        failure_modes.append("unsafe_auto_assignment")

    confidence_in_band = confidence_matches_expectation(confidence, expected) if valid else False
    if valid and not confidence_in_band:
        failure_modes.append("confidence_out_of_expected_band")

    metrics = {
        "case_id": case["id"],
        "valid_schema": valid,
        "category_match": exact_match if expected["category"] == UNKNOWN_CATEGORY else category_match,
        "tag_match": tag_matches if valid else False,
        "exact_taxonomy_match": exact_match,
        "needs_review_match": needs_review_match,
        "confidence_in_expected_band": confidence_in_band,
        "high_confidence_wrong": high_confidence_wrong,
        "unsafe_auto_assignment": unsafe_auto_assignment,
        "expected_tags": sorted(expected_tags),
        "actual_tags": sorted(actual_tags),
        "failure_modes": failure_modes,
    }
    return decoded, metrics


def decode_prompt_eval_result(raw_output: Any, taxonomy: PromptEvalTaxonomy) -> dict[str, Any]:
    """Decode one ID-based LLM result into names and validity flags."""
    if not isinstance(raw_output, dict):
        return {"valid_schema": False, "error": "output is not an object"}

    category_by_id = {int(row["id"]): row["name"] for row in taxonomy.category_rows}
    tag_by_id = {int(row["id"]): row["name"] for row in taxonomy.tag_rows}
    category_id = raw_output.get("category_id")
    confidence = raw_output.get("confidence")
    needs_review = raw_output.get("needs_review")
    tag_ids = raw_output.get("tag_ids")
    valid_category_id = type(category_id) is int and category_id in category_by_id
    valid_confidence = (
        isinstance(confidence, (int, float)) and not isinstance(confidence, bool) and 0 <= float(confidence) <= 1
    )
    valid_tag_ids = isinstance(tag_ids, list) and all(type(tag_id) is int and tag_id in tag_by_id for tag_id in tag_ids)
    valid_schema = valid_category_id and valid_confidence and isinstance(needs_review, bool) and valid_tag_ids
    return {
        "valid_schema": valid_schema,
        "request_id": raw_output.get("request_id"),
        "category": category_by_id.get(category_id) if type(category_id) is int else None,
        "tags": [tag_by_id[tag_id] for tag_id in tag_ids] if valid_tag_ids and valid_schema else [],
        "confidence": float(confidence) if valid_confidence else None,
        "needs_review": needs_review if isinstance(needs_review, bool) else None,
        "reason": raw_output.get("reason") if isinstance(raw_output.get("reason"), str) else "",
    }


def confidence_matches_expectation(confidence: float | None, expected: Mapping[str, Any]) -> bool:
    """Return whether confidence falls inside optional expected bands."""
    if confidence is None:
        return False
    minimum = expected.get("min_confidence")
    maximum = expected.get("max_confidence")
    if minimum is not None and confidence < float(minimum):
        return False
    if maximum is not None and confidence > float(maximum):
        return False
    return True


def summarize_prompt_eval_records(records: Sequence[PromptEvalRecord]) -> dict[str, Any]:
    """Return aggregate metrics for scored prompt-eval records."""
    total = len(records)
    valid_count = sum(1 for record in records if record.metrics["valid_schema"])
    expected_tag_total = sum(len(record.metrics["expected_tags"]) for record in records)
    actual_tag_total = sum(len(record.metrics["actual_tags"]) for record in records)
    true_positive_tags = sum(
        len(set(record.metrics["expected_tags"]) & set(record.metrics["actual_tags"])) for record in records
    )
    return {
        "case_count": total,
        "valid_json_rate": safe_rate(valid_count, total),
        "schema_valid_rate": safe_rate(valid_count, total),
        "category_accuracy": safe_rate(sum_metric(records, "category_match"), total),
        "exact_taxonomy_match_rate": safe_rate(sum_metric(records, "exact_taxonomy_match"), total),
        "tag_micro_precision": safe_rate(true_positive_tags, actual_tag_total),
        "tag_micro_recall": safe_rate(true_positive_tags, expected_tag_total),
        "tag_micro_f1": f1_score(
            safe_rate(true_positive_tags, actual_tag_total),
            safe_rate(true_positive_tags, expected_tag_total),
        ),
        "needs_review_accuracy": safe_rate(sum_metric(records, "needs_review_match"), total),
        "confidence_band_rate": safe_rate(sum_metric(records, "confidence_in_expected_band"), total),
        "unsafe_auto_assignment_rate": safe_rate(sum_metric(records, "unsafe_auto_assignment"), total),
        "high_confidence_wrong_rate": safe_rate(sum_metric(records, "high_confidence_wrong"), total),
        "invalid_output_rate": safe_rate(total - valid_count, total),
        "failure_mode_counts": failure_mode_counts(records),
    }


def sum_metric(records: Sequence[PromptEvalRecord], key: str) -> int:
    """Count truthy record metrics."""
    return sum(1 for record in records if record.metrics.get(key))


def safe_rate(numerator: int | float, denominator: int | float) -> float:
    """Return a safe division rate."""
    return float(numerator) / float(denominator) if denominator else 0.0


def f1_score(precision: float, recall: float) -> float:
    """Return F1 from precision and recall."""
    return 0.0 if precision + recall == 0 else (2 * precision * recall) / (precision + recall)


def failure_mode_counts(records: Sequence[PromptEvalRecord]) -> dict[str, int]:
    """Count failure-mode labels across records."""
    counts: dict[str, int] = {}
    for record in records:
        for failure_mode in record.metrics["failure_modes"]:
            counts[failure_mode] = counts.get(failure_mode, 0) + 1
    return dict(sorted(counts.items()))


def render_prompt_eval_report(run: PromptEvalRun) -> str:
    """Render a Markdown report for one prompt eval run."""
    summary = run.summary
    lines = [
        "# LLM Categorization Prompt Eval Report",
        "",
        "## Run summary",
        "",
        f"- Model: `{run.metadata['model']}`",
        f"- Examples: {run.metadata['case_count']}",
        f"- Verify threshold: {run.metadata['verify_threshold']:.2f}",
        f"- Review threshold: {run.metadata['review_threshold']:.2f}",
        "",
        "## Headline metrics",
        "",
        "| Metric | Value |",
        "| --- | ---: |",
    ]
    for key in (
        "schema_valid_rate",
        "category_accuracy",
        "exact_taxonomy_match_rate",
        "tag_micro_f1",
        "needs_review_accuracy",
        "confidence_band_rate",
        "unsafe_auto_assignment_rate",
        "high_confidence_wrong_rate",
        "invalid_output_rate",
    ):
        lines.append(f"| {key} | {format_metric(summary[key])} |")

    lines.extend(["", "## Failure modes", ""])
    if summary["failure_mode_counts"]:
        for key, count in summary["failure_mode_counts"].items():
            lines.append(f"- {key}: {count}")
    else:
        lines.append("- None")

    lines.extend(["", "## Case results", ""])
    lines.append("| Case | Expected | Actual | Review | Failures |")
    lines.append("| --- | --- | --- | --- | --- |")
    for record in run.records:
        expected = require_mapping(record.case, "expected")
        actual_category = record.decoded.get("category") or "(invalid)"
        actual_tags = ", ".join(record.decoded.get("tags") or [])
        expected_tags = ", ".join(expected.get("tags") or [])
        expected_text = f"{expected['category']} [{expected_tags}]"
        actual_text = f"{actual_category} [{actual_tags}]"
        review = record.decoded.get("needs_review")
        failures = ", ".join(record.metrics["failure_modes"]) or "none"
        lines.append(f"| `{record.case['id']}` | {expected_text} | {actual_text} | {review} | {failures} |")
    return "\n".join(lines) + "\n"


def format_metric(value: Any) -> str:
    """Return a report-friendly metric string."""
    return f"{value:.6f}" if isinstance(value, float) else str(value)


def write_prompt_eval_artifacts(output_dir: str | Path, run: PromptEvalRun) -> None:
    """Write raw outputs and a Markdown report for an eval run."""
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    (output_path / "report.md").write_text(render_prompt_eval_report(run), encoding="utf-8")
    with (output_path / "raw_outputs.jsonl").open("w", encoding="utf-8") as handle:
        for record in run.records:
            handle.write(
                json.dumps(
                    {
                        "case_id": record.case["id"],
                        "raw_output": record.raw_output,
                        "decoded": record.decoded,
                        "metrics": record.metrics,
                        "error": record.error,
                    },
                    ensure_ascii=True,
                    sort_keys=True,
                )
                + "\n"
            )
