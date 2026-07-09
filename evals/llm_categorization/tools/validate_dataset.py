"""CLI validation for offline LLM categorization JSONL datasets.

The validator checks curated examples against the local eval schema only. It
does not import FinScope application modules, open runtime databases, call
external services, or score model outputs.
"""

import argparse
import sys
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from evals.llm_categorization.tools.io_utils import JsonlError, read_jsonl
from evals.llm_categorization.tools.schemas import (
    BUILTIN_CONCEPT_NAMES,
    UNKNOWN_CATEGORY_NAME,
    DatasetValidationError,
    EvaluationExample,
    validate_evaluation_example,
)

MIN_BENCHMARK_EXAMPLES = 80
MIN_VALIDATION_OR_TEST_EXAMPLES = 20


@dataclass(frozen=True)
class DatasetSummary:
    """Represent a concise validation summary for a JSONL dataset."""

    path: Path
    example_count: int
    unique_request_id_count: int
    category_coverage: Counter[str]
    tag_coverage: Counter[str]
    label_source_counts: Counter[str]
    privacy_level_counts: Counter[str]
    direction_counts: Counter[str]
    needs_review_counts: Counter[str]
    expected_unknown_count: int | None
    ambiguity_type_counts: Counter[str]
    statement_type_counts: Counter[str]
    warnings: tuple[str, ...]


def validate_dataset(path: Path) -> DatasetSummary:
    """Validate a JSONL dataset and return aggregate dataset counts."""
    examples: list[EvaluationExample] = []
    validation_errors: list[str] = []
    try:
        for line_number, payload in read_jsonl(path):
            try:
                examples.append(validate_evaluation_example(payload, line_number=line_number))
            except DatasetValidationError as exc:
                validation_errors.append(str(exc))
    except JsonlError:
        raise

    validation_errors.extend(duplicate_request_id_errors(examples))
    if validation_errors:
        raise DatasetValidationError(format_validation_errors(validation_errors))
    if not examples:
        raise DatasetValidationError("dataset contains no examples")
    return summarize_examples(path, examples)


def duplicate_request_id_errors(examples: Sequence[EvaluationExample]) -> list[str]:
    """Return validation errors for duplicate request IDs."""
    lines_by_request_id: dict[str, list[int]] = defaultdict(list)
    for example in examples:
        if example.line_number is not None:
            lines_by_request_id[example.request_id].append(example.line_number)

    errors = []
    for request_id, line_numbers in sorted(lines_by_request_id.items()):
        if len(line_numbers) < 2:
            continue
        first_line = line_numbers[0]
        for duplicate_line in line_numbers[1:]:
            errors.append(
                f"line {duplicate_line}, request {request_id}: duplicate request_id; first seen on line {first_line}"
            )
    return errors


def summarize_examples(path: Path, examples: Sequence[EvaluationExample]) -> DatasetSummary:
    """Build aggregate counts for validated examples."""
    category_coverage: Counter[str] = Counter()
    tag_coverage: Counter[str] = Counter()
    label_source_counts: Counter[str] = Counter()
    privacy_level_counts: Counter[str] = Counter()
    direction_counts: Counter[str] = Counter()
    needs_review_counts: Counter[str] = Counter()
    ambiguity_type_counts: Counter[str] = Counter()
    statement_type_counts: Counter[str] = Counter()

    unknown_category_present = False
    expected_unknown_count = 0
    for example in examples:
        category_name = (
            example.candidate_taxonomy.category_name(example.expected.category_id) or example.expected.category_id
        )
        category_coverage[coverage_label(category_name, example.expected.category_id)] += 1
        for tag_id in example.expected.tag_ids:
            tag_name = example.candidate_taxonomy.tag_name(tag_id) or tag_id
            tag_coverage[coverage_label(tag_name, tag_id)] += 1

        label_source_counts[example.label_source] += 1
        privacy_level_counts[example.privacy_level] += 1
        direction_counts[example.coverage.direction] += 1
        needs_review_counts[str(example.expected.needs_review).lower()] += 1
        ambiguity_type_counts[example.coverage.ambiguity_type or "null"] += 1
        statement_type_counts[example.coverage.statement_type or "null"] += 1

        if example.candidate_taxonomy.has_unknown_category():
            unknown_category_present = True
        if category_name == UNKNOWN_CATEGORY_NAME:
            expected_unknown_count += 1

    warnings = methodology_warnings(path, examples, category_coverage, tag_coverage, expected_unknown_count)
    return DatasetSummary(
        path=path,
        example_count=len(examples),
        unique_request_id_count=len({example.request_id for example in examples}),
        category_coverage=category_coverage,
        tag_coverage=tag_coverage,
        label_source_counts=label_source_counts,
        privacy_level_counts=privacy_level_counts,
        direction_counts=direction_counts,
        needs_review_counts=needs_review_counts,
        expected_unknown_count=expected_unknown_count if unknown_category_present else None,
        ambiguity_type_counts=ambiguity_type_counts,
        statement_type_counts=statement_type_counts,
        warnings=warnings,
    )


