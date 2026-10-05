"""Validate and run the local LLM categorization oracle dataset.

The default mode validates the local workbook only. Pass ``--live`` to call the
configured OpenAI-compatible provider and score its output against the retained
ground-truth rows.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from finance_app.database.engine import db_core_transaction  # noqa: E402
from finance_app.modules.categories.llm_eval import (  # noqa: E402
    LLM_EVAL_CONTEXT_COLD,
    LLM_EVAL_CONTEXT_MODES,
    LlmEvalMetrics,
    LlmEvalReport,
    load_eval_rows,
    prepare_llm_eval,
    retained_eval_rows,
    validate_eval_rows,
)
from finance_app.modules.categories.taxonomy import get_category_rows, get_tag_options  # noqa: E402

DEFAULT_EVAL_PATH = PROJECT_ROOT / "runtime" / "llm_eval" / "llm_categorization_oracle.xlsx"


def main() -> int:
    """Run the LLM categorization evaluation command."""
    args = parse_args()
    input_path = Path(args.input)
    rows = load_eval_rows(input_path)
    with db_core_transaction() as conn:
        category_options = [row["name"] for row in get_category_rows(conn)]
        tag_options = get_tag_options(conn)
        issues = validate_eval_rows(rows, category_options, tag_options)
        modes = selected_modes(args.mode)
        prepared_by_mode = (
            {mode: prepare_llm_eval(conn, rows, openai_model=args.model, mode=mode) for mode in modes}
            if not issues and args.live
            else {}
        )

    retained_count = len(retained_eval_rows(rows))
    print(f"Loaded {len(rows)} rows from {input_path}")
    print(f"Retained rows: {retained_count}")

    if issues:
        print(f"Validation failed with {len(issues)} issue(s):")
        for issue in issues[: args.show_failures]:
            print(f"- {issue.row_id}: {issue.code}: {issue.message}")
        if len(issues) > args.show_failures:
            print(f"... {len(issues) - args.show_failures} more issue(s)")
        return 2

    print("Validation passed.")
    if not args.live:
        print("Live LLM evaluation not run. Pass --live to request provider output and score it.")
        return 0
    if not prepared_by_mode:
        print("No prepared eval context was available.")
        return 2

    reports = {mode: run_live_eval(prepared, args.batch_size) for mode, prepared in prepared_by_mode.items()}
    print_reports(reports, args.show_failures)
    output_json = Path(args.output_json) if args.output_json else default_report_path(input_path, args.mode)
    write_json_report(output_json, reports, input_path)
    if not args.no_markdown:
        output_md = Path(args.output_md) if args.output_md else output_json.with_suffix(".md")
        write_markdown_report(output_md, reports, input_path, args.show_failures)
    return 0


def parse_args() -> argparse.Namespace:
    """Parse command-line options."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input",
        default=str(DEFAULT_EVAL_PATH),
        help=f"Path to the labeled eval CSV or XLSX workbook. Default: {DEFAULT_EVAL_PATH}",
    )
    parser.add_argument("--csv", dest="input", help=argparse.SUPPRESS)
    parser.add_argument(
        "--live",
        action="store_true",
        help="Call the configured LLM provider. Without this flag, only validation runs.",
    )
    parser.add_argument(
        "--mode",
        choices=(*LLM_EVAL_CONTEXT_MODES, "all"),
        default=LLM_EVAL_CONTEXT_COLD,
        help=(
            "Prompt context to evaluate. cold uses taxonomy-only new-account context; "
            "rules adds rule evidence; full adds production-like rule and history evidence. all runs every mode."
        ),
    )
    parser.add_argument("--model", default=None, help="Optional categorization model override.")
    parser.add_argument("--batch-size", type=int, default=None, help="Optional LLM request batch size.")
    parser.add_argument("--output-json", default=None, help="Optional path for a JSON eval report.")
    parser.add_argument("--output-md", default=None, help="Optional path for a Markdown eval report.")
    parser.add_argument("--no-markdown", action="store_true", help="Do not write the Markdown report.")
    parser.add_argument("--show-failures", type=int, default=25, help="Maximum validation or mismatch rows to print.")
    return parser.parse_args()


def selected_modes(mode: str) -> tuple[str, ...]:
    """Return the concrete eval modes requested by the CLI."""
    if mode == "all":
        return tuple(LLM_EVAL_CONTEXT_MODES)
    return (mode,)


def run_live_eval(prepared: Any, batch_size: int | None) -> LlmEvalReport:
    """Run the provider-backed evaluation after database preparation is complete."""
    from finance_app.modules.categories.llm_eval import evaluate_prepared_llm

    return evaluate_prepared_llm(prepared, batch_size=batch_size)


def print_reports(reports: dict[str, LlmEvalReport], failure_limit: int) -> None:
    """Print one or more compact eval reports."""
    for mode, report in reports.items():
        print_report(mode, report, failure_limit)


