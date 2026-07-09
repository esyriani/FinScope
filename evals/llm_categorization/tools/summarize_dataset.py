"""Summarize validated offline LLM categorization datasets.

The utility validates JSONL examples with the shared eval schema, then reports
coverage and methodology risks for curation and split review. It does not import
FinScope runtime modules, inspect databases, call model providers, or score
prompt outputs.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from evals.llm_categorization.tools import validate_dataset
from evals.llm_categorization.tools.io_utils import JsonlError, read_jsonl
from evals.llm_categorization.tools.schemas import (
    UNKNOWN_CATEGORY_NAME,
    DatasetValidationError,
    EvaluationExample,
    validate_evaluation_example,
)

HIGH_TRUST_LABEL_SOURCES = frozenset(
    {
        "manual_edit",
        "reviewed",
        "high_confidence_rule",
        "stable_history",
        "curated_by_researcher",
    }
)
IMPORTANT_AMBIGUITY_TYPES = (
    "transfer_like",
    "income_like",
    "reimbursement_like",
    "reimbursable_like",
    "rental_like",
    "tax_like",
)


@dataclass(frozen=True)
class DatasetCurationSummary:
    """Represent curation-oriented coverage counts for one dataset or split."""

    path: Path
    example_count: int
    category_counts: Counter[str]
    tag_counts: Counter[str]
    direction_counts: Counter[str]
    needs_review_counts: Counter[str]
    expected_unknown_count: int | None
    label_source_counts: Counter[str]
    privacy_level_counts: Counter[str]
    ambiguity_type_counts: Counter[str]
    statement_type_counts: Counter[str]
    high_trust_label_count: int
    low_trust_label_count: int
    missing_categories: tuple[str, ...]
    missing_tags: tuple[str, ...]
    warnings: tuple[str, ...]


def load_validated_records(path: Path) -> tuple[list[dict[str, Any]], list[EvaluationExample]]:
    """Return raw JSONL records and validated examples after strict dataset validation."""
    validate_dataset.validate_dataset(path)

    records: list[dict[str, Any]] = []
    examples: list[EvaluationExample] = []
    for line_number, payload in read_jsonl(path):
        records.append(payload)
        examples.append(validate_evaluation_example(payload, line_number=line_number))
    return records, examples


def summarize_examples(
    path: Path,
    examples: Sequence[EvaluationExample],
    *,
    reference_examples: Sequence[EvaluationExample] | None = None,
) -> DatasetCurationSummary:
    """Build a curation summary for validated examples.

    When ``reference_examples`` is provided, warnings are emitted for important
    strata present in the reference dataset but absent from this subset.
    """
    category_counts: Counter[str] = Counter()
    tag_counts: Counter[str] = Counter()
    direction_counts: Counter[str] = Counter()
    needs_review_counts: Counter[str] = Counter()
    label_source_counts: Counter[str] = Counter()
    privacy_level_counts: Counter[str] = Counter()
    ambiguity_type_counts: Counter[str] = Counter()
    statement_type_counts: Counter[str] = Counter()
    expected_unknown_count = 0
    unknown_category_present = False
    high_trust_label_count = 0

    for example in examples:
        category_name = (
            example.candidate_taxonomy.category_name(example.expected.category_id) or example.expected.category_id
        )
        category_counts[validate_dataset.coverage_label(category_name, example.expected.category_id)] += 1
        for tag_id in example.expected.tag_ids:
            tag_name = example.candidate_taxonomy.tag_name(tag_id) or tag_id
            tag_counts[validate_dataset.coverage_label(tag_name, tag_id)] += 1
        direction_counts[example.coverage.direction] += 1
        needs_review_counts[str(example.expected.needs_review).lower()] += 1
        label_source_counts[example.label_source] += 1
        privacy_level_counts[example.privacy_level] += 1
        ambiguity_type_counts[example.coverage.ambiguity_type or "null"] += 1
        statement_type_counts[example.coverage.statement_type or "null"] += 1
        if example.candidate_taxonomy.has_unknown_category():
            unknown_category_present = True
        if category_name == UNKNOWN_CATEGORY_NAME:
            expected_unknown_count += 1
        if example.label_source in HIGH_TRUST_LABEL_SOURCES:
            high_trust_label_count += 1

    missing_categories, missing_tags = missing_taxonomy_coverage(examples, category_counts, tag_counts)
    warnings = tuple(
        dict.fromkeys(
            (
                *validate_dataset.methodology_warnings(
                    path,
                    examples,
                    category_counts,
                    tag_counts,
                    expected_unknown_count,
                ),
                *split_methodology_warnings(examples, reference_examples or examples, expected_unknown_count),
            )
        )
    )
    return DatasetCurationSummary(
        path=path,
        example_count=len(examples),
        category_counts=category_counts,
        tag_counts=tag_counts,
        direction_counts=direction_counts,
        needs_review_counts=needs_review_counts,
        expected_unknown_count=expected_unknown_count if unknown_category_present else None,
        label_source_counts=label_source_counts,
        privacy_level_counts=privacy_level_counts,
        ambiguity_type_counts=ambiguity_type_counts,
        statement_type_counts=statement_type_counts,
        high_trust_label_count=high_trust_label_count,
        low_trust_label_count=len(examples) - high_trust_label_count,
        missing_categories=missing_categories,
        missing_tags=missing_tags,
        warnings=warnings,
    )


def missing_taxonomy_coverage(
    examples: Sequence[EvaluationExample],
    category_counts: Counter[str],
    tag_counts: Counter[str],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Return candidate taxonomy values that are not represented by expected labels."""
    candidate_categories = {
        validate_dataset.coverage_label(item.name, item.id)
        for example in examples
        for item in example.candidate_taxonomy.categories
    }
    candidate_tags = {
        validate_dataset.coverage_label(item.name, item.id)
        for example in examples
        for item in example.candidate_taxonomy.tags
    }
    return tuple(sorted(candidate_categories - set(category_counts))), tuple(sorted(candidate_tags - set(tag_counts)))


