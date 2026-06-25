"""Configuration helper tests."""

from pathlib import Path

from finance_app.core import config as config_module
from finance_app.core.constants import PROJECT_DIR
from finance_app.database.engine import create_database_engine


def test_default_config_files_live_at_project_root():
    """Verify default config files are resolved from the repository root."""
    project_dir = Path(PROJECT_DIR)

    assert config_module.CONFIG_PATH == project_dir / "config.ini"
    assert config_module.EXAMPLE_CONFIG_PATH == project_dir / "config.example.ini"


def test_relative_config_paths_resolve_from_project_root():
    """Resolve config file paths relative to the repository root."""
    assert config_module.resolve_path("runtime/finescope.db") == Path(PROJECT_DIR) / "runtime" / "finescope.db"


def test_database_url_defaults_to_sqlite_path(monkeypatch, tmp_path):
    """Load a SQLite URL derived from the configured database path."""
    database_path = tmp_path / "finance.db"
    monkeypatch.delenv("FINANCE_DATABASE_URL", raising=False)
    monkeypatch.setenv("FINANCE_DB_PATH", str(database_path))

    settings = config_module.load_settings(tmp_path / "missing.ini")

    assert settings.database_path == database_path
    assert settings.database_url == config_module.sqlite_database_url(database_path)


def test_sqlite_database_url_sets_database_path(monkeypatch, tmp_path):
    """Use a SQLite URL as the source of the filesystem database path."""
    database_path = tmp_path / "finance url.db"
    database_url = config_module.sqlite_database_url(database_path)
    monkeypatch.setenv("FINANCE_DATABASE_URL", database_url)

    settings = config_module.load_settings(tmp_path / "missing.ini")

    assert settings.database_url == database_url
    assert settings.database_path == database_path


def test_non_sqlite_database_url_preserves_configured_path(monkeypatch, tmp_path):
    """Keep the configured SQLite path when a non-SQLite URL is supplied."""
    database_path = tmp_path / "fallback.db"
    database_url = "mysql+pymysql://user:password@localhost/finscope"
    monkeypatch.setenv("FINANCE_DB_PATH", str(database_path))
    monkeypatch.setenv("FINANCE_DATABASE_URL", database_url)

    settings = config_module.load_settings(tmp_path / "missing.ini")

    assert settings.database_path == database_path
    assert settings.database_url == database_url
    assert config_module.database_dialect(settings.database_url) == "mysql"


def test_sqlite_memory_url_is_preserved():
    """Represent SQLite in-memory URLs without filesystem resolution."""
    assert config_module.sqlite_path_from_database_url("sqlite:///:memory:") == Path(":memory:")


def test_create_database_engine_uses_sqlalchemy_url():
    """Create a SQLAlchemy engine from an explicit database URL."""
    engine = create_database_engine("sqlite:///:memory:")

    assert engine.url.drivername == "sqlite"


def test_statement_upload_extensions_default_to_csv_only(monkeypatch, tmp_path):
    """Verify statement uploads default to CSV-only support."""
    monkeypatch.delenv("FINANCE_ALLOWED_EXTENSIONS", raising=False)
    settings = config_module.load_settings(tmp_path / "missing.ini")

    assert settings.allowed_statement_extensions == {"csv"}


def test_setting_defaults_include_runtime_settings_seed_values(monkeypatch, tmp_path):
    """Verify Settings-page runtime defaults are configurable."""
    monkeypatch.setenv("FINANCE_DEFAULT_THEME_MODE", "light")
    monkeypatch.setenv("FINANCE_DEFAULT_UI_LANGUAGE", "fr-CA")
    monkeypatch.setenv("FINANCE_DEFAULT_COMPARISON_INSIGHT_CARD_LIMIT", "9")
    monkeypatch.setenv("FINANCE_DEFAULT_DASHBOARD_TOP_DRIVER_LIMIT", "8")
    monkeypatch.setenv("FINANCE_DEFAULT_PINNED_REPORT_LIMIT", "6")
    monkeypatch.setenv("FINANCE_DEFAULT_MERCHANT_SUGGESTION_LIMIT", "6")
    monkeypatch.setenv("FINANCE_DEFAULT_LLM_REVIEW_THRESHOLD", "0.62")
    monkeypatch.setenv("FINANCE_DEFAULT_TRANSACTION_AI_RERUN_ENABLED", "false")
    monkeypatch.setenv("FINANCE_DEFAULT_CONFIRM_AI_TOKEN_USAGE_ENABLED", "false")
    monkeypatch.setenv("FINANCE_DEFAULT_RECURRENCE_MINIMUM_OCCURRENCES", "4")
    monkeypatch.setenv("FINANCE_DEFAULT_RECURRENCE_DATE_TOLERANCE_DAYS", "6")
    monkeypatch.setenv("FINANCE_DEFAULT_RECURRENCE_AMOUNT_TOLERANCE_ABSOLUTE", "12.5")
    monkeypatch.setenv("FINANCE_DEFAULT_RECURRENCE_AMOUNT_TOLERANCE_PERCENT", "0.2")
    monkeypatch.setenv("FINANCE_DEFAULT_RECURRENCE_MISSED_CYCLES_BEFORE_INACTIVE", "3")

    settings = config_module.load_settings(tmp_path / "missing.ini")

    assert settings.default_theme_mode == "light"
    assert settings.default_ui_language == "fr"
    assert settings.default_comparison_insight_card_limit == 9
    assert settings.default_dashboard_top_driver_limit == 8
    assert settings.default_pinned_report_limit == 6
    assert settings.default_merchant_suggestion_limit == 6
    assert settings.default_llm_review_threshold == 0.62
    assert settings.default_transaction_ai_rerun_enabled is False
    assert settings.default_confirm_ai_token_usage_enabled is False
    assert settings.default_recurrence_minimum_occurrences == 4
    assert settings.default_recurrence_date_tolerance_days == 6
    assert settings.default_recurrence_amount_tolerance_absolute == 12.5
    assert settings.default_recurrence_amount_tolerance_percent == 0.2
    assert settings.default_recurrence_missed_cycles_before_inactive == 3