def print_report(mode: str, report: LlmEvalReport, failure_limit: int) -> None:
    """Print a compact human-readable eval report."""
    metrics = report.metrics
    print("")
    print(f"LLM categorization eval ({mode})")
    print(f"- Rows scored: {metrics.total}")
    print(f"- Category accuracy: {metrics.category_correct}/{metrics.total} " f"({percent(metrics.category_accuracy)})")
    print(
        f"- Non-UNKNOWN category accuracy: "
        f"{metrics.concrete_category_correct}/{metrics.concrete_category_total} "
        f"({percent(metrics.concrete_category_accuracy)})"
    )
    print(
        f"- Exact category+tag match: {metrics.exact_match_correct}/{metrics.total} ({percent(metrics.exact_match_accuracy)})"
    )
    print(
        f"- Tag precision/recall/F1: {percent(metrics.tag_precision)} / {percent(metrics.tag_recall)} / {percent(metrics.tag_f1)}"
    )
    print(f"- UNKNOWN precision/recall: {percent(metrics.unknown_precision)} / {percent(metrics.unknown_recall)}")
    mismatches = report.mismatches
    if not mismatches:
        print("- Mismatches: 0")
        return

    print(f"- Mismatches: {len(mismatches)}")
    for prediction in mismatches[:failure_limit]:
        expected_tags = "|".join(prediction.row.expected_tags)
        predicted_tags = "|".join(prediction.tags)
        print(
            f"  - {prediction.row.candidate_id} {prediction.row.merchant_key}: "
            f"expected {prediction.row.expected_category} [{expected_tags}], "
            f"got {prediction.category} [{predicted_tags}], "
            f"confidence={prediction.confidence}"
        )
    if len(mismatches) > failure_limit:
        print(f"  ... {len(mismatches) - failure_limit} more mismatch(es)")


def default_report_path(input_path: Path, mode: str) -> Path:
    """Return the default on-demand report path beside the oracle file."""
    suffix = "live_report" if mode == LLM_EVAL_CONTEXT_COLD else f"{mode}_live_report"
    return input_path.with_name(f"{input_path.stem}_{suffix}.json")


def write_json_report(path: Path, reports: dict[str, LlmEvalReport], input_path: Path) -> None:
    """Write an eval report as JSON."""
    if len(reports) == 1:
        mode, report = next(iter(reports.items()))
        payload = {
            "source": {
                "input_path": str(input_path),
                "retained_rows": report.metrics.total,
                "mode": mode,
            },
            **report_payload(report),
        }
    else:
        payload = {
            "source": {
                "input_path": str(input_path),
                "retained_rows": next(iter(reports.values())).metrics.total if reports else 0,
                "mode": "all",
                "modes": list(reports),
            },
            "reports": {mode: report_payload(report) for mode, report in reports.items()},
        }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
    print(f"JSON report written to {path}")


def report_payload(report: LlmEvalReport) -> dict[str, Any]:
    """Return JSON-safe report details for one mode."""
    payload = {
        "metrics": metrics_payload(report.metrics),
        "predictions": [
            {
                "candidate_id": prediction.row.candidate_id,
                "merchant_key": prediction.row.merchant_key,
                "amount_direction": prediction.row.amount_direction,
                "amount_magnitude": prediction.row.amount_magnitude,
                "transaction_kind": prediction.row.transaction_kind,
                "expected_category": prediction.row.expected_category,
                "predicted_category": prediction.category,
                "expected_tags": list(prediction.row.expected_tags),
                "predicted_tags": list(prediction.tags),
                "confidence": prediction.confidence,
                "needs_review": prediction.needs_review,
                "metadata": dict(prediction.metadata),
                "context": dict(prediction.context),
            }
            for prediction in report.predictions
        ],
        "mismatches": [
            {
                "candidate_id": prediction.row.candidate_id,
                "merchant_key": prediction.row.merchant_key,
                "expected_category": prediction.row.expected_category,
                "predicted_category": prediction.category,
                "expected_tags": list(prediction.row.expected_tags),
                "predicted_tags": list(prediction.tags),
                "confidence": prediction.confidence,
                "needs_review": prediction.needs_review,
                "metadata": dict(prediction.metadata),
                "context": dict(prediction.context),
            }
            for prediction in report.mismatches
        ],
    }
    return payload


