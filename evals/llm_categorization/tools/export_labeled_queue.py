"""Export manually labeled AI-problem queue items to eval JSONL."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from evals.llm_categorization.services.labeling_queue_service import export_labeled_queue


def build_parser() -> argparse.ArgumentParser:
    """Return the CLI argument parser."""
    parser = argparse.ArgumentParser(description="Export labeled LLM categorization queue items to eval JSONL.")
    parser.add_argument("--queue", required=True, type=Path, help="Path to a labeling queue JSONL file.")
    parser.add_argument("--out", required=True, type=Path, help="Output JSONL path for exported eval examples.")
    parser.add_argument("--merge-into", type=Path, help="Optional existing draft dataset to prepend before exports.")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run labeled queue export."""
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        records = export_labeled_queue(args.queue, args.out, merge_into=args.merge_into)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"Exported labeled items: {len(records)}")
    print(f"Wrote: {args.out}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
