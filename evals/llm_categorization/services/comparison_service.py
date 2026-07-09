"""Comparison services for scored LLM categorization eval runs.

The functions compare saved scoring artifacts directly from Python, producing
the same deterministic Markdown report as the CLI.
"""

from pathlib import Path

from evals.llm_categorization.tools import compare_runs


def compare_selected_runs(run_dirs: tuple[Path, ...] | list[Path], *, out_path: Path | None = None) -> str:
    """Compare selected scored runs and optionally write the Markdown report."""
    report = compare_runs.compare_runs(tuple(run_dirs))
    if out_path is not None:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(f"{report}\n", encoding="utf-8", newline="\n")
    return report
