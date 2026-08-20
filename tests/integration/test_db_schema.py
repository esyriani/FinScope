"""Tests for Core database schema behavior."""

import pytest
from sqlalchemy import CheckConstraint, ForeignKeyConstraint, create_engine, insert, inspect, select, text
from sqlalchemy.dialects import mysql
from sqlalchemy.exc import IntegrityError

from finance_app.core.constants import (
    ACCOUNT_TYPES,
    CATEGORY_RULE_DIRECTIONS,
    CATEGORY_RULE_SOURCES,
    CATEGORY_SOURCES,
    DATE_ORDERS,
    INTERAC_DIRECTIONS,
    RECURRING_PATTERN_TYPES,
    RECURRING_USER_STATUSES,
    STATEMENT_IMPORT_MODES,
    STATEMENT_IMPORT_STATUS_COMPLETED,
    STATEMENT_IMPORT_STATUS_FAILED,
    STATEMENT_IMPORT_STATUS_QUEUED,
    STATEMENT_IMPORT_STATUS_RUNNING,
    STATEMENT_IMPORT_STATUSES,
    STATEMENT_TYPE_PARSER_TYPES,
    TRANSACTION_KINDS,
    TRANSACTION_TAG_SOURCES,
    USER_ROLE_EDITOR,
    USER_ROLE_OWNER,
    USER_ROLES,
)
from finance_app.database import connection as connection_module
from finance_app.database.runtime_repair import INTERRUPTED_STATEMENT_IMPORT_ERROR
from finance_app.database.seeds import seed_category_taxonomy_defaults
from finance_app.database.tables import (
    accounts as accounts_table,
)
from finance_app.database.tables import (
    allowed_values_check_sql,
    metadata,
)
from finance_app.database.tables import (
    categories as categories_table,
)
from finance_app.database.tables import (
    category_rules as category_rules_table,
)
from finance_app.database.tables import (
    merchants as merchants_table,
)
from finance_app.database.tables import (
    pinned_reports as pinned_reports_table,
)
from finance_app.database.tables import (
    recurring_patterns as recurring_patterns_table,
)
from finance_app.database.tables import (
    reimbursement_allocations as reimbursement_allocations_table,
)
from finance_app.database.tables import (
    reimbursement_expense_completions as reimbursement_expense_completions_table,
)
from finance_app.database.tables import (
    statement_types as statement_types_table,
)
from finance_app.database.tables import (
    statements as statements_table,
)
from finance_app.database.tables import (
    tags as tags_table,
)
from finance_app.database.tables import (
    transactions as transactions_table,
)
from finance_app.database.tables import (
    users as users_table,
)
from finance_app.modules.categories.service import rename_category
from finance_app.modules.settings.runtime import get_unknown_category


@pytest.fixture
def schema_conn():
    """Create a seeded Core schema in an in-memory SQLite database."""
    engine = create_engine("sqlite://")
    metadata.create_all(engine)
    conn = engine.connect()
    seed_category_taxonomy_defaults(conn)
    conn.commit()
    try:
        yield conn
    finally:
        conn.close()
        engine.dispose()


def column_names(conn, table_name):
    """Return reflected column names for a created table."""
    return {column["name"] for column in inspect(conn).get_columns(table_name)}


def foreign_key_triplets(conn, table_name):
    """Return reflected constrained, referred-table, referred-column triples."""
    return {
        (local_column, foreign_key["referred_table"], remote_column)
        for foreign_key in inspect(conn).get_foreign_keys(table_name)
        for local_column, remote_column in zip(
            foreign_key["constrained_columns"],
            foreign_key["referred_columns"],
        )
    }


def category_id(conn, name):
    """Return the category ID for a seeded category."""
    found = conn.execute(select(categories_table.c.id).where(categories_table.c.name == name)).scalar_one_or_none()
    assert found is not None
    return found


def create_statements_table_without_date_order(conn):
    """Create a statements table missing the current date_order column."""
    conn.execute(text("""
            CREATE TABLE statements (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id INTEGER,
                statement_type_id INTEGER NOT NULL,
                filename VARCHAR(512) NOT NULL,
                checksum VARCHAR(128) NOT NULL UNIQUE,
                extension VARCHAR(32) NOT NULL DEFAULT '',
                interac_direction VARCHAR(32) NOT NULL DEFAULT 'auto',
                raw_text TEXT,
                import_status VARCHAR(32) NOT NULL DEFAULT 'pending',
                import_error TEXT,
                import_started_at DATETIME,
                import_finished_at DATETIME,
                imported_count INTEGER NOT NULL DEFAULT 0,
                skipped_count INTEGER NOT NULL DEFAULT 0,
                ignored_count INTEGER NOT NULL DEFAULT 0,
                llm_candidate_count INTEGER NOT NULL DEFAULT 0,
                uploaded_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """))


def create_current_schema_engine():
    """Create an in-memory database with the current Core schema."""
    engine = create_engine("sqlite://")
    metadata.create_all(engine)
    return engine


