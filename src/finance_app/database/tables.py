"""SQLAlchemy Core table metadata.

Defines portable SQLAlchemy table objects that mirror the current FinScope
schema. Runtime initialization creates the clean schema through SQLAlchemy Core
for SQLite and MySQL deployments.
"""

from sqlalchemy import (
    CheckConstraint,
    Column,
    Computed,
    ForeignKey,
    Float,
    Index,
    Integer,
    MetaData,
    Numeric,
    String,
    Table,
    Text,
    UniqueConstraint,
    column,
    func,
    text,
)
from sqlalchemy.dialects import mysql

from finance_app.database.dates import ISODate, UTCDateTime
from finance_app.core.constants import (
    ACCOUNT_TYPE_CHECKING,
    ACCOUNT_TYPES,
    CATEGORY_RULE_DIRECTION_ANY,
    CATEGORY_RULE_DIRECTIONS,
    CATEGORY_RULE_SOURCE_MANUAL,
    CATEGORY_RULE_SOURCES,
    CATEGORY_SOURCE_UNKNOWN,
    CATEGORY_SOURCES,
    DATE_ORDER_AUTO,
    DATE_ORDERS,
    INTERAC_DIRECTIONS,
    INTERAC_DIRECTION_AUTO,
    RECURRING_PATTERN_TYPES,
    RECURRING_USER_STATUS_DETECTED,
    RECURRING_USER_STATUSES,
    STATEMENT_IMPORT_MODE_LEDGER,
    STATEMENT_IMPORT_MODES,
    STATEMENT_IMPORT_STATUS_PENDING,
    STATEMENT_IMPORT_STATUSES,
    STATEMENT_TYPE_PARSER_CREDIT_CARD,
    STATEMENT_TYPE_PARSER_TYPES,
    TRANSACTION_KIND_EXPENSE,
    TRANSACTION_KINDS,
    TRANSACTION_TAG_SOURCES,
    USER_ROLES,
)


