"""Create deterministic development, validation, and test dataset splits.

The splitter operates only on already validated offline eval JSONL files. It
keeps likely near-duplicates together, avoids known source-example leakage, and
balances curation strata as much as possible without calling model providers or
touching FinScope runtime data.
"""

from __future__ import annotations

import argparse
import math
import random
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from evals.llm_categorization.tools import validate_dataset
from evals.llm_categorization.tools.io_utils import JsonlError, write_jsonl
from evals.llm_categorization.tools.schemas import UNKNOWN_CATEGORY_NAME, DatasetValidationError, EvaluationExample
from evals.llm_categorization.tools.summarize_dataset import (
    IMPORTANT_AMBIGUITY_TYPES,
    format_dataset_summary,
    load_validated_records,
    summarize_examples,
)

SPLIT_NAMES = ("dev", "validation", "test")
SOURCE_REQUEST_ID_RE = re.compile(r"\bsource_request_id\s*[:=]\s*([A-Za-z0-9_.:-]+)\b", re.IGNORECASE)
NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True)
class ExampleRecord:
    """Pair a raw JSONL record with its validated example object."""

    record: dict[str, Any]
    example: EvaluationExample


@dataclass(frozen=True)
class SplitGroup:
    """Represent one leakage group that must stay in a single split."""

    group_key: str
    bundles: tuple[ExampleRecord, ...]
    feature_counts: Counter[str]

    @property
    def size(self) -> int:
        """Return the number of examples in this group."""
        return len(self.bundles)


@dataclass
class SplitBucket:
    """Track the mutable assignment state for one output split."""

    name: str
    ratio: float
    target_count: int
    bundles: list[ExampleRecord] = field(default_factory=list)
    feature_counts: Counter[str] = field(default_factory=Counter)
    group_keys: list[str] = field(default_factory=list)

    def add_group(self, group: SplitGroup) -> None:
        """Assign a leakage group to this split."""
        self.bundles.extend(group.bundles)
        self.feature_counts.update(group.feature_counts)
        self.group_keys.append(group.group_key)


class UnionFind:
    """Track request IDs that must remain in the same split."""

    def __init__(self, request_ids: Sequence[str]) -> None:
        """Initialize each request ID as its own group."""
        self.parents = {request_id: request_id for request_id in request_ids}

    def find(self, request_id: str) -> str:
        """Return the canonical parent for a request ID."""
        parent = self.parents[request_id]
        if parent != request_id:
            self.parents[request_id] = self.find(parent)
        return self.parents[request_id]

    def union(self, first_request_id: str, second_request_id: str) -> None:
        """Merge two request-ID groups."""
        first_parent = self.find(first_request_id)
        second_parent = self.find(second_request_id)
        if first_parent == second_parent:
            return
        if second_parent < first_parent:
            first_parent, second_parent = second_parent, first_parent
        self.parents[second_parent] = first_parent


def split_records(
    records: Sequence[dict[str, Any]],
    examples: Sequence[EvaluationExample],
    *,
    ratios: dict[str, float],
    seed: int,
) -> tuple[dict[str, list[dict[str, Any]]], tuple[SplitBucket, ...], tuple[SplitGroup, ...]]:
    """Split records into deterministic leakage-aware buckets."""
    if len(records) != len(examples):
        raise ValueError("records and examples must have the same length")
    if len(examples) < len(SPLIT_NAMES):
        raise ValueError("at least 3 examples are required to create non-empty dev, validation, and test splits")

    groups = build_split_groups(records, examples)
    targets = target_counts(len(examples), ratios)
    buckets = tuple(SplitBucket(name=name, ratio=ratios[name], target_count=targets[name]) for name in SPLIT_NAMES)
    assign_groups(groups, buckets, seed=seed)
    split_records_by_name = {
        bucket.name: [bundle.record for bundle in sorted(bucket.bundles, key=lambda bundle: bundle.example.request_id)]
        for bucket in buckets
    }
    return split_records_by_name, buckets, groups


