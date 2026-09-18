"""Run the focused pytest selection used by the rule-engine mutation campaign.

Cosmic Ray runs the configured test command without a shell and records only
stdout for failing test runs. This helper keeps the command platform-light,
finds the repository virtual environment when present, and mirrors stderr to
stdout so baseline or mutant failures are diagnosable from Cosmic Ray dumps.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

RULE_ENGINE_TESTS = ("tests/unit/test_rule_engine_helpers.py",)


def main() -> int:
    """Run the existing focused rule-engine tests for Cosmic Ray."""
    repo_root = Path(__file__).resolve().parents[1]
    command = [
        str(select_python(repo_root)),
        "-B",
        "-m",
        "pytest",
        "-n",
        "0",
        *RULE_ENGINE_TESTS,
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
