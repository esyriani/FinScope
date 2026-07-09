"""Unit tests for LLM categorization eval CLI help text."""

from __future__ import annotations

from evals.llm_categorization.tools import (
    build_dataset_from_db,
    compare_runs,
    inspect_db,
    render_prompt,
    run_eval,
    score_outputs,
    split_dataset,
    summarize_dataset,
    validate_dataset,
)


def test_eval_cli_parsers_have_helpful_descriptions_and_options():
    """Verify every eval CLI exposes useful help text."""
    parsers = [
        validate_dataset.build_parser(),
        summarize_dataset.build_parser(),
        inspect_db.build_parser(),
        build_dataset_from_db.build_parser(),
        split_dataset.build_parser(),
        render_prompt.build_parser(),
        run_eval.build_parser(),
        score_outputs.build_parser(),
        compare_runs.build_parser(),
    ]

    help_text = "\n".join(parser.format_help() for parser in parsers)

    assert "Validate a FinScope LLM categorization eval JSONL dataset." in help_text
    assert "Summarize a validated FinScope LLM categorization eval dataset." in help_text
    assert "Inspect a FinScope SQLite database for LLM categorization eval readiness." in help_text
    assert "Build a draft LLM categorization eval dataset from a FinScope SQLite database." in help_text
    assert "Split a FinScope LLM categorization eval dataset." in help_text
    assert "Render LLM categorization prompt payloads without API calls." in help_text
    assert "Run one LLM categorization prompt candidate on a dataset." in help_text
    assert "Score saved LLM categorization raw outputs." in help_text
    assert "Compare scored LLM categorization prompt runs." in help_text
    assert "--dry-run" in help_text
    assert "--resume" in help_text
    assert "--no-score" in help_text
    assert "--out-dir" in help_text
