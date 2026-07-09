"""Build draft JSONL datasets from coverage-driven specs.

The command reads a JSON dataset spec, opens a SQLite database read-only, and
writes draft eval artifacts for manual review. It never modifies FinScope
runtime data or calls model providers.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from evals.llm_categorization.services.dataset_builder_service import (
    DatasetSpecError,
    build_draft_dataset_from_spec,
    load_dataset_spec,
    resolve_dataset_spec_path,
)
from evals.llm_categorization.tools.inspect_db import InspectionError


def build_parser() -> argparse.ArgumentParser:
    """Return the CLI argument parser."""
    parser = argparse.ArgumentParser(
        description="Build a draft LLM categorization eval dataset from a coverage spec.",
    )
    parser.add_argument("--db", required=True, type=Path, help="Path to the FinScope SQLite database.")
    parser.add_argument(
        "--spec",
        required=True,
        type=Path,
        help="Path to a JSON dataset spec under evals/llm_categorization/dataset_specs/.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the dataset build command."""
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        spec_path = resolve_dataset_spec_path(args.spec)
        spec = load_dataset_spec(spec_path)
        result = build_draft_dataset_from_spec(args.db, spec)
    except (DatasetSpecError, FileNotFoundError, InspectionError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(f"Wrote draft dataset: {result.artifacts.dataset_path}")
    print(f"Wrote coverage report: {result.artifacts.coverage_report_path}")
    print(f"Wrote adjudication queue: {result.artifacts.adjudication_path}")
    print(f"Wrote labeling queue: {result.artifacts.labeling_queue_path}")
    print(f"Wrote spec snapshot: {result.artifacts.spec_used_path}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
