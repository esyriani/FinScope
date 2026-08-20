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
    assert source_line_count("src/finance_app/modules/categories/llm.py") <= 650
    assert source_line_count("src/finance_app/modules/categories/llm_results.py") <= 400
    assert source_line_count("src/finance_app/modules/categories/llm_rules.py") <= 120
    assert source_line_count("src/finance_app/modules/categories/llm_workflow.py") <= 140
    assert source_line_count("src/finance_app/modules/comparison/anomaly_insights.py") <= 180
    assert source_line_count("src/finance_app/modules/comparison/change_metrics.py") <= 120
    assert source_line_count("src/finance_app/modules/comparison/insight_cards.py") <= 320
    assert source_line_count("src/finance_app/modules/comparison/insight_scoring.py") <= 320
    assert source_line_count("src/finance_app/modules/comparison/insights.py") <= 550
    assert source_line_count("src/finance_app/modules/comparison/merchant_insights.py") <= 380
    assert source_line_count("src/finance_app/modules/comparison/presenter.py") <= 500
    assert source_line_count("src/finance_app/modules/home/insights.py") <= 320
    assert source_line_count("src/finance_app/modules/home/permissions.py") <= 80
    assert source_line_count("src/finance_app/modules/home/presenter.py") <= 470
    assert source_line_count("src/finance_app/modules/home/queries.py") <= 320
    assert source_line_count("src/finance_app/modules/home/service.py") <= 220
    assert source_line_count("src/finance_app/modules/home/sharing.py") <= 120
    assert source_line_count("src/finance_app/modules/reports/export_presenter.py") <= 240
    assert source_line_count("src/finance_app/modules/reports/presenter.py") <= 1100
    assert source_line_count("src/finance_app/modules/reports/query_data.py") <= 600
    assert source_line_count("src/finance_app/modules/reports/service.py") <= 450
    assert source_line_count("src/finance_app/modules/reports/taxonomy_detail_presenter.py") <= 340
    assert source_line_count("src/finance_app/modules/rules/audit.py") <= 760
    assert source_line_count("src/finance_app/modules/rules/audit_presenter.py") <= 600
    assert source_line_count("src/finance_app/modules/rules/audit_preview.py") <= 260
    assert source_line_count("src/finance_app/modules/rules/audit_preview_impacts.py") <= 460
    assert source_line_count("src/finance_app/modules/rules/audit_preview_types.py") <= 80
    assert source_line_count("src/finance_app/modules/rules/engine.py") <= 220
    assert source_line_count("src/finance_app/modules/rules/presenter.py") <= 80
    assert source_line_count("src/finance_app/modules/rules/queries.py") <= 160
    assert source_line_count("src/finance_app/modules/settings/runtime.py") <= 260
    assert source_line_count("src/finance_app/modules/statements/types.py") <= 260
    assert source_line_count("src/finance_app/modules/upload/ai_workflow.py") <= 520
    assert source_line_count("src/finance_app/modules/upload/presenter.py") <= 60
    assert source_line_count("src/finance_app/modules/upload/preview.py") <= 440
    assert source_line_count("src/finance_app/modules/upload/queries.py") <= 140
    assert source_line_count("src/finance_app/modules/upload/service.py") <= 500
    assert source_line_count("src/finance_app/modules/upload/workflow.py") <= 650
    assert source_line_count("src/finance_app/modules/transactions/ai_presenter.py") <= 280
    assert source_line_count("src/finance_app/modules/transactions/service.py") <= 720


def test_dashboard_context_builder_stays_as_orchestration():
    """Keep dashboard context building split across focused helpers."""
    assert (
        function_line_count(
            "src/finance_app/modules/dashboard/service.py",
            "build_dashboard_context",
        )
        <= 20
    )