def build_split_groups(
    records: Sequence[dict[str, Any]], examples: Sequence[EvaluationExample]
) -> tuple[SplitGroup, ...]:
    """Build leakage groups from near-duplicate, merchant/amount, and source-link keys."""
    bundles = [ExampleRecord(record=record, example=example) for record, example in zip(records, examples)]
    request_ids = [bundle.example.request_id for bundle in bundles]
    request_id_set = set(request_ids)
    union_find = UnionFind(request_ids)
    keyed_request_ids: defaultdict[str, list[str]] = defaultdict(list)

    for bundle in bundles:
        example = bundle.example
        keyed_request_ids[near_duplicate_key(example)].append(example.request_id)
        merchant_key = merchant_amount_key(example)
        if merchant_key is not None:
            keyed_request_ids[merchant_key].append(example.request_id)
        source_request_id = source_request_id_from_notes(example.notes)
        if source_request_id in request_id_set:
            union_find.union(example.request_id, source_request_id)

    for grouped_request_ids in keyed_request_ids.values():
        if len(grouped_request_ids) < 2:
            continue
        first_request_id = grouped_request_ids[0]
        for request_id in grouped_request_ids[1:]:
            union_find.union(first_request_id, request_id)

    bundles_by_parent: defaultdict[str, list[ExampleRecord]] = defaultdict(list)
    for bundle in bundles:
        bundles_by_parent[union_find.find(bundle.example.request_id)].append(bundle)

    groups = []
    for parent_id, grouped_bundles in sorted(bundles_by_parent.items()):
        ordered_bundles = tuple(sorted(grouped_bundles, key=lambda bundle: bundle.example.request_id))
        feature_counts: Counter[str] = Counter()
        for bundle in ordered_bundles:
            feature_counts.update(example_features(bundle.example))
        groups.append(SplitGroup(group_key=parent_id, bundles=ordered_bundles, feature_counts=feature_counts))
    return tuple(groups)


def assign_groups(groups: Sequence[SplitGroup], buckets: Sequence[SplitBucket], *, seed: int) -> None:
    """Assign groups to buckets with deterministic coverage balancing."""
    rng = random.Random(seed)
    global_feature_counts: Counter[str] = Counter()
    for group in groups:
        global_feature_counts.update(group.feature_counts)

    randomized_tiebreakers = {group.group_key: rng.random() for group in groups}
    ordered_groups = sorted(
        groups,
        key=lambda group: (-group_rarity(group, global_feature_counts), randomized_tiebreakers[group.group_key]),
    )
    for group in ordered_groups:
        scored_buckets = [
            (bucket_score(bucket, group, global_feature_counts, len(buckets)), bucket.name, bucket)
            for bucket in buckets
        ]
        _, _, selected_bucket = min(scored_buckets, key=lambda item: (item[0], item[1]))
        selected_bucket.add_group(group)


def bucket_score(
    bucket: SplitBucket,
    group: SplitGroup,
    global_feature_counts: Counter[str],
    split_count: int,
) -> float:
    """Return a lower-is-better assignment score for adding a group to a bucket."""
    new_count = len(bucket.bundles) + group.size
    target_count = max(bucket.target_count, 1)
    size_penalty = abs(new_count - bucket.target_count) / target_count
    overfill_penalty = max(0, new_count - bucket.target_count) / target_count

    coverage_reward = 0.0
    for feature, group_count in group.feature_counts.items():
        desired_count = desired_feature_count(global_feature_counts[feature], bucket.ratio, split_count)
        if desired_count <= 0:
            continue
        current_count = bucket.feature_counts[feature]
        if current_count >= desired_count:
            continue
        feature_rarity_weight = 1.0 + (1.0 / max(global_feature_counts[feature], 1))
        coverage_reward += min(group_count, desired_count - current_count) * feature_rarity_weight
        if current_count == 0:
            coverage_reward += 0.5 * feature_rarity_weight

    return (3.0 * size_penalty) + (5.0 * overfill_penalty) - coverage_reward


def desired_feature_count(global_count: int, ratio: float, split_count: int) -> int:
    """Return the approximate feature count desired in one split."""
    if global_count <= 0:
        return 0
    if global_count < split_count:
        return 1
    return max(1, int(round(global_count * ratio)))


def group_rarity(group: SplitGroup, global_feature_counts: Counter[str]) -> float:
    """Return a larger score for groups containing scarce features."""
    return sum(count / max(global_feature_counts[feature], 1) for feature, count in group.feature_counts.items())


def target_counts(total_count: int, ratios: dict[str, float]) -> dict[str, int]:
    """Convert ratios to deterministic integer target counts."""
    raw_targets = {name: total_count * ratios[name] for name in SPLIT_NAMES}
    targets = {name: math.floor(raw_targets[name]) for name in SPLIT_NAMES}
    remaining = total_count - sum(targets.values())
    remainder_order = sorted(
        SPLIT_NAMES,
        key=lambda name: (raw_targets[name] - targets[name], ratios[name], name),
        reverse=True,
    )
    for index in range(remaining):
        targets[remainder_order[index % len(remainder_order)]] += 1
    return targets


