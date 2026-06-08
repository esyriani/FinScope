"""Configuration loading helpers."""

from configparser import ConfigParser
from dataclasses import dataclass
import os
from pathlib import Path
from urllib.parse import quote, unquote

from finance_app.core.constants import BASE_DIR


CONFIG_PATH = Path(os.environ.get("FINANCE_CONFIG_FILE", Path(BASE_DIR) / "config.ini"))
EXAMPLE_CONFIG_PATH = Path(BASE_DIR) / "config.example.ini"
DEVELOPMENT_SECRET_KEY = "dev-secret-key"
LOCAL_BIND_HOSTS = {"127.0.0.1", "localhost", "::1"}


@dataclass(frozen=True)
class AppSettings:
    """Represent app settings."""
    config_path: Path
    secret_key: str
    timezone: str
    locale: str
    currency: str
    currency_symbol: str
    max_upload_mb: int
    database_path: Path
    database_url: str
    server_host: str
    server_port: int
    server_debug: bool
    secure_cookies: bool
    allowed_statement_extensions: set[str]
    openai_api_key: str
    default_table_page_size: int
    default_comparison_max_years: int
    default_comparison_insight_card_limit: int
    default_home_top_category_limit: int
    default_merchant_table_limit: int
    default_rule_preview_limit: int
    default_rule_audit_transaction_limit: int
    default_llm_confidence_threshold: float
    default_llm_review_threshold: float
    default_verify_threshold: float
    default_transaction_ai_rerun_enabled: bool
    default_categorization_model: str

    @property
    def max_content_length(self):
        """Handle max content length."""
        return self.max_upload_mb * 1024 * 1024


def load_settings(config_path=CONFIG_PATH):
    """Load settings."""
    parser = ConfigParser()
    parser.read(EXAMPLE_CONFIG_PATH, encoding="utf-8")
    parser.read(config_path, encoding="utf-8")

    database_path_setting = env(
        "FINANCE_DB_PATH",
        parser.get("database", "path", fallback="finance.db"),
    )
    database_url = env(
        "FINANCE_DATABASE_URL",
        parser.get("database", "url", fallback=""),
    )
    database_path = sqlite_path_from_database_url(database_url) or resolve_path(database_path_setting)
    database_url = database_url.strip() or sqlite_database_url(database_path)

    allowed_extensions = {
        extension.strip().lower().lstrip(".")
        for extension in env(
            "FINANCE_ALLOWED_EXTENSIONS",
            parser.get("uploads", "allowed_extensions", fallback="csv"),
        ).split(",")
        if extension.strip()
    }

    server_host = env("FINANCE_HOST", parser.get("server", "host", fallback="127.0.0.1")).strip() or "127.0.0.1"
    server_debug = parse_bool(env("FINANCE_DEBUG", parser.get("server", "debug", fallback="true")))
    secret_key = validate_secret_key(
        env("FINANCE_SECRET_KEY", parser.get("app", "secret_key", fallback=DEVELOPMENT_SECRET_KEY)),
        server_debug=server_debug,
        server_host=server_host,
    )

    return AppSettings(
        config_path=Path(config_path),
        secret_key=secret_key,
        timezone=env("FINANCE_TIMEZONE", parser.get("app", "timezone", fallback="America/Toronto")),
        locale=env("FINANCE_LOCALE", parser.get("app", "locale", fallback="en_CA")),
        currency=env("FINANCE_CURRENCY", parser.get("app", "currency", fallback="CAD")),
        currency_symbol=env("FINANCE_CURRENCY_SYMBOL", parser.get("app", "currency_symbol", fallback="$")),
        max_upload_mb=parse_positive_int(
            env("FINANCE_MAX_UPLOAD_MB", parser.get("app", "max_upload_mb", fallback="16")),
            16,
        ),
        database_path=database_path,
        database_url=database_url,
        server_host=server_host,
        server_port=parse_port(env("FINANCE_PORT", parser.get("server", "port", fallback="5000"))),
        server_debug=server_debug,
        secure_cookies=parse_secure_cookies(
            env("FINANCE_SECURE_COOKIES", parser.get("app", "secure_cookies", fallback="")),
            server_host,
        ),
        allowed_statement_extensions=allowed_extensions,
        openai_api_key=env("OPENAI_API_KEY", parser.get("api_keys", "openai_api_key", fallback="")),
        default_table_page_size=parse_positive_int(
            env("FINANCE_DEFAULT_TABLE_PAGE_SIZE", parser.get("setting_defaults", "table_page_size", fallback="50")),
            50,
        ),
        default_comparison_max_years=parse_positive_int(
            env("FINANCE_DEFAULT_COMPARISON_MAX_YEARS", parser.get("setting_defaults", "comparison_max_years", fallback="2")),
            2,
        ),
        default_comparison_insight_card_limit=parse_positive_int(
            env(
                "FINANCE_DEFAULT_COMPARISON_INSIGHT_CARD_LIMIT",
                parser.get("setting_defaults", "comparison_insight_card_limit", fallback="7"),
            ),
            7,
        ),
        default_home_top_category_limit=parse_positive_int(
            env("FINANCE_DEFAULT_HOME_TOP_CATEGORY_LIMIT", parser.get("setting_defaults", "home_top_category_limit", fallback="5")),
            5,
        ),
        default_merchant_table_limit=parse_positive_int(
            env("FINANCE_DEFAULT_MERCHANT_TABLE_LIMIT", parser.get("setting_defaults", "merchant_table_limit", fallback="10")),
            10,
        ),
        default_rule_preview_limit=parse_positive_int(
            env("FINANCE_DEFAULT_RULE_PREVIEW_LIMIT", parser.get("setting_defaults", "rule_preview_limit", fallback="10")),
            10,
        ),
        default_rule_audit_transaction_limit=parse_positive_int(
            env(
                "FINANCE_DEFAULT_RULE_AUDIT_TRANSACTION_LIMIT",
                parser.get("setting_defaults", "rule_audit_transaction_limit", fallback="5000"),
            ),
            5000,
        ),
        default_llm_confidence_threshold=parse_probability(
            env("FINANCE_DEFAULT_LLM_CONFIDENCE_THRESHOLD", parser.get("setting_defaults", "llm_confidence_threshold", fallback="0.85")),
            0.85,
        ),
        default_llm_review_threshold=parse_probability(
            env("FINANCE_DEFAULT_LLM_REVIEW_THRESHOLD", parser.get("setting_defaults", "llm_review_threshold", fallback="0.60")),
            0.60,
        ),
        default_verify_threshold=parse_probability(
            env("FINANCE_DEFAULT_VERIFY_THRESHOLD", parser.get("setting_defaults", "verify_threshold", fallback="0.95")),
            0.95,
        ),
        default_transaction_ai_rerun_enabled=parse_bool(
            env(
                "FINANCE_DEFAULT_TRANSACTION_AI_RERUN_ENABLED",
                parser.get("setting_defaults", "transaction_ai_rerun_enabled", fallback="true"),
            )
        ),
        default_categorization_model=env(
            "FINANCE_DEFAULT_CATEGORIZATION_MODEL",
            parser.get("setting_defaults", "categorization_model", fallback="gpt-4o-mini"),
        ).strip() or "gpt-4o-mini",
    )