class ReflectedConstraintInspector:
    """Provide selected reflected schema details for validation-helper tests."""

    def __init__(self, *, checks=(), foreign_keys=()):
        self.checks = list(checks)
        self.foreign_keys = list(foreign_keys)

    def get_check_constraints(self, table_name):
        """Return configured reflected check constraints."""
        del table_name
        return self.checks

    def get_foreign_keys(self, table_name):
        """Return configured reflected foreign keys."""
        del table_name
        return self.foreign_keys


def replace_table(engine, table_name, create_sql, *, indexes=()):
    """Replace one current table with caller-supplied DDL for validation tests."""
    with engine.connect() as conn:
        conn.execute(text("PRAGMA foreign_keys=OFF"))
        conn.execute(text(f"DROP TABLE {table_name}"))
        conn.execute(text(create_sql))
        for index_sql in indexes:
            conn.execute(text(index_sql))
        conn.execute(text("PRAGMA foreign_keys=ON"))
        conn.commit()


def assert_table_uses_allowed_values(table_name, column_name, values):
    """Verify a constrained table column derives its CHECK values from constants."""
    constraints = {
        str(constraint.sqltext)
        for constraint in metadata.tables[table_name].constraints
        if isinstance(constraint, CheckConstraint)
    }
    assert allowed_values_check_sql(column_name, values) in constraints


def reflected_mysql_check_constraints(table):
    """Return MySQL-style reflected check rows from Core metadata."""
    dialect = mysql.dialect()
    constraints = []
    for constraint in table.constraints:
        if not isinstance(constraint, CheckConstraint):
            continue
        sqltext = connection_module.compile_sql(constraint.sqltext, dialect)
        sqltext = sqltext.replace(" != ", " <> ").replace(", ", ",")
        constraints.append(
            {
                "name": dialect.identifier_preparer.format_constraint(constraint),
                "sqltext": f"({sqltext})",
            }
        )
    return constraints


def reflected_mysql_foreign_keys(table):
    """Return MySQL-style reflected foreign-key rows from Core metadata."""
    dialect = mysql.dialect()
    foreign_keys = []
    for constraint in table.constraints:
        if not isinstance(constraint, ForeignKeyConstraint):
            continue
        foreign_keys.append(
            {
                "name": dialect.identifier_preparer.format_constraint(constraint),
                "constrained_columns": [element.parent.name for element in constraint.elements],
                "referred_table": constraint.elements[0].column.table.name,
                "referred_columns": [element.column.name for element in constraint.elements],
                "options": {"ondelete": constraint.elements[0].ondelete},
            }
        )
    return foreign_keys


def test_users_enforce_case_insensitive_username_uniqueness(schema_conn):
    """Verify the database rejects usernames that normalize to the same key."""
    schema_conn.execute(
        insert(users_table).values(
            username="Owner",
            display_name="Owner",
            password_hash="hash",
            role=USER_ROLE_OWNER,
        )
    )
    schema_conn.commit()

    with pytest.raises(IntegrityError):
        schema_conn.execute(
            insert(users_table).values(
                username=" owner ",
                display_name="Case variant",
                password_hash="hash",
                role=USER_ROLE_EDITOR,
            )
        )
    schema_conn.rollback()


def test_users_enforce_single_owner_role(schema_conn):
    """Verify the database rejects multiple owner-role accounts."""
    schema_conn.execute(
        insert(users_table).values(
            username="owner-one",
            display_name="Owner One",
            password_hash="hash",
            role=USER_ROLE_OWNER,
        )
    )
    schema_conn.commit()

    with pytest.raises(IntegrityError):
        schema_conn.execute(
            insert(users_table).values(
                username="owner-two",
                display_name="Owner Two",
                password_hash="hash",
                role=USER_ROLE_OWNER,
            )
        )
    schema_conn.rollback()

    schema_conn.execute(
        insert(users_table).values(
            username="editor",
            display_name="Editor",
            password_hash="hash",
            role=USER_ROLE_EDITOR,
        )
    )


def test_visible_names_enforce_normalized_uniqueness(schema_conn):
    """Verify user-visible names are unique after lower-case trimming."""
    cases = (
        (accounts_table, {"name": "Schema Visa"}, {"name": " schema visa "}),
        (
            statement_types_table,
            {"name": "Schema card import"},
            {"name": " schema CARD import "},
        ),
        (categories_table, {"name": "Schema Food"}, {"name": " schema food "}),
        (tags_table, {"name": "Schema Audit"}, {"name": " schema audit "}),
    )

    for table, first_values, duplicate_values in cases:
        schema_conn.execute(insert(table).values(**first_values))
        schema_conn.commit()

        with pytest.raises(IntegrityError):
            schema_conn.execute(insert(table).values(**duplicate_values))
        schema_conn.rollback()


