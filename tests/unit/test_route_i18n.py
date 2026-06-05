"""Static checks for route-level translation coverage."""

import ast
import json
from pathlib import Path


ROUTE_FILES = tuple(Path("src/finance_app/modules").glob("*/controller.py"))


def test_route_flash_calls_do_not_use_direct_string_literals():
    """Verify route-owned flash messages pass through translation helpers."""
    offenders = []
    for path in ROUTE_FILES:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if is_flash_call(node) and node.args and is_direct_string_message(node.args[0]):
                offenders.append(f"{path}:{node.lineno}")

    assert offenders == []


def test_route_gettext_literal_keys_exist_in_french_catalog():
    """Verify literal route messages are present in the French catalog."""
    catalog = json.loads(Path("src/finance_app/translations/fr.json").read_text(encoding="utf-8"))
    missing = sorted(route_gettext_literal_keys() - set(catalog))

    assert missing == []


def route_gettext_literal_keys():
    """Return literal gettext keys from the route controllers under review."""
    keys = set()
    for path in ROUTE_FILES:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if is_gettext_call(node) and node.args:
                keys.update(gettext_literal_values(node.args[0]))
    return keys


def is_flash_call(node):
    """Return whether an AST call invokes Flask's flash helper by name."""
    return isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "flash"


def is_gettext_call(node):
    """Return whether an AST call invokes the route gettext helper by name."""
    return isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "gettext"


def is_direct_string_message(node):
    """Return whether a flash argument is a direct string or f-string."""
    return isinstance(node, (ast.Constant, ast.JoinedStr)) and (
        not isinstance(node, ast.Constant) or isinstance(node.value, str)
    )


def gettext_literal_values(node):
    """Return string constants used as direct or conditional gettext keys."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return {node.value}
    if isinstance(node, ast.IfExp):
        return gettext_literal_values(node.body) | gettext_literal_values(node.orelse)
    return set()
