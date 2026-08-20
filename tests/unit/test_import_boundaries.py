"""Static tests for module import boundaries."""

import ast
from pathlib import Path

PRODUCTION_ROOT = Path("src/finance_app")


def imported_modules_from(path: Path) -> list[str]:
    """Return absolute import module names from one Python source file."""
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported_modules = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported_modules.append(node.module or "")
    return imported_modules


def test_core_and_database_do_not_import_feature_modules():
    """Verify low-level packages do not depend upward on feature modules."""
    offenders: list[str] = []
    for package in ("core", "database"):
        for path in (PRODUCTION_ROOT / package).rglob("*.py"):
            for module in imported_modules_from(path):
                if module == "finance_app.modules" or module.startswith("finance_app.modules."):
                    offenders.append(f"{path}: {module}")

    assert offenders == []


def test_recurring_package_does_not_import_calendar_package():
    """Verify recurring owns recurring activity without depending on calendar internals."""
    offenders: list[str] = []
    for path in (PRODUCTION_ROOT / "modules" / "recurring").rglob("*.py"):
        for module in imported_modules_from(path):
            if module == "finance_app.modules.calendar" or module.startswith("finance_app.modules.calendar."):
                offenders.append(f"{path}: {module}")

    assert offenders == []


def test_dashboard_and_reports_packages_do_not_import_each_other():
    """Verify shared analytics helpers stay outside Dashboard and Reports packages."""
    package_boundaries = {
        "dashboard": "finance_app.modules.reports",
        "reports": "finance_app.modules.dashboard",
    }
    offenders: list[str] = []
    for package, forbidden_prefix in package_boundaries.items():
        for path in (PRODUCTION_ROOT / "modules" / package).rglob("*.py"):
            for module in imported_modules_from(path):
                if module == forbidden_prefix or module.startswith(f"{forbidden_prefix}."):
                    offenders.append(f"{path}: {module}")

    assert offenders == []


def test_app_factory_does_not_import_render_time_database_helpers():
    """Verify app-wide template context is delegated outside the app factory."""
    imported_modules = imported_modules_from(PRODUCTION_ROOT / "__init__.py")
    source = (PRODUCTION_ROOT / "__init__.py").read_text(encoding="utf-8")

    assert "finance_app.modules.categories.service" not in imported_modules
    assert "finance_app.modules.settings.runtime" not in imported_modules
    assert "get_builtin_category_names" not in source
    assert "get_setting_with_fallback" not in source


def test_controllers_do_not_open_database_transactions():
    """Verify Flask controllers leave database transaction ownership to services/workflows."""
    offenders: list[str] = []
    for path in (PRODUCTION_ROOT / "modules").rglob("controller.py"):
        source = path.read_text(encoding="utf-8")
        if "db_core_transaction" in source:
            offenders.append(str(path))

    assert offenders == []


def test_settings_runtime_does_not_import_auth_package():
    """Verify settings runtime stays independent of auth registration imports."""
    imported_modules = imported_modules_from(Path("src/finance_app/modules/settings/runtime.py"))

    assert "finance_app.modules.auth" not in imported_modules
    assert not any(module.startswith("finance_app.modules.auth.") for module in imported_modules)


def test_auth_permissions_does_not_lazily_import_auth_service():
    """Verify auth guards receive collaborators instead of importing service lazily."""
    source = Path("src/finance_app/modules/auth/permissions.py").read_text(encoding="utf-8")

    assert "from finance_app.modules.auth.service import" not in source