def methodology_warnings(
    path: Path,
    examples: Sequence[EvaluationExample],
    category_coverage: Counter[str],
    tag_coverage: Counter[str],
    expected_unknown_count: int,
) -> tuple[str, ...]:
    """Return non-blocking methodology risk warnings for a valid dataset."""
    warnings = []
    if len(examples) < MIN_BENCHMARK_EXAMPLES:
        warnings.append(f"fewer than {MIN_BENCHMARK_EXAMPLES} examples: {len(examples)}")
    if expected_unknown_count == 0:
        warnings.append("no expected UNKNOWN examples")
    if not any(example.expected.needs_review for example in examples):
        warnings.append("no needs_review: true examples")
    if not any(example.expected.tag_ids for example in examples):
        warnings.append("no tag-required examples")
    if not any(not example.expected.tag_ids for example in examples):
        warnings.append("no examples with empty expected tags")
    if not any(example.coverage.direction == "debit" for example in examples):
        warnings.append("no debit examples")
    if not any(example.coverage.direction == "credit" for example in examples):
        warnings.append("no credit examples")
    warnings.extend(benchmark_strata_warnings(examples))

    warnings.extend(builtin_concept_warnings(examples))
    warnings.extend(candidate_coverage_gap_warnings(examples, category_coverage, tag_coverage))

    dataset_role = infer_dataset_role(path)
    if dataset_role is not None and len(examples) < MIN_VALIDATION_OR_TEST_EXAMPLES:
        warnings.append(
            f"too few examples for a {dataset_role} set: {len(examples)} "
            f"(recommended at least {MIN_VALIDATION_OR_TEST_EXAMPLES})"
        )
    return tuple(warnings)


def benchmark_strata_warnings(examples: Sequence[EvaluationExample]) -> list[str]:
    """Return warnings for missing methodology benchmark strata."""
    ambiguity_types = {example.coverage.ambiguity_type for example in examples}
    concept_names = candidate_concept_names(examples)
    warnings = []
    if "transfer_like" not in ambiguity_types:
        warnings.append("no transfer-like cases")
    if "income_like" not in ambiguity_types:
        warnings.append("no income-like cases")
    if {"Reimbursement", "Reimbursable"} & concept_names and not (
        {"reimbursement_like", "reimbursable_like"} & ambiguity_types
    ):
        warnings.append("no reimbursement-like or reimbursable-like cases")
    if "Rental" in concept_names and "rental_like" not in ambiguity_types:
        warnings.append("no rental-like cases")
    if "Tax" in concept_names and "tax_like" not in ambiguity_types:
        warnings.append("no tax-like cases")
    return warnings


def candidate_concept_names(examples: Sequence[EvaluationExample]) -> set[str]:
    """Return category and tag names present in candidate taxonomies."""
    return {
        item.name
        for example in examples
        for item in (*example.candidate_taxonomy.categories, *example.candidate_taxonomy.tags)
    }


def builtin_concept_warnings(examples: Sequence[EvaluationExample]) -> list[str]:
    """Return warnings for built-in concepts present but unrepresented."""
    candidate_builtins: set[str] = set()
    represented_concepts: set[str] = set()
    for example in examples:
        category_name = example.candidate_taxonomy.category_name(example.expected.category_id)
        if category_name:
            represented_concepts.add(category_name)
        represented_concepts.update(
            tag_name
            for tag_id in example.expected.tag_ids
            if (tag_name := example.candidate_taxonomy.tag_name(tag_id)) is not None
        )
        if example.coverage.category:
            represented_concepts.add(example.coverage.category)
        represented_concepts.update(example.coverage.tags)
        candidate_builtins.update(
            item.name
            for item in (*example.candidate_taxonomy.categories, *example.candidate_taxonomy.tags)
            if item.name in BUILTIN_CONCEPT_NAMES
        )

    missing_builtins = sorted(candidate_builtins - represented_concepts)
    if not missing_builtins:
        return []
    return [f"missing built-in concept coverage: {', '.join(missing_builtins)}"]