def example_features(example: EvaluationExample) -> set[str]:
    """Return split-balancing features for one example."""
    category_name = (
        example.candidate_taxonomy.category_name(example.expected.category_id) or example.expected.category_id
    )
    category_label = validate_dataset.coverage_label(category_name, example.expected.category_id)
    features = {
        f"category:{category_label}",
        f"direction:{example.coverage.direction}",
        f"needs_review:{str(example.expected.needs_review).lower()}",
        f"label_source:{example.label_source}",
        f"ambiguity_type:{example.coverage.ambiguity_type or 'null'}",
        f"statement_type:{example.coverage.statement_type or 'null'}",
        f"expected_unknown:{str(category_name == UNKNOWN_CATEGORY_NAME).lower()}",
        f"tag_required:{str(bool(example.expected.tag_ids)).lower()}",
    }
    for tag_id in example.expected.tag_ids:
        tag_name = example.candidate_taxonomy.tag_name(tag_id) or tag_id
        features.add(f"tag:{validate_dataset.coverage_label(tag_name, tag_id)}")
    if example.coverage.ambiguity_type in IMPORTANT_AMBIGUITY_TYPES:
        features.add(f"benchmark_stratum:{example.coverage.ambiguity_type}")
    return features


def near_duplicate_key(example: EvaluationExample) -> str:
    """Return the strict near-duplicate key for normalized text and signed amount."""
    return (
        f"near:{normalize_text(example.transaction.merchant)}|"
        f"{normalize_text(example.transaction.description)}|{amount_key(example.transaction.amount)}"
    )


def merchant_amount_key(example: EvaluationExample) -> str | None:
    """Return a repeated merchant-and-amount grouping key when enough text exists."""
    merchant = normalize_text(example.transaction.merchant)
    if merchant == "unknown":
        merchant = normalize_text(example.transaction.description)
    if merchant == "unknown":
        return None
    return f"merchant_amount:{merchant}|{amount_key(example.transaction.amount)}"


def normalize_text(value: str | None) -> str:
    """Normalize merchant or description text for approximate leakage grouping."""
    if value is None:
        return "unknown"
    normalized = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii").lower()
    normalized = NON_ALNUM_RE.sub(" ", normalized)
    return " ".join(normalized.split()) or "unknown"


def amount_key(amount: float) -> str:
    """Return a signed two-decimal amount key using decimal rounding."""
    try:
        decimal_amount = Decimal(str(amount)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError):
        decimal_amount = Decimal("0.00")
    return format(decimal_amount, "f")


def source_request_id_from_notes(notes: str) -> str | None:
    """Extract a known source request ID hint from curation notes, if present."""
    match = SOURCE_REQUEST_ID_RE.search(notes)
    if not match:
        return None
    return match.group(1)


def validate_ratios(dev_ratio: float, validation_ratio: float, test_ratio: float) -> dict[str, float]:
    """Validate split ratios and return them by split name."""
    ratios = {"dev": dev_ratio, "validation": validation_ratio, "test": test_ratio}
    if any(ratio <= 0 for ratio in ratios.values()):
        raise ValueError("all split ratios must be positive")
    ratio_sum = sum(ratios.values())
    if not math.isclose(ratio_sum, 1.0, rel_tol=0.0, abs_tol=0.000001):
        raise ValueError(f"split ratios must sum to 1.0; got {ratio_sum:.6f}")
    return ratios


