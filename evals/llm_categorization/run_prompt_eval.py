"""Command-line runner for opt-in LLM categorization prompt evals."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    repo_root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(repo_root))
    sys.path.insert(0, str(repo_root / "src"))

from evals.llm_categorization.prompt_eval import (  # noqa: E402
    DEFAULT_REVIEW_THRESHOLD,
    DEFAULT_VERIFY_THRESHOLD,
    RUNS_DIR,
    PromptEvalProvider,
    evaluate_prompt_eval_cases,
    expected_prompt_eval_result,
    load_prompt_eval_cases,
    prompt_eval_taxonomy,
    write_prompt_eval_artifacts,
)
from finance_app.core.config import settings  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    """Run prompt evals with either dry-run expected output or OpenAI."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=None, help="JSONL dataset path.")
    parser.add_argument("--dry-run", action="store_true", help="Score ideal expected outputs without calling a model.")
    parser.add_argument("--model", default=settings.default_categorization_model, help="OpenAI model name.")
    parser.add_argument("--output-dir", type=Path, default=None, help="Directory for report.md and raw_outputs.jsonl.")
    parser.add_argument("--limit", type=int, default=0, help="Run only the first N cases.")
    parser.add_argument("--verify-threshold", type=float, default=DEFAULT_VERIFY_THRESHOLD)
    parser.add_argument("--review-threshold", type=float, default=DEFAULT_REVIEW_THRESHOLD)
    parser.add_argument("--fail-under-exact", type=float, default=None, help="Fail if exact match rate is below this.")
    args = parser.parse_args(argv)

    cases = load_prompt_eval_cases(args.cases) if args.cases else load_prompt_eval_cases()
    if args.limit:
        cases = cases[: args.limit]

    provider: PromptEvalProvider
    if args.dry_run:
        taxonomy = prompt_eval_taxonomy()

        def provider(case: Any, _messages: Any) -> Any:
            return expected_prompt_eval_result(case, taxonomy)

        model_label = f"{args.model} (dry-run expected output)"
    else:
        api_key = settings.openai_api_key or os.environ.get("OPENAI_API_KEY")
        if not api_key:
            print("OpenAI API key is not configured. Set OPENAI_API_KEY or pass --dry-run.", file=sys.stderr)
            return 2
        provider = openai_prompt_eval_provider(args.model, api_key)
        model_label = args.model

    run = evaluate_prompt_eval_cases(
        cases,
        provider,
        model=model_label,
        verify_threshold=args.verify_threshold,
        review_threshold=args.review_threshold,
    )
    output_dir = args.output_dir or default_output_dir(args.dry_run)
    write_prompt_eval_artifacts(output_dir, run)
    print(f"Wrote prompt eval report to {output_dir / 'report.md'}")
    print(f"Exact taxonomy match rate: {run.summary['exact_taxonomy_match_rate']:.3f}")
    print(f"Unsafe auto-assignment rate: {run.summary['unsafe_auto_assignment_rate']:.3f}")

    if args.fail_under_exact is not None and run.summary["exact_taxonomy_match_rate"] < args.fail_under_exact:
        return 1
    return 0


def openai_prompt_eval_provider(model: str, api_key: str) -> PromptEvalProvider:
    """Return a provider that calls OpenAI with already-built eval messages."""
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise SystemExit("OpenAI package is not installed. Install development dependencies first.") from exc

    client = OpenAI(api_key=api_key, timeout=60)

    def provider(_case: Any, messages: Any) -> Any:
        """Return the first result object from an OpenAI JSON response."""
        response = client.chat.completions.create(
            model=model,
            response_format={"type": "json_object"},
            temperature=0,
            messages=messages,
        )
        payload = json.loads(response.choices[0].message.content)
        results = payload.get("results")
        if isinstance(results, list) and results:
            return results[0]
        return payload

    return provider


def default_output_dir(dry_run: bool) -> Path:
    """Return a timestamped eval output directory."""
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    prefix = "dry_run" if dry_run else "run"
    return RUNS_DIR / f"{prefix}_{timestamp}"


if __name__ == "__main__":
    raise SystemExit(main())
