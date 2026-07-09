"""Compare scored LLM categorization prompt runs.

The comparator reads saved scoring artifacts from multiple run directories and
produces a deterministic Markdown report focused on Task 1 methodology tradeoffs.
It does not call model providers or touch FinScope runtime data.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from evals.llm_categorization.tools.io_utils import JsonlError, read_jsonl

MAX_EXAMPLE_ROWS = 20
DIAGNOSTIC_FIXES = (
    ("Wrong category despite clear taxonomy instructions", "Prompt issue"),
    ("Overlapping categories", "Taxonomy issue"),
    ("Vague category or tag instruction", "Taxonomy instruction issue"),
    ("Merchant always maps to same category", "Rule issue"),
    ("Same merchant differs by amount, direction, or account", "Rule or history issue"),
    ("Local merchant knowledge missing", "Rule, history, or taxonomy example issue"),
    ("Inherently ambiguous transaction", "`UNKNOWN` or `needs_review` issue"),
    ("Invented taxonomy IDs", "Output-format prompt issue"),
    ("Overused optional tags", "Tag instruction or prompt issue"),
    ("Overused `UNKNOWN`", "Evidence-use prompt issue"),
    ("Avoided `UNKNOWN`", "Risk-control prompt issue"),
)


@dataclass(frozen=True)
class RunArtifacts:
    """Represent saved artifacts for one scored prompt run."""

    run_dir: Path
    run_id: str
    config: dict[str, Any]
    metrics: dict[str, Any]
    failures: tuple[dict[str, Any], ...]
    scored_outputs: tuple[dict[str, Any], ...]
    token_usage: dict[str, int]


def load_run(run_dir: Path) -> RunArtifacts:
    """Load one run directory's comparison artifacts."""
    config = load_json(run_dir / "config.json")
    metrics = load_json(run_dir / "metrics.json")
    failures = tuple(record for _, record in read_jsonl(run_dir / "failures.jsonl"))
    scored_outputs = tuple(record for _, record in read_jsonl(run_dir / "scored_outputs.jsonl"))
    run_id = str(config.get("run_id") or run_dir.name)
    return RunArtifacts(
        run_dir=run_dir,
        run_id=run_id,
        config=config,
        metrics=metrics,
        failures=failures,
        scored_outputs=scored_outputs,
        token_usage=load_token_usage(run_dir / "raw_outputs.jsonl"),
    )


