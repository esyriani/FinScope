"""Summarize Cosmic Ray JSONL dumps for on-demand mutation experiments.

Cosmic Ray 8.x exposes detailed session data through ``cosmic-ray dump``. This
helper keeps FinScope's first mutation experiment reproducible by turning that
raw JSONL stream into concise counts and a survivor list without introducing a
dashboard or CI quality gate.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

OUTCOME_KEYS = (
    "test_outcome",
    "test-outcome",
    "worker_outcome",
    "worker-outcome",
    "outcome",
    "status",
)
SURVIVOR_OUTCOMES = {"survived", "survival"}


def main() -> int:
    """Parse arguments, summarize a Cosmic Ray dump, and return a shell status."""
    parser = argparse.ArgumentParser(description="Summarize a Cosmic Ray JSONL dump.")
    parser.add_argument(
        "dump_path",
        nargs="?",
        type=Path,
        help="Path produced by `cosmic-ray dump`; omit or use '-' for stdin.",
    )
    args = parser.parse_args()

    records = list(load_records(args.dump_path))
    if not records:
        print("No Cosmic Ray work items found.", file=sys.stderr)
        return 1

    counts = Counter(outcome_for(result) for _, result in records)
    total = sum(counts.values())
    killed = counts.get("killed", 0)
    survived = sum(count for outcome, count in counts.items() if outcome in SURVIVOR_OUTCOMES)
    score = (killed / (killed + survived) * 100) if killed + survived else 0.0

    print(f"Total mutations: {total}")
    for outcome, count in sorted(counts.items()):
        print(f"{outcome}: {count}")
    print(f"Mutation score: {score:.2f}%")

    survivors = [(work_item, result) for work_item, result in records if outcome_for(result) in SURVIVOR_OUTCOMES]
    if survivors:
        print()
        print("Surviving mutants:")
        for work_item, result in survivors:
            print(f"- {mutation_label(work_item)} ({outcome_for(result)})")

    return 0


def load_records(path: Path | None) -> Iterable[tuple[Mapping[str, Any], Mapping[str, Any] | None]]:
    """Yield Cosmic Ray work-item/result pairs from a JSONL dump."""
    if path is None or str(path) == "-":
        yield from parse_lines(sys.stdin)
        return

    with path.open(encoding="utf-8") as dump_file:
        yield from parse_lines(dump_file)


def parse_lines(lines: Iterable[str]) -> Iterable[tuple[Mapping[str, Any], Mapping[str, Any] | None]]:
    """Yield parsed Cosmic Ray dump records."""
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue

        work_item, result = json.loads(stripped)
        yield work_item, result


def outcome_for(result: Mapping[str, Any] | None) -> str:
    """Return a normalized result outcome for one Cosmic Ray work result."""
    if result is None:
        return "pending"

    for key in OUTCOME_KEYS:
        if key in result and result[key] not in (None, ""):
            return normalize_outcome(result[key])

    for value in result.values():
        if isinstance(value, Mapping):
            nested = outcome_for(value)
            if nested != "pending":
                return nested

    return "unknown"


def normalize_outcome(value: object) -> str:
    """Normalize outcome labels from Cosmic Ray result records."""
    return str(value).strip().lower().replace(" ", "_").replace("-", "_")


def mutation_label(work_item: Mapping[str, Any]) -> str:
    """Return a compact location/operator label for one work item."""
    mutation = first_mutation(work_item)
    module_path = first_value(mutation, "module_path", "module-path", "module", "path") or "unknown module"
    operator = first_value(mutation, "operator", "operator_name", "operator-name") or "unknown operator"
    definition = first_value(mutation, "definition_name", "definition-name")
    occurrence = first_value(mutation, "occurrence", "occurrence_index", "occurrence-index")
    location = mutation_location(mutation)
    parts = [str(module_path)]
    if location:
        parts.append(str(location))
    if definition:
        parts.append(str(definition))
    parts.append(str(operator))
    if occurrence is not None:
        parts.append(f"occurrence {occurrence}")
    return " | ".join(parts)


def first_mutation(work_item: Mapping[str, Any]) -> Mapping[str, Any]:
    """Return the first mutation mapping from a Cosmic Ray work item."""
    for key in ("mutations", "mutation_specs", "mutation-specs"):
        value = work_item.get(key)
        if isinstance(value, list) and value and isinstance(value[0], Mapping):
            return value[0]

    return first_mapping(work_item, "mutation", "mutant", "job") or work_item


def mutation_location(mutation: Mapping[str, Any]) -> str:
    """Return the best available source location for a mutation."""
    for key in ("start_pos", "start-pos", "position", "location"):
        value = mutation.get(key)
        if isinstance(value, (list, tuple)) and value:
            return ":".join(str(item) for item in value)
        if isinstance(value, Mapping):
            line = first_value(value, "line", "lineno", "row")
            column = first_value(value, "column", "col")
            if line is not None and column is not None:
                return f"{line}:{column}"
            if line is not None:
                return str(line)
        if value not in (None, ""):
            return str(value)
    return ""


def first_mapping(mapping: Mapping[str, Any], *keys: str) -> Mapping[str, Any] | None:
    """Return the first nested mapping found under one of the supplied keys."""
    for key in keys:
        value = mapping.get(key)
        if isinstance(value, Mapping):
            return value
    return None


def first_value(mapping: Mapping[str, Any], *keys: str) -> object | None:
    """Return the first non-empty mapping value found under one of the supplied keys."""
    for key in keys:
        value = mapping.get(key)
        if value not in (None, ""):
            return value
    return None


if __name__ == "__main__":
    raise SystemExit(main())