def test_invalid_integer_settings_fall_back_without_import_crash(monkeypatch, tmp_path):
    """Verify malformed integer config values do not crash settings loading."""
    monkeypatch.setenv("FINANCE_MAX_UPLOAD_MB", "not-an-int")
    monkeypatch.setenv("FINANCE_PORT", "70000")
    monkeypatch.setenv("FINANCE_DEFAULT_COMPARISON_INSIGHT_CARD_LIMIT", "20")
    monkeypatch.setenv("FINANCE_DEFAULT_HOME_TOP_CATEGORY_LIMIT", "20")
    monkeypatch.setenv("FINANCE_DEFAULT_DASHBOARD_TOP_DRIVER_LIMIT", "20")
    monkeypatch.setenv("FINANCE_DEFAULT_PINNED_REPORT_LIMIT", "20")

    settings = config_module.load_settings(tmp_path / "missing.ini")

    assert settings.max_upload_mb == 16
    assert settings.server_port == 5000
    assert settings.default_comparison_insight_card_limit == 12
    assert settings.default_home_top_category_limit == 12
    assert settings.default_dashboard_top_driver_limit == 12
    assert settings.default_pinned_report_limit == 12


def test_invalid_float_settings_fall_back_without_import_crash(monkeypatch, tmp_path):
    """Verify malformed float config values fall back during settings loading."""
    monkeypatch.setenv("FINANCE_DEFAULT_RECURRENCE_AMOUNT_TOLERANCE_ABSOLUTE", "-1")
    monkeypatch.setenv("FINANCE_DEFAULT_RECURRENCE_AMOUNT_TOLERANCE_PERCENT", "1.5")

    settings = config_module.load_settings(tmp_path / "missing.ini")

    assert settings.default_recurrence_amount_tolerance_absolute == 10.0
    assert settings.default_recurrence_amount_tolerance_percent == 0.15


def test_development_secret_key_is_rejected_for_non_local_runs(monkeypatch, tmp_path):
    """Verify the known development secret cannot be used on network binds."""
    monkeypatch.setenv("FINANCE_HOST", "0.0.0.0")
    monkeypatch.setenv("FINANCE_DEBUG", "false")
    monkeypatch.delenv("FINANCE_SECRET_KEY", raising=False)

    try:
        config_module.load_settings(tmp_path / "missing.ini")
    except ValueError as exc:
        assert "development secret key" in str(exc)
    else:
        raise AssertionError("Expected non-local dev-secret-key configuration to fail")


def test_secure_cookie_default_tracks_bind_host(monkeypatch, tmp_path):
    """Verify loopback defaults to HTTP-friendly cookies and network binds do not."""
    monkeypatch.setenv("FINANCE_HOST", "127.0.0.1")
    local_settings = config_module.load_settings(tmp_path / "missing.ini")

    monkeypatch.setenv("FINANCE_HOST", "0.0.0.0")
    monkeypatch.setenv("FINANCE_SECRET_KEY", "test-secret-key")
    network_settings = config_module.load_settings(tmp_path / "missing.ini")

    monkeypatch.setenv("FINANCE_SECURE_COOKIES", "false")
    explicit_settings = config_module.load_settings(tmp_path / "missing.ini")

    assert local_settings.secure_cookies is False
    assert network_settings.secure_cookies is True
    assert explicit_settings.secure_cookies is False


def test_app_cookie_security_uses_configured_setting(app):
    """Verify Flask cookie security follows settings instead of debug mode."""
    assert app.config["SESSION_COOKIE_SECURE"] is False
    assert app.config["REMEMBER_COOKIE_SECURE"] is False


def test_database_dialect_reads_sqlalchemy_driver_prefix():
    """Read the database dialect from the configured SQLAlchemy URL."""
    assert config_module.database_dialect("mysql+pymysql://user:password@localhost/finscope") == "mysql"