def load_json(path: Path) -> dict[str, Any]:
    """Load one JSON object file."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def load_token_usage(path: Path) -> dict[str, int]:
    """Aggregate token usage from raw outputs when available."""
    if not path.exists():
        return {}
    totals: Counter[str] = Counter()
    row_count = 0
    for _, payload in read_jsonl(path):
        row_count += 1
        token_usage = payload.get("token_usage")
        if isinstance(token_usage, Mapping):
            for key, value in token_usage.items():
                if isinstance(key, str) and isinstance(value, int):
                    totals[key] += value
        duration_ms = payload.get("duration_ms")
        if isinstance(duration_ms, int):
            totals["duration_ms"] += duration_ms
    if row_count:
        totals["raw_output_rows"] = row_count
    return dict(sorted(totals.items()))


def compare_runs(run_dirs: Sequence[Path]) -> str:
    """Return a deterministic Markdown comparison report."""
    if len(run_dirs) < 2:
        raise ValueError("at least two runs are required for comparison")
    runs = tuple(load_run(run_dir) for run_dir in run_dirs)
    dataset_hashes = {dataset_hash(run) for run in runs}
    warnings = []
    if len(dataset_hashes) > 1:
        warnings.append("Runs do not all use the same dataset hash; compare metrics cautiously.")

    return render_report(runs, warnings)


def render_report(runs: Sequence[RunArtifacts], warnings: Sequence[str]) -> str:
    """Render the complete comparison Markdown report."""
    lines = [
        "# Prompt Run Comparison",
        "",
        "This report compares saved scored runs. It presents tradeoffs and does not declare a winner from the composite score alone.",
        "",
    ]
    if warnings:
        lines.extend(["## Warnings", "", *[f"- {warning}" for warning in warnings], ""])

    lines.extend(
        [
            "## Run configurations",
            "",
            "| Run | Model | Prompt | Prompt hash | Dataset hash | Temperature | Examples | Timestamp | Response format |",
            "| --- | --- | --- | --- | --- | ---: | ---: | --- | --- |",
            *configuration_rows(runs),
            "",
            "## Headline metrics",
            "",
            metric_table(
                runs,
                [
                    "composite_score",
                    "category_accuracy",
                    "known_category_accuracy",
                    "exact_taxonomy_match_rate",
                    "tag_micro_f1",
                    "needs_review_f1",
                    "confidence_calibration_score",
                    "unsafe_auto_assignment_rate",
                    "high_confidence_wrong_rate",
                ],
            ),
            "",
            "## Validity metrics",
            "",
            metric_table(
                runs,
                [
                    "valid_json_rate",
                    "schema_valid_rate",
                    "valid_category_id_rate",
                    "valid_tag_id_rate",
                    "valid_taxonomy_id_rate",
                    "invalid_output_rate",
                ],
            ),
            "",
            "## Category Metrics",
            "",
            metric_table(runs, ["category_accuracy", "known_category_accuracy", "exact_taxonomy_match_rate"]),
            "",
            "## Tag Metrics",
            "",
            metric_table(
                runs,
                [
                    "tag_micro_precision",
                    "tag_micro_recall",
                    "tag_micro_f1",
                    "tag_macro_precision",
                    "tag_macro_recall",
                    "tag_macro_f1",
                ],
            ),
            "",
            "## UNKNOWN Behavior",
            "",
            metric_table(runs, ["unknown_precision", "unknown_recall", "false_unknown_rate", "missed_unknown_rate"]),
            "",
            "## needs_review Behavior",
            "",
            metric_table(runs, ["needs_review_precision", "needs_review_recall", "needs_review_f1"]),
            "",
            "## Safety Metrics",
            "",
            metric_table(runs, ["unsafe_auto_assignment_rate", "high_confidence_wrong_rate"]),
            "",
            "## Confidence Calibration Summary",
            "",
            confidence_table(runs),
            "",
            "## Token Usage",
            "",
            token_usage_table(runs),
            "",
            "## Failure-Mode Comparison",
            "",
            failure_mode_table(runs),
            "",
            "## Disagreement Examples",
            "",
            examples_table(disagreement_examples(runs)),
            "",
            "## Uniquely Correct vs Unsafe",
            "",
            examples_table(unique_correct_unsafe_examples(runs)),
            "",
            "## Overused UNKNOWN Examples",
            "",
            examples_table(failure_mode_examples(runs, "false_unknown")),
            "",
            "## Overused Tag Examples",
            "",
            examples_table(failure_mode_examples(runs, "extra_tag")),
            "",
            "## Interpretation Notes",
            "",
            *interpretation_lines(runs),
            "",
            "## Diagnostic Categories",
            "",
            "| Failure type | Likely fix |",
            "| --- | --- |",
            *[f"| {failure_type} | {likely_fix} |" for failure_type, likely_fix in DIAGNOSTIC_FIXES],
        ]
    )
    return "\n".join(lines)


def configuration_rows(runs: Sequence[RunArtifacts]) -> list[str]:
    """Render configuration table rows."""
    rows = []
    for run in runs:
        rows.append(
            "| {run} | {model} | {prompt} | {prompt_hash} | {dataset_hash} | {temperature} | {examples} | {timestamp} | {response_format} |".format(
                run=run.run_id,
                model=run.config.get("model", ""),
                prompt=Path(str(run.config.get("prompt_path", ""))).name,
                prompt_hash=short_hash(str(run.config.get("prompt_hash", ""))),
                dataset_hash=short_hash(dataset_hash(run)),
                temperature=format_metric(run.config.get("temperature")),
                examples=run.config.get("number_of_examples", run.metrics.get("run", {}).get("example_count", "")),
                timestamp=run.config.get("timestamp", ""),
                response_format=run.config.get("response_format", ""),
            )
        )
    return rows


def metric_table(runs: Sequence[RunArtifacts], metric_names: Sequence[str]) -> str:
    """Render a metric-by-run table."""
    lines = [
        "| Metric | " + " | ".join(run.run_id for run in runs) + " |",
        "| --- | " + " | ".join("---:" for _ in runs) + " |",
    ]
    for metric_name in metric_names:
        values = [format_metric(headline(run).get(metric_name)) for run in runs]
        lines.append(f"| {metric_name} | {' | '.join(values)} |")
    return "\n".join(lines)


def confidence_table(runs: Sequence[RunArtifacts]) -> str:
    """Render calibration summary by run."""
    lines = [
        "| Run | Score | Method | Populated bands | Note |",
        "| --- | ---: | --- | ---: | --- |",
    ]
    for run in runs:
        calibration = run.metrics.get("confidence_calibration", {})
        bands = calibration.get("bands") if isinstance(calibration, Mapping) else []
        populated_bands = sum(1 for band in bands if isinstance(band, Mapping) and band.get("count"))
        lines.append(
            "| {run} | {score} | {method} | {bands} | {note} |".format(
                run=run.run_id,
                score=format_metric(headline(run).get("confidence_calibration_score")),
                method=calibration.get("method", "") if isinstance(calibration, Mapping) else "",
                bands=populated_bands,
                note=calibration.get("note", "") if isinstance(calibration, Mapping) else "",
            )
        )
    return "\n".join(lines)


def token_usage_table(runs: Sequence[RunArtifacts]) -> str:
    """Render token usage summary when raw-output metadata is available."""
    token_keys = sorted({key for run in runs for key in run.token_usage})
    if not token_keys:
        return "- Token usage was not available in the compared artifacts."
    lines = [
        "| Run | " + " | ".join(token_keys) + " |",
        "| --- | " + " | ".join("---:" for _ in token_keys) + " |",
    ]
    for run in runs:
        values = [str(run.token_usage.get(key, 0)) for key in token_keys]
        lines.append(f"| {run.run_id} | {' | '.join(values)} |")
    return "\n".join(lines)


def failure_mode_table(runs: Sequence[RunArtifacts]) -> str:
    """Render failure-mode count comparison."""
    modes = sorted({mode for run in runs for mode in failure_mode_counts(run)})
    if not modes:
        return "- No failure modes were recorded."
    lines = [
        "| Failure mode | " + " | ".join(run.run_id for run in runs) + " |",
        "| --- | " + " | ".join("---:" for _ in runs) + " |",
    ]
    for mode in modes:
        values = [str(failure_mode_counts(run).get(mode, 0)) for run in runs]
        lines.append(f"| {mode} | {' | '.join(values)} |")
    return "\n".join(lines)


def disagreement_examples(runs: Sequence[RunArtifacts]) -> list[dict[str, str]]:
    """Return examples where compared runs disagree."""
    rows = []
    for request_id in sorted(common_request_ids(runs)):
        predictions = {run.run_id: prediction_signature(scored_by_request_id(run)[request_id]) for run in runs}
        if len(set(predictions.values())) <= 1:
            continue
        expected = expected_summary(next(scored_by_request_id(run)[request_id] for run in runs))
        rows.append(example_row(request_id, expected, predictions))
    return rows[:MAX_EXAMPLE_ROWS]


def unique_correct_unsafe_examples(runs: Sequence[RunArtifacts]) -> list[dict[str, str]]:
    """Return examples where one run is uniquely exact and another is unsafe."""
    rows = []
    for request_id in sorted(common_request_ids(runs)):
        scored_by_run = {run.run_id: scored_by_request_id(run)[request_id] for run in runs}
        exact_runs = [
            run_id for run_id, scored in scored_by_run.items() if bool_at(scored, ("scores", "exact_taxonomy_match"))
        ]
        unsafe_runs = [
            run_id for run_id, scored in scored_by_run.items() if bool_at(scored, ("scores", "unsafe_auto_assignment"))
        ]
        if len(exact_runs) != 1 or not unsafe_runs:
            continue
        predictions = {run_id: prediction_signature(scored) for run_id, scored in scored_by_run.items()}
        row = example_row(request_id, expected_summary(next(iter(scored_by_run.values()))), predictions)
        row["note"] = f"Uniquely correct: {exact_runs[0]}; unsafe: {', '.join(sorted(unsafe_runs))}"
        rows.append(row)
    return rows[:MAX_EXAMPLE_ROWS]


def failure_mode_examples(runs: Sequence[RunArtifacts], failure_mode: str) -> list[dict[str, str]]:
    """Return examples where at least one run has a failure mode."""
    rows = []
    for request_id in sorted(common_request_ids(runs)):
        scored_by_run = {run.run_id: scored_by_request_id(run)[request_id] for run in runs}
        affected_runs = [
            run_id for run_id, scored in scored_by_run.items() if failure_mode in scored.get("failure_modes", [])
        ]
        if not affected_runs:
            continue
        predictions = {run_id: prediction_signature(scored) for run_id, scored in scored_by_run.items()}
        row = example_row(request_id, expected_summary(next(iter(scored_by_run.values()))), predictions)
        row["note"] = f"{failure_mode}: {', '.join(sorted(affected_runs))}"
        rows.append(row)
    return rows[:MAX_EXAMPLE_ROWS]


def examples_table(rows: Sequence[dict[str, str]]) -> str:
    """Render representative example rows."""
    if not rows:
        return "- (none)"
    run_keys = sorted({key for row in rows for key in row if key.startswith("run:")})
    lines = [
        "| Request ID | Expected | " + " | ".join(key.removeprefix("run:") for key in run_keys) + " | Note |",
        "| --- | --- | " + " | ".join("---" for _ in run_keys) + " | --- |",
    ]
    for row in rows:
        predictions = " | ".join(row.get(key, "") for key in run_keys)
        lines.append(f"| `{row['request_id']}` | {row['expected']} | {predictions} | {row.get('note', '')} |")
    return "\n".join(lines)


def example_row(request_id: str, expected: str, predictions: Mapping[str, str]) -> dict[str, str]:
    """Build one representative example table row."""
    row = {"request_id": request_id, "expected": expected, "note": ""}
    for run_id, prediction in sorted(predictions.items()):
        row[f"run:{run_id}"] = prediction
    return row


def interpretation_lines(runs: Sequence[RunArtifacts]) -> list[str]:
    """Return deterministic interpretation notes and tradeoff labels."""
    lines = [
        "- Do not claim a winner based only on the composite score. Compare safety, validity, review behavior, and failure modes before selecting a prompt.",
        f"- Most accurate prompt: {best_run(runs, 'exact_taxonomy_match_rate', higher_is_better=True)} by exact taxonomy match.",
        f"- Safest prompt: {safest_prompt(runs)} based on unsafe auto-assignment, high-confidence wrong rate, and validity.",
        f"- Prompt with lowest unsafe auto-assignment rate: {best_run(runs, 'unsafe_auto_assignment_rate', higher_is_better=False)}.",
        f"- Prompt that overuses `UNKNOWN`: {best_run(runs, 'false_unknown_rate', higher_is_better=True)}.",
        f"- Prompt that overuses tags: {tag_overuse_run(runs)}.",
        f"- Prompt with best confidence calibration: {best_run(runs, 'confidence_calibration_score', higher_is_better=True)}.",
    ]
    taxonomy_cases = diagnostic_cases(
        runs, {"rental_housing_confusion", "transfer_income_confusion", "reimbursement_confusion"}
    )
    rule_cases = diagnostic_cases(runs, {"ignored_similar_history", "overused_similar_history"})
    adjudication_cases = diagnostic_cases(runs, {"false_unknown", "missed_unknown", "under_review", "over_review"})
    lines.extend(
        [
            f"- Cases that likely require taxonomy changes: {format_case_list(taxonomy_cases)}.",
            f"- Cases that likely require categorization rules: {format_case_list(rule_cases)}.",
            f"- Cases that likely require manual adjudication rather than prompt changes: {format_case_list(adjudication_cases)}.",
        ]
    )
    return lines


def best_run(runs: Sequence[RunArtifacts], metric_name: str, *, higher_is_better: bool) -> str:
    """Return the best run for one metric, handling ties and unavailable values."""
    values = [(run.run_id, metric_value(headline(run).get(metric_name))) for run in runs]
    available = [(run_id, value) for run_id, value in values if value is not None]
    if not available:
        return "n/a"
    target_value = (max if higher_is_better else min)(value for _, value in available)
    winners = sorted(run_id for run_id, value in available if value == target_value)
    return f"{', '.join(winners)} ({format_metric(target_value)})"


def safest_prompt(runs: Sequence[RunArtifacts]) -> str:
    """Return the safest run by a simple safety tuple."""
    scored = []
    for run in runs:
        metrics = headline(run)
        scored.append(
            (
                metric_value(metrics.get("unsafe_auto_assignment_rate")) or 0.0,
                metric_value(metrics.get("high_confidence_wrong_rate")) or 0.0,
                -(metric_value(metrics.get("valid_taxonomy_id_rate")) or 0.0),
                run.run_id,
            )
        )
    best = min(scored)
    winners = sorted(
        run_id for unsafe, high_wrong, validity, run_id in scored if (unsafe, high_wrong, validity) == best[:3]
    )
    return ", ".join(winners)


def tag_overuse_run(runs: Sequence[RunArtifacts]) -> str:
    """Return the run with the highest false-positive tag count per example."""
    values = []
    for run in runs:
        example_count = run.metrics.get("run", {}).get("example_count", len(run.scored_outputs))
        false_positive_tags = run.metrics.get("counts", {}).get("tag_false_positives", 0)
        rate = false_positive_tags / example_count if example_count else None
        values.append((run.run_id, rate))
    available = [(run_id, value) for run_id, value in values if value is not None]
    if not available:
        return "n/a"
    target = max(value for _, value in available)
    winners = sorted(run_id for run_id, value in available if value == target)
    return f"{', '.join(winners)} ({format_metric(target)} false-positive tags/example)"


def diagnostic_cases(runs: Sequence[RunArtifacts], failure_modes: set[str]) -> list[str]:
    """Return request IDs with any of the selected failure modes."""
    request_ids = []
    for request_id in sorted(common_request_ids(runs)):
        if any(
            failure_mode in scored_by_request_id(run)[request_id].get("failure_modes", [])
            for run in runs
            for failure_mode in failure_modes
        ):
            request_ids.append(request_id)
    return request_ids[:MAX_EXAMPLE_ROWS]


def format_case_list(request_ids: Sequence[str]) -> str:
    """Format representative request IDs."""
    if not request_ids:
        return "none identified"
    return ", ".join(f"`{request_id}`" for request_id in request_ids)


def common_request_ids(runs: Sequence[RunArtifacts]) -> set[str]:
    """Return request IDs present in every run's scored outputs."""
    request_id_sets = [set(scored_by_request_id(run)) for run in runs]
    if not request_id_sets:
        return set()
    return set.intersection(*request_id_sets)