def test_init_core_db_rejects_statements_table_without_date_order():
    """Verify startup refuses a schema missing required statement columns."""
    engine = create_engine("sqlite://")
    try:
        with engine.begin() as conn:
            create_statements_table_without_date_order(conn)

        with pytest.raises(RuntimeError, match="statements.date_order"):
            connection_module.init_core_db(engine)
    finally:
        engine.dispose()


def test_init_core_db_marks_interrupted_statement_imports_failed():
    """Verify startup makes orphaned in-memory statement imports retryable."""
    engine = create_engine("sqlite://")
    try:
        connection_module.init_core_db(engine)
        with engine.begin() as conn:
            statement_type_id = conn.execute(
                select(statement_types_table.c.id).order_by(statement_types_table.c.id).limit(1)
            ).scalar_one()
            conn.execute(
                insert(statements_table),
                [
                    {
                        "statement_type_id": statement_type_id,
                        "filename": "restart-queued.csv",
                        "checksum": "restart-queued",
                        "raw_text": "",
                        "import_status": STATEMENT_IMPORT_STATUS_QUEUED,
                        "import_token": "queued-token",
                        "import_started_at": None,
                        "import_finished_at": None,
                    },
                    {
                        "statement_type_id": statement_type_id,
                        "filename": "restart-running.csv",
                        "checksum": "restart-running",
                        "raw_text": "",
                        "import_status": STATEMENT_IMPORT_STATUS_RUNNING,
                        "import_token": "running-token",
                        "import_started_at": "2026-05-01T12:00:00Z",
                        "import_finished_at": None,
                    },
                    {
                        "statement_type_id": statement_type_id,
                        "filename": "restart-completed.csv",
                        "checksum": "restart-completed",
                        "raw_text": "",
                        "import_status": STATEMENT_IMPORT_STATUS_COMPLETED,
                        "import_token": None,
                        "import_started_at": "2026-05-01T12:30:00Z",
                        "import_finished_at": "2026-05-01T13:00:00Z",
                    },
                ],
            )

        connection_module.init_core_db(engine)
        with engine.connect() as conn:
            rows = (
                conn.execute(
                    select(
                        statements_table.c.filename,
                        statements_table.c.import_status,
                        statements_table.c.import_error,
                        statements_table.c.import_finished_at,
                    ).order_by(statements_table.c.filename)
                )
                .mappings()
                .all()
            )
    finally:
        engine.dispose()

    completed, queued, running = rows
    assert completed["filename"] == "restart-completed.csv"
    assert completed["import_status"] == STATEMENT_IMPORT_STATUS_COMPLETED
    assert completed["import_error"] is None
    assert queued["filename"] == "restart-queued.csv"
    assert queued["import_status"] == STATEMENT_IMPORT_STATUS_FAILED
    assert queued["import_error"] == INTERRUPTED_STATEMENT_IMPORT_ERROR
    assert queued["import_finished_at"] is not None
    assert running["filename"] == "restart-running.csv"
    assert running["import_status"] == STATEMENT_IMPORT_STATUS_FAILED
    assert running["import_error"] == INTERRUPTED_STATEMENT_IMPORT_ERROR
    assert running["import_finished_at"] is not None


def test_schema_validation_accepts_mysql_reflected_check_sql_and_truncated_names():
    """Verify MySQL/MariaDB reflection spelling does not create false schema errors."""
    issues = {}
    dialect = mysql.dialect()
    inspector = ReflectedConstraintInspector(
        checks=reflected_mysql_check_constraints(recurring_patterns_table),
    )

    connection_module.validate_check_constraints(
        issues,
        inspector,
        dialect,
        recurring_patterns_table.name,
        recurring_patterns_table,
    )

    assert issues == {}


def test_schema_validation_accepts_mysql_reflected_foreign_key_truncation():
    """Verify MySQL-reflected truncated foreign-key names match Core metadata."""
    issues = {}
    dialect = mysql.dialect()
    inspector = ReflectedConstraintInspector(
        foreign_keys=reflected_mysql_foreign_keys(reimbursement_allocations_table),
    )

    connection_module.validate_foreign_keys(
        issues,
        inspector,
        dialect,
        reimbursement_allocations_table.name,
        reimbursement_allocations_table,
    )

    assert issues == {}


def test_schema_validation_normalizes_mysql_generated_columns_and_defaults():
    """Verify generated SQL and timestamp defaults compare across MySQL reflection."""
    assert connection_module.sql_fragments_match("lower(trim(name))", "(lcase(trim(`name`)))")
    assert connection_module.sql_fragments_match(
        "short_title IS NULL OR length(trim(short_title)) <= 30",
        "`short_title` is null or octet_length(trim(`short_title`)) <= 30",
    )
    assert connection_module.normalize_default(text("CURRENT_TIMESTAMP")) == connection_module.normalize_default(
        "current_timestamp()"
    )


