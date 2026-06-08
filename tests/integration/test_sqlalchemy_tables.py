"""Tests for SQLAlchemy Core table metadata."""

from sqlalchemy import Float, Numeric, create_engine, inspect
from sqlalchemy.engine import create_mock_engine

from finance_app.database.dates import ISODate, UTCDateTime
from finance_app.database.tables import metadata

EXPECTED_TABLE_COLUMNS = {
    "users": [
        "id",
        "username",
        "username_key",
        "display_name",
        "password_hash",
        "role",
        "is_active",
        "owner_role_key",
        "must_change_password",
        "created_at",
        "updated_at",
        "last_login_at",
        "failed_login_count",
        "locked_until",
    ],
    "user_settings": ["user_id", "key", "value", "updated_at"],
    "audit_log": ["id", "user_id", "username", "action", "details", "ip_address", "created_at"],
    "accounts": ["id", "name", "account_type", "paid_from_account_id"],
    "statement_types": [
        "id",
        "name",
        "parser_type",
        "import_mode",
        "default_account_type",
        "active",
        "created_at",
    ],
    "statements": [
        "id",
        "account_id",
        "statement_type_id",
        "filename",
        "checksum",
        "extension",
        "interac_direction",
        "date_order",
        "raw_text",
        "import_status",
        "import_error",
        "import_started_at",
        "import_finished_at",
        "imported_count",
        "skipped_count",
        "ignored_count",
        "llm_candidate_count",
        "uploaded_at",
    ],
    "categories": ["id", "name", "builtin_key", "description", "instruction", "created_at"],
    "merchants": [
        "id",
        "merchant_key",
        "created_at",
        "updated_at",
    ],
    "transactions": [
        "id",
        "statement_id",
        "account_id",
        "merchant_id",
        "tx_date",
        "description",
        "amount",
        "category",
        "category_id",
        "needs_review",
        "category_source",
        "category_confidence",
        "category_rule_id",
        "category_metadata",
        "categorized_at",
        "reviewed_at",
        "ignored",
        "transaction_kind",
        "fingerprint",
        "created_at",
    ],
    "category_rules": [
        "id",
        "account_id",
        "merchant_id",
        "keyword",
        "category",
        "category_id",
        "amount_min",
        "amount_max",
        "direction",
        "keyword_scope_key",
        "account_id_key",
        "amount_min_key",
        "amount_max_key",
        "source",
        "ai_approved",
        "created_at",
    ],
    "recurring_patterns": [
        "pattern_key",
        "merchant_id",
        "merchant",
        "type",
        "user_status",
        "frequency",
        "expected_day",
        "typical_amount",
        "date_tolerance_days",
        "amount_tolerance",
        "active",
        "created_at",
        "updated_at",
    ],
    "tags": ["id", "name", "description", "instruction", "color", "created_at"],
    "transaction_tags": ["transaction_id", "tag_id", "source", "rule_id", "assigned_at"],
    "category_rule_tags": ["rule_id", "tag_id"],
}

EXPECTED_EXPLICIT_INDEXES = {
    "idx_audit_log_created_at",
    "idx_audit_log_user",
    "idx_category_rule_tags_tag",
    "idx_category_rules_amount_bounds",
    "idx_category_rules_account",
    "idx_category_rules_category_id",
    "idx_category_rules_direction",
    "idx_category_rules_keyword",
    "idx_category_rules_merchant",
    "idx_category_rules_source_approval",
    "idx_merchants_key",
    "idx_recurring_patterns_status",
    "idx_statement_types_active",
    "idx_statements_account",
    "idx_statements_statement_type",
    "idx_statements_uploaded_at",
    "idx_transaction_tags_tag",
    "idx_users_locked_until",
    "idx_users_role_active",
    "idx_transactions_account",
    "idx_transactions_amount",
    "idx_transactions_category",
    "idx_transactions_category_id",
    "idx_transactions_category_source",
    "idx_transactions_dashboard_category_amount",
    "idx_transactions_dashboard_date_amount",
    "idx_transactions_date",
    "idx_transactions_date_category",
    "idx_transactions_description",
    "idx_transactions_ignored",
    "idx_transactions_ignored_account",
    "idx_transactions_ignored_category_id",
    "idx_transactions_ignored_date",
    "idx_transactions_ignored_merchant",
    "idx_transactions_kind",
    "idx_transactions_merchant",
    "idx_transactions_needs_review",
    "idx_transactions_reviewed_at",
    "idx_transactions_statement",
}

EXPECTED_UNIQUE_CONSTRAINTS = {
    "users": {
        "uq_users_username": ["username"],
        "uq_users_username_key": ["username_key"],
        "uq_users_single_owner": ["owner_role_key"],
    },
    "categories": {
        "uq_categories_builtin_key": ["builtin_key"],
    },
    "category_rules": {
        "uq_category_rules_keyword_amount": [
            "keyword_scope_key",
            "account_id_key",
            "direction",
            "amount_min_key",
            "amount_max_key",
        ],
        "uq_category_rules_merchant_amount": [
            "merchant_id",
            "account_id_key",
            "direction",
            "amount_min_key",
            "amount_max_key",
        ],
    },
    "recurring_patterns": {
        "uq_recurring_patterns_merchant_type": ["merchant_id", "type"],
    },
}


def build_core_schema_engine():
    """Create the SQLAlchemy Core metadata schema in an in-memory SQLite database."""
    engine = create_engine("sqlite:///:memory:")
    metadata.create_all(engine)
    return engine


def test_core_metadata_creates_current_tables_and_columns():
    """Verify Core metadata creates the expected table and column surface."""
    core_engine = build_core_schema_engine()
    try:
        inspector = inspect(core_engine)
        core_tables = set(inspector.get_table_names())

        assert core_tables == set(EXPECTED_TABLE_COLUMNS)
        for table_name, expected_columns in EXPECTED_TABLE_COLUMNS.items():
            assert [column["name"] for column in inspector.get_columns(table_name)] == expected_columns
    finally:
        core_engine.dispose()