def scored_by_request_id(run: RunArtifacts) -> dict[str, dict[str, Any]]:
    """Return scored outputs keyed by request ID."""
    return {str(record.get("request_id")): record for record in run.scored_outputs if record.get("request_id")}


def prediction_signature(scored: Mapping[str, Any]) -> str:
    """Return a concise predicted assignment string."""
    predicted = scored.get("predicted", {})
    if not isinstance(predicted, Mapping):
        return "(invalid)"
    category_id = predicted.get("category_id")
    category_name = predicted.get("category_name")
    tag_ids = predicted.get("tag_ids") if isinstance(predicted.get("tag_ids"), list) else []
    needs_review = predicted.get("needs_review")
    confidence = predicted.get("confidence")
    return (
        f"{category_name or category_id or 'null'}"
        f" tags=[{', '.join(str(tag_id) for tag_id in sorted(tag_ids))}]"
        f" review={needs_review}"
        f" conf={format_metric(confidence)}"
    )


def expected_summary(scored: Mapping[str, Any]) -> str:
    """Return a concise expected assignment string."""
    expected = scored.get("expected", {})
    if not isinstance(expected, Mapping):
        return "(missing)"
    tag_ids = expected.get("tag_ids") if isinstance(expected.get("tag_ids"), list) else []
    return f"{expected.get('category_name') or expected.get('category_id')} tags=[{', '.join(str(tag_id) for tag_id in sorted(tag_ids))}]"


