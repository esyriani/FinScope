"""Tests for pytest performance configuration.

Verifies that the repository-level pytest defaults keep collection scoped to
the test suite and avoid slow serial coverage runs during normal development.
"""

from configparser import ConfigParser
from pathlib import Path


def load_pytest_config():
    """Load the repository pytest configuration as an INI parser."""
    parser = ConfigParser()
    parser.read(Path("pytest.ini"), encoding="utf-8")
    return parser["pytest"]


def test_pytest_defaults_parallel_no_coverage_test_collection_only():
    """Verify default pytest runs are parallel, coverage-free, and tests-scoped."""
    config = load_pytest_config()
    addopts = config["addopts"].split()
    norecursedirs = set(config["norecursedirs"].split())

    assert config["testpaths"].split() == ["tests"]
    assert "-n" in addopts
    assert "auto" in addopts
    assert not any(option == "--no-cov" or option.startswith("--cov") for option in addopts)
    assert {"src", "docs", "runtime", ".venv", "vibecoding"} <= norecursedirs
