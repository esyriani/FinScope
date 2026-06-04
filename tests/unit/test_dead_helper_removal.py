"""Static checks for removed legacy production helpers."""

import ast
from pathlib import Path


REMOVED_FUNCTIONS = {
    "chunk_candidate_options",
    "count_users",
    "delete_user_setting",
    "explain_rule_win",
    "parse_settings_form",
    "run_transaction_ai_categorization",
    "statement_unknown_transaction_rows",
}


def test_legacy_helper_functions_are_not_defined():
    """Verify verified-dead compatibility helpers stay removed."""
    definitions = set()
    for path in Path("src/finance_app").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        definitions.update(
            node.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        )

    assert REMOVED_FUNCTIONS.isdisjoint(definitions)