def test_init_core_db_rejects_column_compatible_schema_missing_unique_constraints():
    """Verify startup rejects an existing schema missing deduplication uniqueness."""
    engine = create_current_schema_engine()
    try:
        replace_table(
            engine,
            "transactions",
            """
            CREATE TABLE transactions (
                id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
                statement_id INTEGER,
                account_id INTEGER,
                merchant_id INTEGER,
                tx_date VARCHAR(10) NOT NULL,
                description VARCHAR(512) NOT NULL,
                amount NUMERIC(14, 2) NOT NULL,
                category VARCHAR(255),
                category_id INTEGER,
                needs_review INTEGER NOT NULL DEFAULT 0,
                category_source VARCHAR(32) NOT NULL DEFAULT 'unknown',
                category_confidence FLOAT,
                category_rule_id INTEGER,
                category_metadata TEXT,
                categorized_at VARCHAR(32),
                reviewed_at VARCHAR(32),
                ignored INTEGER NOT NULL DEFAULT 0,
                transaction_kind VARCHAR(32) NOT NULL DEFAULT 'expense',
                fingerprint VARCHAR(255) NOT NULL,
                created_at VARCHAR(32) NOT NULL DEFAULT CURRENT_TIMESTAMP,
                CONSTRAINT fk_transactions_statement_id_statements
                    FOREIGN KEY(statement_id) REFERENCES statements (id),
                CONSTRAINT fk_transactions_account_id_accounts
                    FOREIGN KEY(account_id) REFERENCES accounts (id),
                CONSTRAINT fk_transactions_merchant_id_merchants
                    FOREIGN KEY(merchant_id) REFERENCES merchants (id) ON DELETE SET NULL,
                CONSTRAINT fk_transactions_category_id_categories
                    FOREIGN KEY(category_id) REFERENCES categories (id) ON DELETE SET NULL,
                CONSTRAINT fk_transactions_category_rule_id_category_rules
                    FOREIGN KEY(category_rule_id) REFERENCES category_rules (id) ON DELETE SET NULL,
                CONSTRAINT ck_transactions_transactions_description_non_empty
                    CHECK (trim(description) != ''),
                CONSTRAINT ck_transactions_transactions_needs_review_bool
                    CHECK (needs_review IN (0, 1)),
                CONSTRAINT ck_transactions_transactions_ignored_bool
                    CHECK (ignored IN (0, 1)),
                CONSTRAINT ck_transactions_transactions_transaction_kind_allowed
                    CHECK (transaction_kind IN ('expense', 'income', 'payment', 'refund', 'transfer')),
                CONSTRAINT ck_transactions_transactions_category_source_allowed
                    CHECK (category_source IN ('ai', 'history', 'manual', 'rule', 'unknown')),
                CONSTRAINT ck_transactions_transactions_category_confidence_probability
                    CHECK (
                        category_confidence IS NULL
                        OR (category_confidence >= 0 AND category_confidence <= 1)
                    )
            )
            """,
        )

        with pytest.raises(RuntimeError, match="transactions.uq_transactions_fingerprint"):
            connection_module.init_core_db(engine)
    finally:
        engine.dispose()


def test_init_core_db_rejects_column_compatible_schema_missing_check_constraints():
    """Verify startup rejects an existing schema missing allocation guards."""
    engine = create_current_schema_engine()
    try:
        replace_table(
            engine,
            "reimbursement_allocations",
            """
            CREATE TABLE reimbursement_allocations (
                id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
                reimbursement_transaction_id INTEGER NOT NULL,
                expense_transaction_id INTEGER NOT NULL,
                amount NUMERIC(14, 2) NOT NULL,
                created_at VARCHAR(32) NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at VARCHAR(32) NOT NULL DEFAULT CURRENT_TIMESTAMP,
                CONSTRAINT uq_reimbursement_allocations_pair
                    UNIQUE (reimbursement_transaction_id, expense_transaction_id),
                CONSTRAINT fk_reimbursement_allocations_reimbursement_transaction_id_transactions
                    FOREIGN KEY(reimbursement_transaction_id) REFERENCES transactions (id) ON DELETE CASCADE,
                CONSTRAINT fk_reimbursement_allocations_expense_transaction_id_transactions
                    FOREIGN KEY(expense_transaction_id) REFERENCES transactions (id) ON DELETE CASCADE
            )
            """,
            indexes=(
                """
                CREATE INDEX idx_reimbursement_allocations_reimbursement
                ON reimbursement_allocations (reimbursement_transaction_id)
                """,
                """
                CREATE INDEX idx_reimbursement_allocations_expense
                ON reimbursement_allocations (expense_transaction_id)
                """,
            ),
        )

        with pytest.raises(RuntimeError, match="reimbursement_allocations_amount_positive"):
            connection_module.init_core_db(engine)
    finally:
        engine.dispose()


