"""Validate manual labeling queue JSONL files for AI problem cases."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from evals.llm_categorization.services.labeling_queue_service import validate_labeling_queue


def build_parser() -> argparse.ArgumentParser:
    """Return the CLI argument parser."""
    parser = argparse.ArgumentParser(description="Validate an LLM categorization AI-problem labeling queue.")
    parser.add_argument("--queue", required=True, type=Path, help="Path to a labeling queue JSONL file.")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run labeling queue validation."""
    parser = build_parser()
    args = parser.parse_args(argv)
    result = validate_labeling_queue(args.queue)
    print(f"Items: {result.item_count}")
    print(f"Pending: {result.pending_count}")
    print(f"Labeled: {result.labeled_count}")
    print(f"Unusable: {result.unusable_count}")
    if result.valid:
        print("Status: valid")
        return 0
    print("Status: invalid")
    for error in result.errors:
        print(f"- {error}", file=sys.stderr)
    return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
