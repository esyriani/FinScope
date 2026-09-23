"""Run focused pytest profiles for FinScope mutation-testing campaigns.

Cosmic Ray executes commands without a shell and only records stdout for failing
tests. This profile runner keeps each mutation target's test command concise,
uses the repository virtualenv when present, and mirrors stderr into stdout for
diagnosable session dumps.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

PROFILES = {
    "analytics-summary": ("tests/unit/test_analytics_helpers.py",),
    "categorization-orchestration": ("tests/integration/test_categorization_workflow.py",),
    "comparison-statistics": ("tests/unit/test_comparison_statistics.py",),
    "financial-reporting": ("tests/integration/test_reporting_expressions.py",),
    "import-transaction-kinds": (
        "tests/unit/test_upload_transaction_kinds.py",
        "tests/integration/test_account_payment_import.py",
        "tests/integration/test_interac_import.py",
    ),
    "import-transaction-kinds-unit": ("tests/unit/test_upload_transaction_kinds.py",),
    "llm-results": ("tests/integration/test_llm_categorization.py",),
    "merchant-normalization": ("tests/unit/test_merchant_normalization.py",),
    "reimbursements": ("tests/integration/test_reimbursements_service.py",),
    "review-workflow": ("tests/integration/test_review_workflow.py",),
    "rule-scoring": ("tests/unit/test_category_rules_matching.py",),
    "statement-parser": ("tests/unit/test_statement_importer.py",),
    "transaction-importer": ("tests/integration/test_transaction_importer.py",),
}


def main() -> int:
    """Run one named Cosmic Ray pytest profile."""
    parser = argparse.ArgumentParser(description="Run a focused mutation-test pytest profile.")
    parser.add_argument("profile", choices=sorted(PROFILES))
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[1]
    command = [
        str(select_python(repo_root)),
        "-B",
        "-m",
        "pytest",
        "-n",
        "0",
        "-x",
        *PROFILES[args.profile],
    ]
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"

    proc = subprocess.run(
        command,
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.stdout:
        print(proc.stdout, end="")
    if proc.stderr:
        print(proc.stderr, end="")
    return proc.returncode


def select_python(repo_root: Path) -> Path:
    """Return the repo virtualenv Python when available, else this interpreter."""
    candidates = (
        repo_root / ".venv" / "Scripts" / "python.exe",
        repo_root / ".venv" / "bin" / "python",
    )
    for candidate in candidates:
        if candidate.exists():
            return candidate

    return Path(sys.executable)


if __name__ == "__main__":
    raise SystemExit(main())