def test_init_core_db_rejects_foreign_keys_with_wrong_delete_behavior():
    """Verify startup rejects an existing schema missing required FK cascades."""
    engine = create_current_schema_engine()
    try:
        replace_table(
            engine,
            "reimbursement_expense_completions",
            """
            CREATE TABLE reimbursement_expense_completions (
                id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
                expense_transaction_id INTEGER NOT NULL,
                created_at VARCHAR(32) NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at VARCHAR(32) NOT NULL DEFAULT CURRENT_TIMESTAMP,
                CONSTRAINT uq_reimbursement_expense_completions_expense UNIQUE (expense_transaction_id),
                CONSTRAINT fk_reimbursement_expense_completions_expense_transaction_id_transactions FOREIGN KEY(expense_transaction_id) REFERENCES transactions (id)
            )
            """,
            indexes=(
                """
                CREATE INDEX idx_reimbursement_expense_completions_expense
                ON reimbursement_expense_completions (expense_transaction_id)
                """,
            ),
        )

        with pytest.raises(RuntimeError, match="foreign key mismatches"):
            connection_module.init_core_db(engine)
    finally:
        engine.dispose()


def test_init_core_db_rejects_missing_generated_columns():
    """Verify startup rejects ordinary columns where generated keys are required."""
    engine = create_current_schema_engine()
    try:
        replace_table(
            engine,
            "users",
            """
            CREATE TABLE users (
                id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
                username VARCHAR(150) NOT NULL,
                username_key VARCHAR(150),
                display_name VARCHAR(150) NOT NULL,
                password_hash TEXT NOT NULL,
                role VARCHAR(32) NOT NULL,
                is_active INTEGER NOT NULL DEFAULT 1,
                owner_role_key INTEGER GENERATED ALWAYS AS (
                    CASE WHEN role = 'owner' THEN 1 ELSE NULL END
                ) STORED,
                must_change_password INTEGER NOT NULL DEFAULT 0,
                created_at VARCHAR(32) NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at VARCHAR(32) NOT NULL DEFAULT CURRENT_TIMESTAMP,
                last_login_at VARCHAR(32),
                failed_login_count INTEGER NOT NULL DEFAULT 0,
                locked_until VARCHAR(32),
                CONSTRAINT uq_users_username UNIQUE (username),
                CONSTRAINT uq_users_username_key UNIQUE (username_key),
                CONSTRAINT uq_users_single_owner UNIQUE (owner_role_key),
                CONSTRAINT ck_users_users_username_non_empty CHECK (trim(username) != ''),
                CONSTRAINT ck_users_users_display_name_non_empty CHECK (trim(display_name) != ''),
                CONSTRAINT ck_users_users_role_allowed CHECK (role IN ('editor', 'owner', 'viewer')),
                CONSTRAINT ck_users_users_is_active_bool CHECK (is_active IN (0, 1)),
                CONSTRAINT ck_users_users_must_change_password_bool CHECK (must_change_password IN (0, 1)),
                CONSTRAINT ck_users_users_failed_login_count_non_negative CHECK (failed_login_count >= 0)
            )
            """,
            indexes=(
                "CREATE INDEX idx_users_role_active ON users (role, is_active)",
                "CREATE INDEX idx_users_locked_until ON users (locked_until)",
            ),
        )

        with pytest.raises(RuntimeError, match="users.username_key generated expression"):
            connection_module.init_core_db(engine)
    finally:
        engine.dispose()


def test_init_core_db_rejects_missing_explicit_indexes():
    """Verify startup rejects an existing schema missing current query indexes."""
    engine = create_current_schema_engine()
    try:
        with engine.begin() as conn:
            conn.execute(text("DROP INDEX idx_transactions_date"))

        with pytest.raises(RuntimeError, match="transactions.idx_transactions_date"):
            connection_module.init_core_db(engine)
    finally:
        engine.dispose()


def test_core_schema_creates_category_tag_tables(schema_conn):
    """Verify Core metadata creates taxonomy and merchant identity tables."""
    tables = set(inspect(schema_conn).get_table_names())

    assert {"tags", "transaction_tags", "category_rule_tags"}.issubset(tables)
    assert "builtin_key" in column_names(schema_conn, "tags")
    assert "merchants" in tables
    assert "merchant_aliases" not in tables