def build_split_report(
    source_path: Path,
    out_dir: Path,
    buckets: Sequence[SplitBucket],
    groups: Sequence[SplitGroup],
    examples: Sequence[EvaluationExample],
    *,
    ratios: dict[str, float],
    seed: int,
) -> str:
    """Render a deterministic Markdown split report."""
    group_count_by_size = Counter("multi-example" if group.size > 1 else "single-example" for group in groups)
    lines = [
        "# Dataset Split Report",
        "",
        f"- Source: `{source_path}`",
        f"- Output directory: `{out_dir}`",
        f"- Seed: `{seed}`",
        f"- Requested ratios: dev={ratios['dev']:.2f}, validation={ratios['validation']:.2f}, test={ratios['test']:.2f}",
        f"- Leakage groups: {len(groups)}",
        f"- Multi-example leakage groups: {group_count_by_size['multi-example']}",
        "",
        "## Intended use",
        "",
        "- Development set: prompt design and iteration.",
        "- Validation set: prompt candidate selection.",
        "- Held-out test set: final estimate after prompt selection only.",
        "",
        "## Overfitting controls",
        "",
        "- Do not tune prompts directly against the held-out test set.",
        "- Do not encode merchant-specific fixes in the prompt when a rule or taxonomy fix is more appropriate.",
        "- Use failure categories to revise prompts, not isolated examples.",
        "- Near-duplicates use normalized merchant, description, and signed amount.",
        "- Repeated merchant-and-amount patterns stay in one split when possible.",
        "- Notes containing `source_request_id=...` keep synthetic variants with their source example.",
        "",
        "## Split counts",
        "",
        "| Split | Target | Actual | Leakage groups |",
        "| --- | ---: | ---: | ---: |",
    ]
    for bucket in buckets:
        lines.append(f"| {bucket.name} | {bucket.target_count} | {len(bucket.bundles)} | {len(bucket.group_keys)} |")

    leakage_messages = leakage_report_lines(buckets, groups)
    lines.extend(["", "## Leakage check", "", *leakage_messages])

    for bucket in buckets:
        split_examples = [bundle.example for bundle in bucket.bundles]
        split_summary = summarize_examples(
            out_dir / f"{bucket.name}.jsonl", split_examples, reference_examples=examples
        )
        lines.extend(["", format_dataset_summary(split_summary)])

    return "\n".join(lines)


def leakage_report_lines(buckets: Sequence[SplitBucket], groups: Sequence[SplitGroup]) -> list[str]:
    """Return report lines describing leakage grouping integrity."""
    split_by_group_key = {group_key: bucket.name for bucket in buckets for group_key in bucket.group_keys}
    missing_group_keys = sorted(group.group_key for group in groups if group.group_key not in split_by_group_key)
    if missing_group_keys:
        return [f"- Error: {len(missing_group_keys)} leakage group(s) were not assigned."]
    return ["- No leakage grouping keys cross splits."]


def write_split_outputs(
    out_dir: Path,
    split_records_by_name: dict[str, list[dict[str, Any]]],
    report: str,
) -> None:
    """Write split JSONL files and the Markdown report."""
    out_dir.mkdir(parents=True, exist_ok=True)
    for name in SPLIT_NAMES:
        write_jsonl(out_dir / f"{name}.jsonl", split_records_by_name[name])
    (out_dir / "split_report.md").write_text(f"{report}\n", encoding="utf-8", newline="\n")


def validate_split_outputs(out_dir: Path) -> None:
    """Validate all generated split files."""
    for name in SPLIT_NAMES:
        validate_dataset.validate_dataset(out_dir / f"{name}.jsonl")


def build_parser() -> argparse.ArgumentParser:
    """Create the command-line parser."""
    parser = argparse.ArgumentParser(description="Split a FinScope LLM categorization eval dataset.")
    parser.add_argument("--input", required=True, type=Path, help="Validated curated JSONL dataset.")
    parser.add_argument("--out-dir", required=True, type=Path, help="Directory for dev/validation/test JSONL files.")
    parser.add_argument("--dev-ratio", type=float, default=0.5, help="Development split ratio.")
    parser.add_argument("--validation-ratio", type=float, default=0.3, help="Validation split ratio.")
    parser.add_argument("--test-ratio", type=float, default=0.2, help="Held-out test split ratio.")
    parser.add_argument("--seed", type=int, default=42, help="Deterministic split seed.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the dataset splitting CLI."""
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        ratios = validate_ratios(args.dev_ratio, args.validation_ratio, args.test_ratio)
        records, examples = load_validated_records(args.input)
        split_records_by_name, buckets, groups = split_records(records, examples, ratios=ratios, seed=args.seed)
        report = build_split_report(
            args.input,
            args.out_dir,
            buckets,
            groups,
            examples,
            ratios=ratios,
            seed=args.seed,
        )
        write_split_outputs(args.out_dir, split_records_by_name, report)
        validate_split_outputs(args.out_dir)
    except (DatasetValidationError, JsonlError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print("Wrote dev.jsonl, validation.jsonl, test.jsonl, and split_report.md " f"to {args.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