def write_markdown_report(
    path: Path,
    reports: dict[str, LlmEvalReport],
    input_path: Path,
    failure_limit: int,
) -> None:
    """Write a compact Markdown report for one or more eval modes."""
    lines = [
        "# LLM Categorization Evaluation",
        "",
        f"- Source: `{input_path}`",
        f"- Modes: {', '.join(reports)}",
        "",
        "## Summary",
        "",
        "| Mode | Rows | Category | Concrete | Exact | Tag F1 | UNKNOWN P/R | Mismatches |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for mode, report in reports.items():
        metrics = report.metrics
        lines.append(
            "| "
            f"{mode} | "
            f"{metrics.total} | "
            f"{percent(metrics.category_accuracy)} | "
            f"{percent(metrics.concrete_category_accuracy)} | "
            f"{percent(metrics.exact_match_accuracy)} | "
            f"{percent(metrics.tag_f1)} | "
            f"{percent(metrics.unknown_precision)} / {percent(metrics.unknown_recall)} | "
            f"{len(report.mismatches)} |"
        )

    for mode, report in reports.items():
        lines.extend(markdown_mode_section(mode, report, failure_limit))

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    print(f"Markdown report written to {path}")


def markdown_mode_section(mode: str, report: LlmEvalReport, failure_limit: int) -> list[str]:
    """Return the Markdown details for one mode."""
    metrics = report.metrics
    lines = [
        "",
        f"## {mode}",
        "",
        f"- Rows scored: {metrics.total}",
        f"- Category accuracy: {metrics.category_correct}/{metrics.total} ({percent(metrics.category_accuracy)})",
        (
            f"- Non-UNKNOWN category accuracy: {metrics.concrete_category_correct}/"
            f"{metrics.concrete_category_total} ({percent(metrics.concrete_category_accuracy)})"
        ),
        f"- Exact category+tag match: {metrics.exact_match_correct}/{metrics.total} ({percent(metrics.exact_match_accuracy)})",
        f"- Tag precision/recall/F1: {percent(metrics.tag_precision)} / {percent(metrics.tag_recall)} / {percent(metrics.tag_f1)}",
        f"- UNKNOWN precision/recall: {percent(metrics.unknown_precision)} / {percent(metrics.unknown_recall)}",
        "",
        "### Mismatches",
        "",
    ]
    if not report.mismatches:
        lines.append("No mismatches.")
        return lines

    lines.extend(
        [
            "| Candidate | Merchant | Expected | Predicted | Context | Evidence | Confidence |",
            "|---|---|---|---|---|---|---:|",
        ]
    )
    for prediction in report.mismatches[:failure_limit]:
        lines.append(markdown_mismatch_row(prediction))
    if len(report.mismatches) > failure_limit:
        lines.append("")
        lines.append(f"... {len(report.mismatches) - failure_limit} more mismatch(es).")
    return lines


def markdown_mismatch_row(prediction: Any) -> str:
    """Return one Markdown table row for a mismatch."""
    expected = markdown_label(prediction.row.expected_category, prediction.row.expected_tags)
    predicted = markdown_label(prediction.category, prediction.tags)
    context = prediction.context
    evidence = ", ".join(
        part
        for part in (
            f"rule={context.get('rule_category')}" if context.get("rule_category") else "",
            f"history={context.get('historical_category')}" if context.get("historical_category") else "",
        )
        if part
    )
    if not evidence:
        evidence = "-"
    return (
        "| "
        f"{escape_markdown_cell(prediction.row.candidate_id)} | "
        f"{escape_markdown_cell(prediction.row.merchant_key)} | "
        f"{escape_markdown_cell(expected)} | "
        f"{escape_markdown_cell(predicted)} | "
        f"{escape_markdown_cell(str(context.get('db_match_status') or '-'))} | "
        f"{escape_markdown_cell(evidence)} | "
        f"{'' if prediction.confidence is None else f'{prediction.confidence:.4f}'} |"
    )


def markdown_label(category: str, tags: Any) -> str:
    """Return a compact category/tag label."""
    tag_text = "|".join(tags)
    return f"{category} [{tag_text}]"


def escape_markdown_cell(value: object) -> str:
    """Escape Markdown table separator characters in generated report cells."""
    return str(value or "").replace("|", "\\|").replace("\n", " ")


def metrics_payload(metrics: LlmEvalMetrics) -> dict[str, Any]:
    """Return JSON-safe metric values."""
    payload = asdict(metrics)
    payload.update(
        {
            "category_accuracy": metrics.category_accuracy,
            "concrete_category_accuracy": metrics.concrete_category_accuracy,
            "exact_match_accuracy": metrics.exact_match_accuracy,
            "tag_set_accuracy": metrics.tag_set_accuracy,
            "tag_precision": metrics.tag_precision,
            "tag_recall": metrics.tag_recall,
            "tag_f1": metrics.tag_f1,
            "unknown_precision": metrics.unknown_precision,
            "unknown_recall": metrics.unknown_recall,
        }
    )
    return payload


def percent(value: float) -> str:
    """Format a ratio as a percentage."""
    return f"{value * 100:.1f}%"


if __name__ == "__main__":
    raise SystemExit(main())