def test_core_schema_creates_user_owned_pinned_reports(schema_conn):
    """Verify pinned report views are owned rows with bounded display metadata."""
    tables = set(inspect(schema_conn).get_table_names())
    columns = column_names(schema_conn, "pinned_reports")
    foreign_keys = foreign_key_triplets(schema_conn, "pinned_reports")

    assert "pinned_reports" in tables
    assert {
        "user_id",
        "report_type",
        "target_kind",
        "target_category_id",
        "target_tag_id",
        "target_account_id",
        "target_merchant_id",
        "period",
        "date_from",
        "date_to",
        "measure",
        "basis",
        "account_filter_id",
        "merchant_filter_id",
        "merchant_query",
        "classification_scope",
        "category_filters",
        "tag_filters",
        "fingerprint",
        "sort_order",
        "short_title",
        "created_at",
    }.issubset(columns)
    assert ("user_id", "users", "id") in foreign_keys
    assert ("target_category_id", "categories", "id") in foreign_keys
    assert ("target_tag_id", "tags", "id") in foreign_keys
    assert ("target_account_id", "accounts", "id") in foreign_keys
    assert ("target_merchant_id", "merchants", "id") in foreign_keys

    schema_conn.execute(
        insert(users_table).values(
            username="PinOwner",
            display_name="Pin owner",
            password_hash="hash",
            role=USER_ROLE_OWNER,
        )
    )
    user_id = schema_conn.execute(select(users_table.c.id).where(users_table.c.username == "PinOwner")).scalar_one()
    schema_conn.execute(
        insert(pinned_reports_table).values(
            user_id=user_id,
            report_type="overview",
            period="year_to_date",
            measure="spending",
            basis="cash_flow",
            classification_scope="categorized",
            fingerprint="schema-pin",
            sort_order=0,
        )
    )
    schema_conn.commit()

    with pytest.raises(IntegrityError):
        schema_conn.execute(
            insert(pinned_reports_table).values(
                user_id=user_id,
                report_type="overview",
                period="year_to_date",
                measure="spending",
                basis="cash_flow",
                classification_scope="categorized",
                fingerprint="schema-pin",
                sort_order=1,
            )
        )
    schema_conn.rollback()

    with pytest.raises(IntegrityError):
        schema_conn.execute(
            insert(pinned_reports_table).values(
                user_id=user_id,
                report_type="overview",
                period="year_to_date",
                measure="spending",
                basis="cash_flow",
                classification_scope="categorized",
                fingerprint="schema-pin-title",
                sort_order=0,
                short_title="x" * 31,
            )
        )
    schema_conn.rollback()


def test_core_schema_text_constraints_match_shared_constants(schema_conn):
    """Verify persisted enum-like values are constrained from shared constants."""
    expectations = (
        ("accounts", "account_type", ACCOUNT_TYPES),
        ("statement_types", "parser_type", STATEMENT_TYPE_PARSER_TYPES),
        ("statement_types", "import_mode", STATEMENT_IMPORT_MODES),
        ("statement_types", "default_account_type", ACCOUNT_TYPES),
        ("statements", "import_status", STATEMENT_IMPORT_STATUSES),
        ("statements", "interac_direction", INTERAC_DIRECTIONS),
        ("statements", "date_order", DATE_ORDERS),
        ("transactions", "transaction_kind", TRANSACTION_KINDS),
        ("transactions", "category_source", CATEGORY_SOURCES),
        ("category_rules", "source", CATEGORY_RULE_SOURCES),
        ("category_rules", "direction", CATEGORY_RULE_DIRECTIONS),
        ("recurring_patterns", "type", RECURRING_PATTERN_TYPES),
        ("recurring_patterns", "user_status", RECURRING_USER_STATUSES),
        ("transaction_tags", "source", TRANSACTION_TAG_SOURCES),
        ("users", "role", USER_ROLES),
    )

    for table_name, column_name, values in expectations:
        assert_table_uses_allowed_values(table_name, column_name, values)


def test_core_schema_tracks_statement_import_state(schema_conn):
    """Verify statement rows include persistent import retry metadata."""
    statement_columns = column_names(schema_conn, "statements")

    assert {
        "extension",
        "import_status",
        "import_error",
        "import_started_at",
        "import_finished_at",
        "interac_direction",
        "date_order",
        "import_token",
        "imported_count",
        "skipped_count",
        "ignored_count",
        "llm_candidate_count",
    }.issubset(statement_columns)


def test_core_schema_tracks_account_roles_and_transaction_kinds(schema_conn):
    """Verify schema supports account roles, statement behavior, and transaction kinds."""
    account_columns = column_names(schema_conn, "accounts")
    statement_type_columns = column_names(schema_conn, "statement_types")
    transaction_columns = column_names(schema_conn, "transactions")

    assert {"account_type", "paid_from_account_id"}.issubset(account_columns)
    assert {"import_mode", "default_account_type"}.issubset(statement_type_columns)
    assert "transaction_kind" in transaction_columns

    schema_conn.execute(insert(accounts_table).values(name="Visa", account_type="credit_card"))
    schema_conn.execute(
        insert(statement_types_table).values(
            name="Card import",
            parser_type="credit_card",
            import_mode="ledger",
            default_account_type="credit_card",
        )
    )
    schema_conn.execute(
        insert(transactions_table).values(
            tx_date="2026-01-01",
            description="CARD PAYMENT",
            amount=100,
            transaction_kind="payment",
            fingerprint="tx-payment-kind",
        )
    )


