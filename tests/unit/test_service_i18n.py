"""Static checks for service/workflow translation catalog coverage."""

import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FRENCH_CATALOG = ROOT / "src" / "finance_app" / "translations" / "fr.json"

SERVICE_MESSAGE_SOURCE_FILES = (
    ROOT / "src" / "finance_app" / "core" / "csrf.py",
    ROOT / "src" / "finance_app" / "modules" / "auth" / "permissions.py",
    ROOT / "src" / "finance_app" / "background" / "runner.py",
    ROOT / "src" / "finance_app" / "modules" / "auth" / "service.py",
    ROOT / "src" / "finance_app" / "modules" / "rules" / "forms.py",
    ROOT / "src" / "finance_app" / "modules" / "rules" / "service.py",
    ROOT / "src" / "finance_app" / "modules" / "rules" / "import_export.py",
    ROOT / "src" / "finance_app" / "modules" / "rules" / "audit_preview.py",
    ROOT / "src" / "finance_app" / "modules" / "rules" / "workflow.py",
    ROOT / "src" / "finance_app" / "modules" / "taxonomy_admin" / "forms.py",
    ROOT / "src" / "finance_app" / "modules" / "taxonomy_admin" / "service.py",
    ROOT / "src" / "finance_app" / "modules" / "upload" / "workflow.py",
    ROOT / "src" / "finance_app" / "modules" / "review" / "workflow.py",
)

TRANSLATED_DICT_KEYS = {"message", "error", "summary"}


def test_french_catalog_covers_user_facing_service_messages():
    """Verify known service/workflow boundary messages translate to French."""
    catalog = json.loads(FRENCH_CATALOG.read_text(encoding="utf-8"))

    assert sorted(service_message_literals() - set(catalog)) == []


def service_message_literals():
    """Return literal service/workflow messages surfaced through controllers."""
    messages = set()
    for path in SERVICE_MESSAGE_SOURCE_FILES:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        visitor = ServiceMessageVisitor()
        visitor.visit(tree)
        messages.update(visitor.messages)
    return messages


class ServiceMessageVisitor(ast.NodeVisitor):
    """Collect literal user-facing messages from selected service boundaries."""

    def __init__(self) -> None:
        self.messages: set[str] = set()

    def visit_Raise(self, node):
        """Collect literal exception messages translated by controllers."""
        exc = node.exc
        if isinstance(exc, ast.Call) and exc.args:
            self._add_constant_string(exc.args[0])
        self.generic_visit(node)

    def visit_Return(self, node):
        """Collect literal job/result strings translated at display time."""
        self._add_constant_string(node.value)
        self.generic_visit(node)

    def visit_Dict(self, node):
        """Collect literal message values from result dictionaries."""
        for key, value in zip(node.keys, node.values):
            if isinstance(key, ast.Constant) and key.value in TRANSLATED_DICT_KEYS:
                self._add_constant_string(value)
        self.generic_visit(node)

    def visit_Call(self, node):
        """Collect literal gettext calls in non-controller boundary helpers."""
        if isinstance(node.func, ast.Name) and node.func.id == "gettext" and node.args:
            self._add_constant_string(node.args[0])
        self.generic_visit(node)

    def _add_constant_string(self, node):
        """Add sentence-like string constants and skip internal ids."""
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value.endswith("."):
            self.messages.add(node.value)
