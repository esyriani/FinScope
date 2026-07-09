"""Preview coverage-driven dataset builds without writing datasets.

The command validates a JSON dataset spec, inspects a SQLite database in
read-only mode, and prints whether requested coverage appears satisfiable. It
does not modify the database or generate JSONL examples.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from evals.llm_categorization.services.dataset_builder_service import (
    DatasetSpecError,
    load_dataset_spec,
    preview_dataset_build,
    render_preview_report,
    resolve_dataset_spec_path,
)
from evals.llm_categorization.tools.inspect_db import InspectionError


def build_parser() -> argparse.ArgumentParser:
    """Return the CLI argument parser."""
    parser = argparse.ArgumentParser(
        description="Preview a coverage-driven LLM categorization eval dataset build.",
    )
    parser.add_argument("--db", required=True, type=Path, help="Path to the FinScope SQLite database.")
    parser.add_argument(
        "--spec",
        required=True,
        type=Path,
        help="Path to a JSON dataset spec under evals/llm_categorization/dataset_specs/.",
    )
    parser.add_argument("--out", type=Path, help="Optional Markdown output path for the preview report.")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the dataset build preview CLI."""
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        spec_path = resolve_dataset_spec_path(args.spec)
        spec = load_dataset_spec(spec_path)
        preview = preview_dataset_build(args.db, spec)
        report = render_preview_report(preview)
    except (DatasetSpecError, FileNotFoundError, InspectionError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(f"{report}\n", encoding="utf-8", newline="\n")
    else:
        print(report)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