def test_core_schema_tracks_reimbursement_expense_completion_markers(schema_conn):
    """Verify reimbursement completion rows link one marker to one expense."""
    completion_columns = column_names(schema_conn, "reimbursement_expense_completions")
    completion_foreign_keys = foreign_key_triplets(schema_conn, "reimbursement_expense_completions")

    assert {"id", "expense_transaction_id", "created_at", "updated_at"}.issubset(completion_columns)
    assert ("expense_transaction_id", "transactions", "id") in completion_foreign_keys

    expense_id = schema_conn.execute(
        insert(transactions_table).values(
            tx_date="2026-01-01",
            description="REIMBURSABLE EXPENSE",
            amount=100,
            transaction_kind="expense",
            fingerprint="tx-reimbursement-completion",
        )
    ).inserted_primary_key[0]
    schema_conn.execute(
        insert(reimbursement_expense_completions_table).values(
            expense_transaction_id=expense_id,
        )
    )

    with pytest.raises(IntegrityError):
        schema_conn.execute(
            insert(reimbursement_expense_completions_table).values(
                expense_transaction_id=expense_id,
            )
        )


def test_core_schema_accepts_interac_statement_parser_type(schema_conn):
    """Verify the statement type parser constraint accepts Interac history."""
    schema_conn.execute(
        insert(statement_types_table).values(
            name="Interac test",
            parser_type="interac_etransfer",
        )
    )

    found = schema_conn.execute(
        select(statement_types_table.c.parser_type).where(statement_types_table.c.name == "Interac test")
    ).scalar_one()
    assert found == "interac_etransfer"


def test_core_schema_links_transactions_rules_and_recurring_patterns_to_merchants(schema_conn):
    """Verify merchant identity columns and foreign keys exist."""
    transaction_columns = column_names(schema_conn, "transactions")
    rule_columns = column_names(schema_conn, "category_rules")
    recurring_columns = column_names(schema_conn, "recurring_patterns")
    transaction_foreign_keys = foreign_key_triplets(schema_conn, "transactions")
    rule_foreign_keys = foreign_key_triplets(schema_conn, "category_rules")
    recurring_foreign_keys = foreign_key_triplets(schema_conn, "recurring_patterns")

    assert "merchant_id" in transaction_columns
    assert "merchant_id" in rule_columns
    assert "account_id" in rule_columns
    assert "ai_approved" in rule_columns
    assert "merchant_id" in recurring_columns
    assert ("merchant_id", "merchants", "id") in transaction_foreign_keys
    assert ("merchant_id", "merchants", "id") in rule_foreign_keys
    assert ("account_id", "accounts", "id") in rule_foreign_keys
    assert ("merchant_id", "merchants", "id") in recurring_foreign_keys


def test_category_rules_allow_merchant_bound_and_keyword_fuzzy_scopes(schema_conn):
    """Verify category rule uniqueness is scoped by merchant binding."""
    result = schema_conn.execute(
        insert(merchants_table).values(
            merchant_key="METRO",
        )
    )
    merchant_id = result.inserted_primary_key[0]

    schema_conn.execute(
        insert(category_rules_table).values(
            merchant_id=merchant_id,
            keyword="METRO",
            category="Food",
        )
    )
    schema_conn.execute(
        insert(category_rules_table).values(
            keyword="METRO",
            category="Groceries",
        )
    )

    with pytest.raises(IntegrityError):
        schema_conn.execute(
            insert(category_rules_table).values(
                merchant_id=merchant_id,
                keyword="METRO GROCERY",
                category="Dining",
            )
        )


def test_category_rules_enforce_keyword_and_merchant_uniqueness(schema_conn):
    """Verify category rule duplicate prevention uses the same logical keys."""
    first_merchant = schema_conn.execute(
        insert(merchants_table).values(
            merchant_key="FIRST",
        )
    ).inserted_primary_key[0]
    second_merchant = schema_conn.execute(
        insert(merchants_table).values(
            merchant_key="SECOND",
        )
    ).inserted_primary_key[0]
    schema_conn.execute(insert(category_rules_table).values(keyword="MARKET", category="Food"))
    schema_conn.execute(
        insert(category_rules_table).values(
            keyword="MARKET",
            category="Food",
            amount_min=10,
        )
    )
    schema_conn.execute(
        insert(category_rules_table).values(
            merchant_id=first_merchant,
            keyword="FIRST MARKET",
            category="Food",
        )
    )
    schema_conn.execute(
        insert(category_rules_table).values(
            merchant_id=second_merchant,
            keyword="SECOND MARKET",
            category="Food",
        )
    )
    schema_conn.commit()

    with pytest.raises(IntegrityError):
        schema_conn.execute(
            insert(category_rules_table).values(
                keyword="MARKET",
                category="Utilities",
            )
        )
    schema_conn.rollback()

    with pytest.raises(IntegrityError):
        schema_conn.execute(
            insert(category_rules_table).values(
                keyword="MARKET",
                category="Utilities",
                amount_min=10,
            )
        )
    schema_conn.rollback()

    with pytest.raises(IntegrityError):
        schema_conn.execute(
            insert(category_rules_table).values(
                merchant_id=first_merchant,
                keyword="FIRST MARKET UPDATED",
                category="Utilities",
            )
        )


