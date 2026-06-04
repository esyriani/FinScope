"""Maintainability checks for refactored application modules."""

import ast
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def source_line_count(relative_path):
    """Return the physical line count for a project source file."""
    path = PROJECT_ROOT / relative_path
    return len(path.read_text(encoding="utf-8").splitlines())


def function_line_count(relative_path, function_name):
    """Return the source line count for a top-level function."""
    path = PROJECT_ROOT / relative_path
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == function_name:
            return node.end_lineno - node.lineno + 1
    raise AssertionError(f"{function_name} not found in {relative_path}")


def test_refactored_modules_stay_below_review_size():
    """Keep formerly oversized modules from regrowing past reviewable size."""
    assert source_line_count("src/finance_app/modules/rules/audit_presenter.py") <= 600
    assert source_line_count("src/finance_app/modules/upload/workflow.py") <= 1100
    assert source_line_count("src/finance_app/modules/categories/llm.py") <= 950


def test_dashboard_context_builder_stays_as_orchestration():
    """Keep dashboard context building split across focused helpers."""
    assert function_line_count(
        "src/finance_app/modules/dashboard/service.py",
        "build_dashboard_context",
    ) <= 20