def split_methodology_warnings(
    examples: Sequence[EvaluationExample],
    reference_examples: Sequence[EvaluationExample],
    expected_unknown_count: int,
) -> tuple[str, ...]:
    """Return warnings for important strata missing from a dataset or split."""
    warnings: list[str] = []
    if not examples:
        return ("no examples",)

    reference_has_unknown = any(is_expected_unknown(example) for example in reference_examples)
    reference_has_tags = any(example.expected.tag_ids for example in reference_examples)
    reference_has_no_tags = any(not example.expected.tag_ids for example in reference_examples)

    warnings.extend(
        missing_value_warnings(
            "direction", example_values(examples, "direction"), reference_values(reference_examples, "direction")
        )
    )
    warnings.extend(
        missing_value_warnings(
            "needs_review",
            {str(example.expected.needs_review).lower() for example in examples},
            {str(example.expected.needs_review).lower() for example in reference_examples},
        )
    )
    warnings.extend(
        missing_value_warnings(
            "label_source",
            {example.label_source for example in examples},
            {example.label_source for example in reference_examples},
        )
    )
    warnings.extend(
        missing_value_warnings(
            "ambiguity_type",
            {example.coverage.ambiguity_type or "null" for example in examples},
            {example.coverage.ambiguity_type or "null" for example in reference_examples},
        )
    )
    warnings.extend(
        missing_value_warnings(
            "statement_type",
            {example.coverage.statement_type or "null" for example in examples},
            {example.coverage.statement_type or "null" for example in reference_examples},
        )
    )

    if reference_has_unknown and expected_unknown_count == 0:
        warnings.append("no expected UNKNOWN examples represented from source")
    if reference_has_tags and not any(example.expected.tag_ids for example in examples):
        warnings.append("no tag-required examples represented from source")
    if reference_has_no_tags and not any(not example.expected.tag_ids for example in examples):
        warnings.append("no examples with empty expected tags represented from source")
    if not any(example.label_source in HIGH_TRUST_LABEL_SOURCES for example in examples):
        warnings.append("no high-trust labels")

    represented_ambiguity = {example.coverage.ambiguity_type for example in examples}
    reference_ambiguity = {example.coverage.ambiguity_type for example in reference_examples}
    for ambiguity_type in IMPORTANT_AMBIGUITY_TYPES:
        if ambiguity_type in reference_ambiguity and ambiguity_type not in represented_ambiguity:
            warnings.append(f"no {ambiguity_type} cases represented from source")

    return tuple(warnings)