def test_core_metadata_defines_current_explicit_indexes():
    """Verify Core metadata defines the current explicit index surface."""
    core_indexes = {index.name for table in metadata.tables.values() for index in table.indexes}

    assert core_indexes == EXPECTED_EXPLICIT_INDEXES


def test_core_metadata_creates_portable_unique_constraints():
    """Verify logical uniqueness is modeled with portable constraints."""
    core_engine = build_core_schema_engine()
    try:
        inspector = inspect(core_engine)

        for table_name, expected_constraints in EXPECTED_UNIQUE_CONSTRAINTS.items():
            constraints = {
                constraint["name"]: constraint["column_names"]
                for constraint in inspector.get_unique_constraints(table_name)
            }
            for constraint_name, column_names in expected_constraints.items():
                assert constraints[constraint_name] == column_names
    finally:
        core_engine.dispose()


def test_core_metadata_uses_fixed_scale_numeric_for_money_columns():
    """Verify monetary Core columns use fixed-scale numeric types."""
    money_columns = [
        metadata.tables["transactions"].c.amount,
        metadata.tables["category_rules"].c.amount_min,
        metadata.tables["category_rules"].c.amount_max,
        metadata.tables["category_rules"].c.amount_min_key,
        metadata.tables["category_rules"].c.amount_max_key,
        metadata.tables["recurring_patterns"].c.typical_amount,
        metadata.tables["recurring_patterns"].c.amount_tolerance,
    ]

    for column in money_columns:
        assert isinstance(column.type, Numeric)
        assert column.type.precision == 14
        assert column.type.scale == 2

    assert isinstance(metadata.tables["transactions"].c.category_confidence.type, Float)


def test_core_metadata_uses_typed_date_and_timestamp_columns():
    """Verify date and timestamp metadata uses SQLAlchemy type decorators."""
    date_columns = [
        metadata.tables["transactions"].c.tx_date,
    ]
    timestamp_columns = [
        metadata.tables["statement_types"].c.created_at,
        metadata.tables["categories"].c.created_at,
        metadata.tables["merchants"].c.created_at,
        metadata.tables["merchants"].c.updated_at,
        metadata.tables["category_rules"].c.created_at,
        metadata.tables["statements"].c.import_started_at,
        metadata.tables["statements"].c.import_finished_at,
        metadata.tables["statements"].c.uploaded_at,
        metadata.tables["transactions"].c.categorized_at,
        metadata.tables["transactions"].c.reviewed_at,
        metadata.tables["transactions"].c.created_at,
        metadata.tables["recurring_patterns"].c.created_at,
        metadata.tables["recurring_patterns"].c.updated_at,
        metadata.tables["tags"].c.created_at,
        metadata.tables["transaction_tags"].c.assigned_at,
        metadata.tables["users"].c.created_at,
        metadata.tables["users"].c.updated_at,
        metadata.tables["users"].c.last_login_at,
        metadata.tables["users"].c.locked_until,
        metadata.tables["user_settings"].c.updated_at,
        metadata.tables["audit_log"].c.created_at,
    ]

    assert all(isinstance(column.type, ISODate) for column in date_columns)
    assert all(isinstance(column.type, UTCDateTime) for column in timestamp_columns)


def test_core_metadata_compiles_portable_uniqueness_for_mysql_and_postgresql():
    """Verify non-SQLite schema DDL keeps portable constraints and types."""
    for database_url in (
        "mysql+pymysql://user:password@localhost/finscope",
        "postgresql+psycopg://user:password@localhost/finscope",
    ):
        rendered = render_mock_schema(database_url)
        normalized = rendered.replace("`", "").replace('"', "")

        assert "CREATE TABLE accounts" in normalized
        assert "CREATE TABLE settings" not in normalized
        assert "CREATE TABLE user_settings" in normalized
        assert "PRIMARY KEY (user_id, key)" in normalized
        assert "FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE" in normalized
        assert "tx_date DATE NOT NULL" in normalized
        assert "NUMERIC(14, 2)" in normalized
        if database_url.startswith("mysql"):
            assert "password_hash VARCHAR(255)" in normalized
            assert "ENGINE=InnoDB" in normalized
            assert "CHARSET=utf8mb4" in normalized
            assert "COLLATE utf8mb4_unicode_ci" in normalized
        else:
            assert "password_hash TEXT" in normalized
        assert "uploaded_at" in normalized
        assert ("uploaded_at DATETIME" in normalized) or ("uploaded_at TIMESTAMP" in normalized)
        assert "FOREIGN KEY(category_id) REFERENCES categories (id) ON DELETE SET NULL" in normalized
        assert "uq_recurring_patterns_merchant_type" in normalized
        assert "uq_category_rules_keyword_amount" in normalized
        assert "uq_category_rules_merchant_amount" in normalized
        assert "keyword_scope_key" in normalized
        assert "amount_min_key" in normalized
        assert "amount_max_key" in normalized
        assert "idx_recurring_patterns_merchant_type" not in normalized
        assert "idx_category_rules_keyword_amount_unique" not in normalized
        assert "idx_category_rules_merchant_amount_unique" not in normalized


def render_mock_schema(database_url):
    """Render metadata DDL through a SQLAlchemy mock engine."""
    statements = []

    def collect(sql, *multiparams, **params):
        """Collect compiled DDL emitted by the mock engine."""
        statements.append(str(sql.compile(dialect=engine.dialect)))

    engine = create_mock_engine(database_url, collect)

    metadata.create_all(engine)
    return "\n".join(statements)
