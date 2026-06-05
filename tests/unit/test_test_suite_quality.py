"""Quality gates for the curated test suite.

Verifies that tests stay in the documented layer layout and that broad route
test patterns do not quietly grow back after curation.
"""

import re
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
TESTS_ROOT = PROJECT_ROOT / "tests"
LAYER_DIRS = {"integration", "routes", "smoke", "unit"}


def test_test_files_live_in_documented_layer_directories():
    """Verify executable test files stay under the documented layer folders."""
    misplaced = []
    for path in TESTS_ROOT.rglob("test_*.py"):
        relative_parts = path.relative_to(TESTS_ROOT).parts
        if not relative_parts or relative_parts[0] not in LAYER_DIRS:
            misplaced.append(path.relative_to(PROJECT_ROOT).as_posix())

    assert misplaced == []


def test_catch_all_route_file_stays_below_review_size():
    """Keep the remaining catch-all route file from regrowing."""
    path = TESTS_ROOT / "routes" / "test_flask_routes.py"
    source = path.read_text(encoding="utf-8")
    test_count = len(re.findall(r"^\s*def test_", source, flags=re.MULTILINE))

    assert test_count <= 20
    assert len(source.splitlines()) <= 700


def test_route_submit_background_job_patches_use_shared_recorder():
    """Verify route tests use the shared recorder for queued job assertions."""
    offenders = []
    for path in (TESTS_ROOT / "routes").glob("test_*.py"):
        source = path.read_text(encoding="utf-8")
        patches_submit_job = re.search(
            r"monkeypatch\.setattr\([^)]*submit_background_job",
            source,
            flags=re.DOTALL,
        )
        defines_local_capture = re.search(r"^\s*def capture_job\(", source, flags=re.MULTILINE)
        if patches_submit_job or defines_local_capture:
            offenders.append(path.relative_to(PROJECT_ROOT).as_posix())

    assert offenders == []