def headline(run: RunArtifacts) -> Mapping[str, Any]:
    """Return headline metrics for a run."""
    value = run.metrics.get("headline", {})
    return value if isinstance(value, Mapping) else {}


def failure_mode_counts(run: RunArtifacts) -> Mapping[str, int]:
    """Return failure mode counts for a run."""
    value = run.metrics.get("failure_mode_counts", {})
    return value if isinstance(value, Mapping) else {}


def dataset_hash(run: RunArtifacts) -> str:
    """Return the dataset hash from config or metrics."""
    value = run.config.get("dataset_hash")
    if isinstance(value, str) and value:
        return value
    metric_run = run.metrics.get("run", {})
    if isinstance(metric_run, Mapping):
        metric_hash = metric_run.get("dataset_hash")
        if isinstance(metric_hash, str):
            return metric_hash
    return ""


def metric_value(value: object) -> float | None:
    """Return a numeric metric value when available."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None


def bool_at(mapping: Mapping[str, Any], path: Sequence[str]) -> bool:
    """Return a nested boolean value, defaulting to False."""
    value: Any = mapping
    for key in path:
        if not isinstance(value, Mapping):
            return False
        value = value.get(key)
    return value is True


def format_metric(value: object) -> str:
    """Format metrics for Markdown."""
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.6f}"
    return str(value)


def short_hash(value: str) -> str:
    """Return a short deterministic hash display."""
    return value[:12] if value else ""


def build_parser() -> argparse.ArgumentParser:
    """Create the command-line parser."""
    parser = argparse.ArgumentParser(description="Compare scored LLM categorization prompt runs.")
    parser.add_argument("--runs", required=True, nargs="+", type=Path, help="Run directories to compare.")
    parser.add_argument("--out", required=True, type=Path, help="Markdown comparison report path.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the comparison CLI."""
    parser = build_parser()
    args = parser.parse_args(argv)
    from evals.llm_categorization.services.comparison_service import compare_selected_runs

    try:
        compare_selected_runs(args.runs, out_path=args.out)
    except (JsonlError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(f"Wrote comparison report to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