CONSTRAINT_NAMING_CONVENTION = {
    "ix": "idx_%(table_name)s_%(column_0_name)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

metadata = MetaData(naming_convention=CONSTRAINT_NAMING_CONVENTION)

MYSQL_TABLE_OPTIONS = {
    "mysql_engine": "InnoDB",
    "mysql_charset": "utf8mb4",
    "mysql_collate": "utf8mb4_unicode_ci",
}
AUTOINCREMENT_TABLE_OPTIONS = {
    **MYSQL_TABLE_OPTIONS,
    "sqlite_autoincrement": True,
}


MONEY_AMOUNT_TYPE = Numeric(14, 2)
MONEY_NULL_SENTINEL_SQL = "-999999999999.00"
DATE_TYPE = ISODate()
TIMESTAMP_TYPE = UTCDateTime()
PASSWORD_HASH_TYPE = Text().with_variant(
    mysql.VARCHAR(255, charset="utf8mb4", collation="utf8mb4_bin"),
    "mysql",
)
PASSWORD_HASH_TYPE = PASSWORD_HASH_TYPE.with_variant(
    mysql.VARCHAR(255, charset="utf8mb4", collation="utf8mb4_bin"),
    "mariadb",
)


def allowed_values_check_sql(column_name, values):
    """Return a SQL CHECK expression for enum-like persisted text values."""
    allowed_values = ", ".join(f"'{value}'" for value in sorted(values))
    return f"{column_name} IN ({allowed_values})"


def allowed_values_constraint(column_name, values, name):
    """Return a named CHECK constraint for a constrained text column."""
    return CheckConstraint(allowed_values_check_sql(column_name, values), name=name)


def non_empty_constraint(column_name, name):
    """Return a named CHECK constraint that rejects blank text values."""
    return CheckConstraint(func.trim(column(column_name)) != "", name=name)


users = Table(
    "users",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("username", String(150), nullable=False),
    Column("display_name", String(150), nullable=False),
    Column("password_hash", PASSWORD_HASH_TYPE, nullable=False),
    Column("role", String(32), nullable=False),
    Column("is_active", Integer, nullable=False, server_default=text("1")),
    Column("must_change_password", Integer, nullable=False, server_default=text("0")),
    Column("created_at", TIMESTAMP_TYPE, nullable=False, server_default=text("CURRENT_TIMESTAMP")),
    Column("updated_at", TIMESTAMP_TYPE, nullable=False, server_default=text("CURRENT_TIMESTAMP")),
    Column("last_login_at", TIMESTAMP_TYPE),
    Column("failed_login_count", Integer, nullable=False, server_default=text("0")),
    Column("locked_until", TIMESTAMP_TYPE),
    UniqueConstraint("username", name="uq_users_username"),
    non_empty_constraint("username", "users_username_non_empty"),
    non_empty_constraint("display_name", "users_display_name_non_empty"),
    allowed_values_constraint("role", USER_ROLES, "users_role_allowed"),
    CheckConstraint("is_active IN (0, 1)", name="users_is_active_bool"),
    CheckConstraint("must_change_password IN (0, 1)", name="users_must_change_password_bool"),
    CheckConstraint("failed_login_count >= 0", name="users_failed_login_count_non_negative"),
    **AUTOINCREMENT_TABLE_OPTIONS,
)

user_settings = Table(
    "user_settings",
    metadata,
    Column("user_id", Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
    Column("key", String(255), primary_key=True),
    Column("value", Text, nullable=False),
    Column("updated_at", TIMESTAMP_TYPE, nullable=False, server_default=text("CURRENT_TIMESTAMP")),
    non_empty_constraint("key", "user_settings_key_non_empty"),
    **MYSQL_TABLE_OPTIONS,
)

audit_log = Table(
    "audit_log",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
    Column("username", String(150)),
    Column("action", String(64), nullable=False),
    Column("details", Text),
    Column("ip_address", String(64)),
    Column("created_at", TIMESTAMP_TYPE, nullable=False, server_default=text("CURRENT_TIMESTAMP")),
    non_empty_constraint("action", "audit_log_action_non_empty"),
    **AUTOINCREMENT_TABLE_OPTIONS,
)


accounts = Table(
    "accounts",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("name", String(255), nullable=False),
    Column("account_type", String(32), nullable=False, server_default=ACCOUNT_TYPE_CHECKING),
    Column("paid_from_account_id", Integer, ForeignKey("accounts.id", ondelete="SET NULL")),
    UniqueConstraint("name", name="uq_accounts_name"),
    allowed_values_constraint("account_type", ACCOUNT_TYPES, "accounts_account_type_allowed"),
    non_empty_constraint("name", "accounts_name_non_empty"),
    **AUTOINCREMENT_TABLE_OPTIONS,
)

statement_types = Table(
    "statement_types",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("name", String(255), nullable=False),
    Column("parser_type", String(64), nullable=False, server_default=STATEMENT_TYPE_PARSER_CREDIT_CARD),
    Column("import_mode", String(32), nullable=False, server_default=STATEMENT_IMPORT_MODE_LEDGER),
    Column("default_account_type", String(32), nullable=False, server_default=ACCOUNT_TYPE_CHECKING),
    Column("active", Integer, nullable=False, server_default=text("1")),
    Column("created_at", TIMESTAMP_TYPE, nullable=False, server_default=text("CURRENT_TIMESTAMP")),
    UniqueConstraint("name", name="uq_statement_types_name"),
    non_empty_constraint("name", "statement_types_name_non_empty"),
    allowed_values_constraint("parser_type", STATEMENT_TYPE_PARSER_TYPES, "statement_types_parser_type_allowed"),
    allowed_values_constraint("import_mode", STATEMENT_IMPORT_MODES, "statement_types_import_mode_allowed"),
    allowed_values_constraint("default_account_type", ACCOUNT_TYPES, "statement_types_default_account_type_allowed"),
    CheckConstraint("active IN (0, 1)", name="statement_types_active_bool"),
    **AUTOINCREMENT_TABLE_OPTIONS,
)

categories = Table(
    "categories",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("name", String(255), nullable=False),
    Column("builtin_key", String(64)),
    Column("description", Text),
    Column("instruction", Text),
    Column("created_at", TIMESTAMP_TYPE, nullable=False, server_default=text("CURRENT_TIMESTAMP")),
    UniqueConstraint("name", name="uq_categories_name"),
    UniqueConstraint("builtin_key", name="uq_categories_builtin_key"),
    non_empty_constraint("name", "categories_name_non_empty"),
    **AUTOINCREMENT_TABLE_OPTIONS,
)

merchants = Table(
    "merchants",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("merchant_key", String(255), nullable=False),
    Column("created_at", TIMESTAMP_TYPE, nullable=False, server_default=text("CURRENT_TIMESTAMP")),
    Column("updated_at", TIMESTAMP_TYPE, nullable=False, server_default=text("CURRENT_TIMESTAMP")),
    UniqueConstraint("merchant_key", name="uq_merchants_merchant_key"),
    non_empty_constraint("merchant_key", "merchants_merchant_key_non_empty"),
    **AUTOINCREMENT_TABLE_OPTIONS,
)

category_rules = Table(
    "category_rules",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("account_id", Integer, ForeignKey("accounts.id", ondelete="SET NULL")),
    Column("merchant_id", Integer, ForeignKey("merchants.id", ondelete="SET NULL")),
    Column("keyword", String(255), nullable=False),
    Column("category", String(255)),
    Column("category_id", Integer, ForeignKey("categories.id", ondelete="SET NULL")),
    Column("amount_min", MONEY_AMOUNT_TYPE),
    Column("amount_max", MONEY_AMOUNT_TYPE),
    Column("direction", String(16), nullable=False, server_default=CATEGORY_RULE_DIRECTION_ANY),
    Column(
        "keyword_scope_key",
        String(255),
        Computed("CASE WHEN merchant_id IS NULL THEN keyword ELSE NULL END", persisted=True),
    ),
    Column(
        "account_id_key",
        Integer,
        Computed("COALESCE(account_id, -1)", persisted=True),
    ),
    Column(
        "amount_min_key",
        MONEY_AMOUNT_TYPE,
        Computed(f"COALESCE(amount_min, {MONEY_NULL_SENTINEL_SQL})", persisted=True),
    ),
    Column(
        "amount_max_key",
        MONEY_AMOUNT_TYPE,
        Computed(f"COALESCE(amount_max, {MONEY_NULL_SENTINEL_SQL})", persisted=True),
    ),
    Column("source", String(32), nullable=False, server_default=CATEGORY_RULE_SOURCE_MANUAL),
    Column("ai_approved", Integer, nullable=False, server_default=text("0")),
    Column("created_at", TIMESTAMP_TYPE, nullable=False, server_default=text("CURRENT_TIMESTAMP")),
    non_empty_constraint("keyword", "category_rules_keyword_non_empty"),
    allowed_values_constraint("source", CATEGORY_RULE_SOURCES, "category_rules_source_allowed"),
    allowed_values_constraint("direction", CATEGORY_RULE_DIRECTIONS, "category_rules_direction_allowed"),
    CheckConstraint("ai_approved IN (0, 1)", name="category_rules_ai_approved_bool"),
    CheckConstraint(
        "amount_min IS NULL OR amount_max IS NULL OR amount_min <= amount_max",
        name="category_rules_amount_range_order",
    ),
    UniqueConstraint(
        "keyword_scope_key",
        "account_id_key",
        "direction",
        "amount_min_key",
        "amount_max_key",
        name="uq_category_rules_keyword_amount",
    ),
    UniqueConstraint(
        "merchant_id",
        "account_id_key",
        "direction",
        "amount_min_key",
        "amount_max_key",
        name="uq_category_rules_merchant_amount",
    ),
    **AUTOINCREMENT_TABLE_OPTIONS,
)

statements = Table(
    "statements",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("account_id", Integer, ForeignKey("accounts.id")),
    Column("statement_type_id", Integer, ForeignKey("statement_types.id"), nullable=False),
    Column("filename", String(512), nullable=False),
    Column("checksum", String(128), nullable=False),
    Column("extension", String(32), nullable=False, server_default=""),
    Column("interac_direction", String(32), nullable=False, server_default=INTERAC_DIRECTION_AUTO),
    Column("date_order", String(32), nullable=False, server_default=DATE_ORDER_AUTO),
    Column("raw_text", Text),
    Column("import_status", String(32), nullable=False, server_default=STATEMENT_IMPORT_STATUS_PENDING),
    Column("import_error", Text),
    Column("import_started_at", TIMESTAMP_TYPE),
    Column("import_finished_at", TIMESTAMP_TYPE),
    Column("imported_count", Integer, nullable=False, server_default=text("0")),
    Column("skipped_count", Integer, nullable=False, server_default=text("0")),
    Column("ignored_count", Integer, nullable=False, server_default=text("0")),
    Column("llm_candidate_count", Integer, nullable=False, server_default=text("0")),
    Column("uploaded_at", TIMESTAMP_TYPE, nullable=False, server_default=text("CURRENT_TIMESTAMP")),
    UniqueConstraint("checksum", name="uq_statements_checksum"),
    non_empty_constraint("filename", "statements_filename_non_empty"),
    non_empty_constraint("checksum", "statements_checksum_non_empty"),
    allowed_values_constraint("import_status", STATEMENT_IMPORT_STATUSES, "statements_import_status_allowed"),
    allowed_values_constraint("interac_direction", INTERAC_DIRECTIONS, "statements_interac_direction_allowed"),
    allowed_values_constraint("date_order", DATE_ORDERS, "statements_date_order_allowed"),
    CheckConstraint("imported_count >= 0", name="statements_imported_count_non_negative"),
    CheckConstraint("skipped_count >= 0", name="statements_skipped_count_non_negative"),
    CheckConstraint("ignored_count >= 0", name="statements_ignored_count_non_negative"),
    CheckConstraint("llm_candidate_count >= 0", name="statements_llm_candidate_count_non_negative"),
    **AUTOINCREMENT_TABLE_OPTIONS,
)

transactions = Table(
    "transactions",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("statement_id", Integer, ForeignKey("statements.id")),
    Column("account_id", Integer, ForeignKey("accounts.id")),
    Column("merchant_id", Integer, ForeignKey("merchants.id", ondelete="SET NULL")),
    Column("tx_date", DATE_TYPE, nullable=False),
    Column("description", String(512), nullable=False),
    Column("amount", MONEY_AMOUNT_TYPE, nullable=False),
    Column("category", String(255)),
    Column("category_id", Integer, ForeignKey("categories.id", ondelete="SET NULL")),
    Column("needs_review", Integer, nullable=False, server_default=text("0")),
    Column("category_source", String(32), nullable=False, server_default=CATEGORY_SOURCE_UNKNOWN),
    Column("category_confidence", Float),
    Column("category_rule_id", Integer, ForeignKey("category_rules.id", ondelete="SET NULL")),
    Column("category_metadata", Text),
    Column("categorized_at", TIMESTAMP_TYPE),
    Column("reviewed_at", TIMESTAMP_TYPE),
    Column("ignored", Integer, nullable=False, server_default=text("0")),
    Column("transaction_kind", String(32), nullable=False, server_default=TRANSACTION_KIND_EXPENSE),
    Column("fingerprint", String(255), nullable=False),
    Column("created_at", TIMESTAMP_TYPE, nullable=False, server_default=text("CURRENT_TIMESTAMP")),
    UniqueConstraint("fingerprint", name="uq_transactions_fingerprint"),
    non_empty_constraint("description", "transactions_description_non_empty"),
    CheckConstraint("needs_review IN (0, 1)", name="transactions_needs_review_bool"),
    CheckConstraint("ignored IN (0, 1)", name="transactions_ignored_bool"),
    allowed_values_constraint("transaction_kind", TRANSACTION_KINDS, "transactions_transaction_kind_allowed"),
    allowed_values_constraint("category_source", CATEGORY_SOURCES, "transactions_category_source_allowed"),
    CheckConstraint(
        "category_confidence IS NULL OR (category_confidence >= 0 AND category_confidence <= 1)",
        name="transactions_category_confidence_probability",
    ),
    **AUTOINCREMENT_TABLE_OPTIONS,
)

recurring_patterns = Table(
    "recurring_patterns",
    metadata,
    Column("pattern_key", String(255), primary_key=True),
    Column("merchant_id", Integer, ForeignKey("merchants.id", ondelete="SET NULL")),
    Column("merchant", String(255), nullable=False),
    Column("type", String(32), nullable=False),
    Column("user_status", String(32), nullable=False, server_default=RECURRING_USER_STATUS_DETECTED),
    Column("frequency", String(64)),
    Column("expected_day", Integer),
    Column("typical_amount", MONEY_AMOUNT_TYPE),
    Column("date_tolerance_days", Integer),
    Column("amount_tolerance", MONEY_AMOUNT_TYPE),
    Column("active", Integer, nullable=False, server_default=text("1")),
    Column("created_at", TIMESTAMP_TYPE, nullable=False, server_default=text("CURRENT_TIMESTAMP")),
    Column("updated_at", TIMESTAMP_TYPE, nullable=False, server_default=text("CURRENT_TIMESTAMP")),
    allowed_values_constraint("type", RECURRING_PATTERN_TYPES, "recurring_patterns_type_allowed"),
    allowed_values_constraint("user_status", RECURRING_USER_STATUSES, "recurring_patterns_user_status_allowed"),
    CheckConstraint("active IN (0, 1)", name="recurring_patterns_active_bool"),
    CheckConstraint("expected_day IS NULL OR (expected_day >= 1 AND expected_day <= 31)", name="recurring_patterns_expected_day_range"),
    CheckConstraint("date_tolerance_days IS NULL OR date_tolerance_days >= 0", name="recurring_patterns_date_tolerance_non_negative"),
    CheckConstraint("amount_tolerance IS NULL OR amount_tolerance >= 0", name="recurring_patterns_amount_tolerance_non_negative"),
    CheckConstraint("typical_amount IS NULL OR typical_amount >= 0", name="recurring_patterns_typical_amount_non_negative"),
    UniqueConstraint("merchant_id", "type", name="uq_recurring_patterns_merchant_type"),
    **MYSQL_TABLE_OPTIONS,
)

tags = Table(
    "tags",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("name", String(255), nullable=False),
    Column("description", Text),
    Column("instruction", Text),
    Column("color", String(64)),
    Column("created_at", TIMESTAMP_TYPE, nullable=False, server_default=text("CURRENT_TIMESTAMP")),
    UniqueConstraint("name", name="uq_tags_name"),
    non_empty_constraint("name", "tags_name_non_empty"),
    **AUTOINCREMENT_TABLE_OPTIONS,
)

transaction_tags = Table(
    "transaction_tags",
    metadata,
    Column("transaction_id", Integer, ForeignKey("transactions.id", ondelete="CASCADE"), primary_key=True),
    Column("tag_id", Integer, ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True),
    Column("source", String(32), nullable=False, server_default=CATEGORY_SOURCE_UNKNOWN),
    Column("rule_id", Integer, ForeignKey("category_rules.id", ondelete="SET NULL")),
    Column("assigned_at", TIMESTAMP_TYPE, nullable=False, server_default=text("CURRENT_TIMESTAMP")),
    allowed_values_constraint("source", TRANSACTION_TAG_SOURCES, "transaction_tags_source_allowed"),
    **MYSQL_TABLE_OPTIONS,
)

category_rule_tags = Table(
    "category_rule_tags",
    metadata,
    Column("rule_id", Integer, ForeignKey("category_rules.id", ondelete="CASCADE"), primary_key=True),
    Column("tag_id", Integer, ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True),
    **MYSQL_TABLE_OPTIONS,
)


Index("idx_transactions_date", transactions.c.tx_date)
Index("idx_transactions_category", transactions.c.category)
Index("idx_transactions_category_id", transactions.c.category_id)
Index("idx_transactions_date_category", transactions.c.tx_date, transactions.c.category)
Index("idx_transactions_account", transactions.c.account_id)
Index("idx_transactions_merchant", transactions.c.merchant_id)
Index("idx_transactions_amount", transactions.c.amount)
Index("idx_transactions_description", transactions.c.description)
Index("idx_transactions_statement", transactions.c.statement_id)
Index("idx_transactions_dashboard_category_amount", transactions.c.category, transactions.c.amount)
Index("idx_transactions_dashboard_date_amount", transactions.c.tx_date, transactions.c.amount)
Index("idx_transactions_needs_review", transactions.c.needs_review)
Index("idx_transactions_ignored", transactions.c.ignored)
Index("idx_transactions_ignored_account", transactions.c.ignored, transactions.c.account_id)
Index("idx_transactions_ignored_category_id", transactions.c.ignored, transactions.c.category_id)
Index("idx_transactions_ignored_date", transactions.c.ignored, transactions.c.tx_date)
Index("idx_transactions_ignored_merchant", transactions.c.ignored, transactions.c.merchant_id)
Index("idx_transactions_category_source", transactions.c.category_source)
Index("idx_transactions_reviewed_at", transactions.c.reviewed_at)
Index("idx_transactions_kind", transactions.c.transaction_kind)

Index("idx_category_rules_keyword", category_rules.c.keyword)
Index("idx_category_rules_merchant", category_rules.c.merchant_id)
Index("idx_category_rules_account", category_rules.c.account_id)
Index("idx_category_rules_direction", category_rules.c.direction)
Index("idx_category_rules_amount_bounds", category_rules.c.amount_min, category_rules.c.amount_max)
Index("idx_category_rules_category_id", category_rules.c.category_id)
Index("idx_category_rules_source_approval", category_rules.c.source, category_rules.c.ai_approved)

Index("idx_merchants_key", merchants.c.merchant_key)
Index("idx_statement_types_active", statement_types.c.active, statement_types.c.name)
Index("idx_statements_account", statements.c.account_id)
Index("idx_statements_statement_type", statements.c.statement_type_id)
Index("idx_statements_uploaded_at", statements.c.uploaded_at)
Index("idx_recurring_patterns_status", recurring_patterns.c.user_status, recurring_patterns.c.active)
Index("idx_transaction_tags_tag", transaction_tags.c.tag_id)
Index("idx_category_rule_tags_tag", category_rule_tags.c.tag_id)
Index("idx_users_role_active", users.c.role, users.c.is_active)
Index("idx_users_locked_until", users.c.locked_until)
Index("idx_audit_log_created_at", audit_log.c.created_at)
Index("idx_audit_log_user", audit_log.c.user_id)

SCHEMA_TABLES = (
    users,
    user_settings,
    audit_log,
    accounts,
    statement_types,
    statements,
    categories,
    merchants,
    transactions,
    category_rules,
    recurring_patterns,
    tags,
    transaction_tags,
    category_rule_tags,
)