def candidate_coverage_gap_warnings(
    examples: Sequence[EvaluationExample],
    category_coverage: Counter[str],
    tag_coverage: Counter[str],
) -> list[str]:
    """Return warnings for candidate categories or tags never used as expected labels."""
    expected_category_labels = set(category_coverage)
    expected_tag_labels = set(tag_coverage)
    candidate_category_labels = {
        coverage_label(item.name, item.id) for example in examples for item in example.candidate_taxonomy.categories
    }
    candidate_tag_labels = {
        coverage_label(item.name, item.id) for example in examples for item in example.candidate_taxonomy.tags
    }

    warnings = []
    category_gaps = sorted(candidate_category_labels - expected_category_labels)
    if category_gaps:
        warnings.append(f"categories with no examples: {summarize_gap_labels(category_gaps)}")
    tag_gaps = sorted(candidate_tag_labels - expected_tag_labels)
    if tag_gaps:
        warnings.append(f"tags with no examples: {summarize_gap_labels(tag_gaps)}")
    return warnings


def infer_dataset_role(path: Path) -> str | None:
    """Infer whether a dataset path looks like a validation or test set."""
    tokens = {token.lower() for token in path.stem.replace("-", "_").split("_")}
    if tokens & {"validation", "valid", "val"}:
        return "validation"
    if tokens & {"test", "held", "heldout"}:
        return "test"
    return None


def coverage_label(name: str, item_id: str) -> str:
    """Return a stable label for coverage summaries."""
    return f"{name} ({item_id})"


def summarize_gap_labels(labels: Sequence[str]) -> str:
    """Return a bounded human-readable list of coverage gaps."""
    preview = ", ".join(labels[:10])
    remaining = len(labels) - 10
    if remaining > 0:
        return f"{len(labels)} candidate value(s) never expected: {preview}, ... {remaining} more"
    return f"{len(labels)} candidate value(s) never expected: {preview}"


def format_validation_errors(validation_errors: Sequence[str]) -> str:
    """Render validation errors with line numbers and a bounded preview."""
    preview = "\n".join(f"- {error}" for error in validation_errors[:20])
    remaining = len(validation_errors) - 20
    if remaining > 0:
        preview = f"{preview}\n- ... {remaining} more error(s)"
    return f"{len(validation_errors)} validation error(s):\n{preview}"


def format_summary(summary: DatasetSummary) -> str:
    """Render a concise human-readable validation summary."""
    lines = [
        f"Validated {summary.example_count} example(s): {summary.path}",
        f"Unique request IDs: {summary.unique_request_id_count}",
        "Category coverage:",
        *format_counter_lines(summary.category_coverage),
        "Tag coverage:",
        *format_counter_lines(summary.tag_coverage, empty_label="(none)"),
        "Label sources:",
        *format_counter_lines(summary.label_source_counts),
        "Privacy levels:",
        *format_counter_lines(summary.privacy_level_counts),
        "Directions:",
        *format_counter_lines(summary.direction_counts),
        "needs_review:",
        *format_counter_lines(summary.needs_review_counts),
    ]
    if summary.expected_unknown_count is not None:
        lines.append(f"Expected UNKNOWN: {summary.expected_unknown_count}")
    lines.extend(
        [
            "Ambiguity types:",
            *format_counter_lines(summary.ambiguity_type_counts),
            "Statement types:",
            *format_counter_lines(summary.statement_type_counts),
        ]
    )
    if summary.warnings:
        lines.append("Warnings:")
        lines.extend(f"- {warning}" for warning in summary.warnings)
    return "\n".join(lines)


def format_counter_lines(counter: Counter[str], *, empty_label: str = "(empty)") -> list[str]:
    """Render a counter as sorted indented summary lines."""
    if not counter:
        return [f"  {empty_label}: 0"]
    return [f"  {name}: {count}" for name, count in sorted(counter.items())]


def build_parser() -> argparse.ArgumentParser:
    """Create the command-line parser."""
    parser = argparse.ArgumentParser(description="Validate a FinScope LLM categorization eval JSONL dataset.")
    parser.add_argument("jsonl_path", type=Path, help="Path to the JSONL dataset to validate.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the dataset validator CLI."""
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        summary = validate_dataset(args.jsonl_path)
    except (DatasetValidationError, JsonlError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(format_summary(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