def missing_value_warnings(label: str, values: set[str], reference_values_: set[str]) -> list[str]:
    """Return warnings for source values absent from this dataset or split."""
    missing_values = sorted(reference_values_ - values)
    return [f"missing {label} value represented in source: {value}" for value in missing_values]


def example_values(examples: Sequence[EvaluationExample], field_name: str) -> set[str]:
    """Return supported coverage values by field name."""
    if field_name == "direction":
        return {example.coverage.direction for example in examples}
    raise ValueError(f"unsupported field_name: {field_name}")


def reference_values(examples: Sequence[EvaluationExample], field_name: str) -> set[str]:
    """Return supported reference coverage values by field name."""
    return example_values(examples, field_name)


def is_expected_unknown(example: EvaluationExample) -> bool:
    """Return whether the expected category is the UNKNOWN built-in."""
    category_name = example.candidate_taxonomy.category_name(example.expected.category_id)
    return category_name == UNKNOWN_CATEGORY_NAME


def format_dataset_summary(summary: DatasetCurationSummary) -> str:
    """Render a curation summary as concise Markdown."""
    lines = [
        f"## Dataset summary: {summary.path}",
        "",
        f"- Examples: {summary.example_count}",
        f"- Examples needing review: {summary.needs_review_counts.get('true', 0)}",
        f"- High-trust labels: {summary.high_trust_label_count}",
        f"- Low-trust labels: {summary.low_trust_label_count}",
    ]
    if summary.expected_unknown_count is not None:
        lines.append(f"- Expected UNKNOWN: {summary.expected_unknown_count}")

    lines.extend(
        [
            "",
            "### Category coverage",
            *format_counter_lines(summary.category_counts),
            "",
            "### Tag coverage",
            *format_counter_lines(summary.tag_counts, empty_label="(none)"),
            "",
            "### Directions",
            *format_counter_lines(summary.direction_counts),
            "",
            "### needs_review",
            *format_counter_lines(summary.needs_review_counts),
            "",
            "### Label sources",
            *format_counter_lines(summary.label_source_counts),
            "",
            "### Privacy levels",
            *format_counter_lines(summary.privacy_level_counts),
            "",
            "### Ambiguity types",
            *format_counter_lines(summary.ambiguity_type_counts),
            "",
            "### Statement types",
            *format_counter_lines(summary.statement_type_counts),
            "",
            "### Missing categories",
            *format_sequence_lines(summary.missing_categories),
            "",
            "### Missing tags",
            *format_sequence_lines(summary.missing_tags),
        ]
    )
    if summary.warnings:
        lines.extend(["", "### Warnings", *[f"- {warning}" for warning in summary.warnings]])
    return "\n".join(lines)


def format_counter_lines(counter: Counter[str], *, empty_label: str = "(empty)") -> list[str]:
    """Render a counter as sorted Markdown bullet lines."""
    if not counter:
        return [f"- {empty_label}: 0"]
    return [f"- {name}: {count}" for name, count in sorted(counter.items())]


def format_sequence_lines(values: Sequence[str]) -> list[str]:
    """Render a sequence as sorted Markdown bullet lines."""
    if not values:
        return ["- (none)"]
    return [f"- {value}" for value in values]


def build_parser() -> argparse.ArgumentParser:
    """Create the command-line parser."""
    parser = argparse.ArgumentParser(description="Summarize a validated FinScope LLM categorization eval dataset.")
    parser.add_argument("--input", required=True, type=Path, help="Path to the JSONL dataset to summarize.")
    parser.add_argument("--out", type=Path, help="Optional Markdown summary output path.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the dataset summary CLI."""
    parser = build_parser()
    args = parser.parse_args(argv)
    from evals.llm_categorization.services.dataset_service import read_dataset_summary

    try:
        summary = read_dataset_summary(args.input)
    except (DatasetValidationError, JsonlError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    report = format_dataset_summary(summary)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(f"{report}\n", encoding="utf-8", newline="\n")
    else:
        print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