def env(name, fallback):
    """Return env."""
    value = os.environ.get(name)
    return fallback if value is None else value


def database_dialect(database_url):
    """Return the SQLAlchemy dialect name from a database URL."""
    return str(database_url or "").split(":", 1)[0].split("+", 1)[0].lower()


def sqlite_database_url(database_path):
    """Return a SQLAlchemy SQLite URL for a filesystem database path."""
    path = Path(database_path)
    if str(path) == ":memory:":
        return "sqlite:///:memory:"

    return f"sqlite:///{quote(path.as_posix(), safe='/:')}"


def sqlite_path_from_database_url(database_url):
    """Return a SQLite path from a SQLAlchemy URL when the URL targets SQLite."""
    url = str(database_url or "").strip()
    if database_dialect(url) != "sqlite":
        return None
    if url == "sqlite:///:memory:":
        return Path(":memory:")
    if not url.startswith("sqlite:///"):
        return None

    path_text = unquote(url[len("sqlite:///"):])
    if os.name == "nt" and path_text.startswith("/") and len(path_text) > 2 and path_text[2] == ":":
        path_text = path_text[1:]

    path = Path(path_text)
    return path if path.is_absolute() else resolve_path(path_text)


def parse_bool(value):
    """Parse bool."""
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def parse_secure_cookies(value, server_host):
    """Return whether browser cookies should require HTTPS transport."""
    text = str(value or "").strip().lower()
    if text:
        return parse_bool(text)
    return not is_local_bind_host(server_host)


def validate_secret_key(value, server_debug=False, server_host=""):
    """Return a usable Flask secret key or raise for unsafe deployments."""
    secret_key = str(value or "").strip()
    if not secret_key:
        raise ValueError("FINANCE_SECRET_KEY or app.secret_key must be configured.")
    if secret_key == DEVELOPMENT_SECRET_KEY and not (server_debug or is_local_bind_host(server_host)):
        raise ValueError(
            "The development secret key is only allowed for debug or loopback local runs. "
            "Set FINANCE_SECRET_KEY before binding FinScope to a non-local host."
        )
    return secret_key


def is_local_bind_host(value):
    """Return whether a configured bind host is loopback-only."""
    host = str(value or "").strip().lower()
    return host in LOCAL_BIND_HOSTS


def parse_port(value, fallback=5000):
    """Parse a TCP port number, falling back when the value is invalid."""
    parsed = parse_positive_int(value, fallback)
    return parsed if 1 <= parsed <= 65535 else fallback


def parse_positive_int(value, fallback):
    """Parse positive int."""
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return fallback

    return parsed if parsed > 0 else fallback


def parse_probability(value, fallback):
    """Parse probability."""
    try:
        parsed = float(str(value).strip())
    except (TypeError, ValueError):
        return fallback

    return parsed if 0 <= parsed <= 1 else fallback


def resolve_path(value):
    """Resolve path."""
    path = Path(value).expanduser()

    if path.is_absolute():
        return path

    return Path(BASE_DIR) / path


settings = load_settings()