def test_recurring_patterns_enforce_merchant_type_uniqueness(schema_conn):
    """Verify merchant-bound recurring patterns are unique across dialects."""
    merchant_id = schema_conn.execute(
        insert(merchants_table).values(
            merchant_key="RECURRENT",
        )
    ).inserted_primary_key[0]
    schema_conn.execute(
        insert(recurring_patterns_table).values(
            pattern_key="merchant::spending",
            merchant_id=merchant_id,
            merchant="Recurring merchant",
            type="spending",
        )
    )
    schema_conn.execute(
        insert(recurring_patterns_table).values(
            pattern_key="keyword a::spending",
            merchant="Keyword A",
            type="spending",
        )
    )
    schema_conn.execute(
        insert(recurring_patterns_table).values(
            pattern_key="keyword b::spending",
            merchant="Keyword B",
            type="spending",
        )
    )
    schema_conn.commit()

    with pytest.raises(IntegrityError):
        schema_conn.execute(
            insert(recurring_patterns_table).values(
                pattern_key="merchant copy::spending",
                merchant_id=merchant_id,
                merchant="Recurring merchant",
                type="spending",
            )
        )


def test_rename_category_preserves_stable_id_and_refreshes_cache(schema_conn):
    """Verify category rename preserves stable IDs and updates cached labels."""
    schema_conn.execute(insert(categories_table).values(name="Custom income"))
    income_id = category_id(schema_conn, "Custom income")
    schema_conn.execute(
        insert(transactions_table).values(
            tx_date="2026-01-01",
            description="PAYROLL",
            amount=-100,
            category_id=income_id,
            category="Custom income",
            fingerprint="tx-income",
        )
    )
    schema_conn.execute(
        insert(category_rules_table).values(
            keyword="PAYROLL",
            category_id=income_id,
            category="Custom income",
        )
    )

    assert rename_category(schema_conn, "Custom income", "Earnings") == "Earnings"

    category = (
        schema_conn.execute(
            select(categories_table.c.id, categories_table.c.name).where(categories_table.c.id == income_id)
        )
        .mappings()
        .one()
    )
    transaction = (
        schema_conn.execute(
            select(transactions_table.c.category_id, transactions_table.c.category).where(
                transactions_table.c.fingerprint == "tx-income"
            )
        )
        .mappings()
        .one()
    )
    rule = (
        schema_conn.execute(
            select(category_rules_table.c.category_id, category_rules_table.c.category).where(
                category_rules_table.c.keyword == "PAYROLL"
            )
        )
        .mappings()
        .one()
    )
    assert (category["id"], category["name"]) == (income_id, "Earnings")
    assert (transaction["category_id"], transaction["category"]) == (income_id, "Earnings")
    assert (rule["category_id"], rule["category"]) == (income_id, "Earnings")


def test_builtin_categories_are_seeded_and_protected(schema_conn):
    """Verify built-in categories use stable keys and cannot be renamed."""
    income_id = category_id(schema_conn, "Income")
    rental_id = category_id(schema_conn, "Rental")
    unknown_id = category_id(schema_conn, "UNKNOWN")
    reimbursement_id = category_id(schema_conn, "Reimbursement")
    transfers_id = category_id(schema_conn, "Transfers")

    rows = {
        row["name"]: row
        for row in schema_conn.execute(
            select(
                categories_table.c.id,
                categories_table.c.name,
                categories_table.c.builtin_key,
            ).where(categories_table.c.id.in_((income_id, rental_id, unknown_id, reimbursement_id, transfers_id)))
        ).mappings()
    }
    assert rows["Income"]["builtin_key"] == "income"
    assert rows["Rental"]["builtin_key"] == "rental"
    assert rows["UNKNOWN"]["builtin_key"] == "unknown"
    assert rows["Reimbursement"]["builtin_key"] == "reimbursement"
    assert rows["Transfers"]["builtin_key"] == "transfers"
    assert rename_category(schema_conn, "Income", "Earnings") is None
    assert rename_category(schema_conn, "Rental", "Rental property") is None
    assert rename_category(schema_conn, "UNKNOWN", "UNCATEGORIZED") is None
    assert rename_category(schema_conn, "Reimbursement", "Repayments") is None
    assert rename_category(schema_conn, "Transfers", "Balance movement") is None
    assert get_unknown_category(schema_conn) == "UNKNOWN"


def test_builtin_tags_are_seeded_with_stable_keys(schema_conn):
    """Verify built-in tags use stable keys."""
    rows = {
        row["name"]: row["builtin_key"]
        for row in schema_conn.execute(
            select(tags_table.c.name, tags_table.c.builtin_key).where(tags_table.c.builtin_key.is_not(None))
        ).mappings()
    }

    assert rows["Reimbursable"] == "reimbursable"
    assert rows["Tax"] == "tax"
