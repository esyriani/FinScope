"""Static tests for module import boundaries."""

import ast
from pathlib import Path


def test_settings_runtime_does_not_import_auth_package():
    """Verify settings runtime stays independent of auth registration imports."""
    source = Path("src/finance_app/modules/settings/runtime.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported_modules = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported_modules.append(node.module or "")

    assert "finance_app.modules.auth" not in imported_modules
    assert not any(module.startswith("finance_app.modules.auth.") for module in imported_modules)


def test_auth_permissions_does_not_lazily_import_auth_service():
    """Verify auth guards receive collaborators instead of importing service lazily."""
    source = Path("src/finance_app/modules/auth/permissions.py").read_text(encoding="utf-8")

    assert "from finance_app.modules.auth.service import" not in source
