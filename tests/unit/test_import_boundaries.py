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


def test_controllers_do_not_open_database_transactions():
    """Verify Flask controllers leave database transaction ownership to services/workflows."""
    offenders: list[str] = []
    for path in (PRODUCTION_ROOT / "modules").rglob("controller.py"):
        source = path.read_text(encoding="utf-8")
        if "db_core_transaction" in source:
            offenders.append(str(path))

    assert offenders == []


def test_high_traffic_services_delegate_read_sql_and_presentation_boundaries():
    """Verify broad page services keep SQL and view shaping in focused modules."""
    home_service_imports = imported_modules_from(PRODUCTION_ROOT / "modules" / "home" / "service.py")
    transaction_service_imports = imported_modules_from(PRODUCTION_ROOT / "modules" / "transactions" / "service.py")
    upload_service_source = (PRODUCTION_ROOT / "modules" / "upload" / "service.py").read_text(encoding="utf-8")
    transaction_service_source = (PRODUCTION_ROOT / "modules" / "transactions" / "service.py").read_text(
        encoding="utf-8"
    )

    assert not any(module == "sqlalchemy" or module.startswith("sqlalchemy.") for module in home_service_imports)
    assert "finance_app.database.tables" not in home_service_imports
    assert "finance_app.modules.auth.permissions" not in home_service_imports
    assert "finance_app.modules.users.repository" not in home_service_imports

    assert not any(module == "sqlalchemy" or module.startswith("sqlalchemy.") for module in transaction_service_imports)
    assert "finance_app.database.tables" not in transaction_service_imports

    assert "select(" not in upload_service_source
    assert "func." not in upload_service_source
    assert "accounts_table" not in upload_service_source
    assert "statement_types_table" not in upload_service_source
    assert "statements_table" not in upload_service_source
    assert "transactions_table" not in upload_service_source

    assert "import json" not in transaction_service_source
    assert "TRANSACTION_KINDS" not in transaction_service_source
    assert "category_source_label" not in transaction_service_source
    assert "category_source_badge_class" not in transaction_service_source
    assert "category_confidence_label" not in transaction_service_source


def test_rules_engine_stays_pure_domain_logic():
    """Verify the rules engine does not own persistence, settings, jobs, or presentation."""
    imported_modules = imported_modules_from(PRODUCTION_ROOT / "modules" / "rules" / "engine.py")
    source = (PRODUCTION_ROOT / "modules" / "rules" / "engine.py").read_text(encoding="utf-8")
    forbidden_prefixes = (
        "sqlalchemy",
        "finance_app.background",
        "finance_app.database",
        "finance_app.modules.settings",
        "finance_app.modules.categories.repository",
        "finance_app.modules.categories.service",
        "finance_app.modules.categories.taxonomy",
        "finance_app.modules.merchants.repository",
        "finance_app.modules.merchants.sql_filters",
    )

    offenders = [
        module
        for module in imported_modules
        if any(module == forbidden or module.startswith(f"{forbidden}.") for forbidden in forbidden_prefixes)
    ]
    assert offenders == []
    assert "db_core_transaction" not in source
    assert "select(" not in source
    assert "update(" not in source
    assert "format_money" not in source


def test_settings_runtime_does_not_import_auth_package():
    """Verify settings runtime stays independent of auth registration imports."""
    imported_modules = imported_modules_from(Path("src/finance_app/modules/settings/runtime.py"))

    assert "finance_app.modules.auth" not in imported_modules
    assert not any(module.startswith("finance_app.modules.auth.") for module in imported_modules)


def test_settings_runtime_does_not_own_statement_type_configuration():
    """Verify statement import configuration stays outside runtime settings."""
    source = Path("src/finance_app/modules/settings/runtime.py").read_text(encoding="utf-8")

    assert "statement_types" not in source
    assert "normalize_statement_parser_type" not in source
    assert "sync_statement_types" not in source


def test_settings_runtime_readers_use_caller_owned_connections():
    """Verify runtime settings do not expose connection-owning read shortcuts."""
    source = Path("src/finance_app/modules/settings/runtime.py").read_text(encoding="utf-8")
    imported_modules = imported_modules_from(Path("src/finance_app/modules/settings/runtime.py"))

    assert "finance_app.database.engine" not in imported_modules
    assert "db_core_connection" not in source
    assert "def get_setting_with_fallback" not in source


def test_request_read_paths_do_not_perform_lazy_seed_writes():
    """Verify request/read helpers do not silently repair seed-owned data."""
    category_repository_source = Path("src/finance_app/modules/categories/repository.py").read_text(encoding="utf-8")
    settings_service_source = Path("src/finance_app/modules/settings/service.py").read_text(encoding="utf-8")

    assert "seed_category_taxonomy" not in category_repository_source
    assert "seed_runtime_settings_defaults" not in settings_service_source


def test_auth_permissions_does_not_lazily_import_auth_service():
    """Verify auth guards receive collaborators instead of importing service lazily."""
    source = Path("src/finance_app/modules/auth/permissions.py").read_text(encoding="utf-8")

    assert "from finance_app.modules.auth.service import" not in source
