"""Tests for Core database schema behavior."""

import pytest
from sqlalchemy import CheckConstraint, create_engine, insert, inspect, select, text
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
    STATEMENT_IMPORT_STATUSES,
    STATEMENT_TYPE_PARSER_TYPES,
    TRANSACTION_KINDS,
    TRANSACTION_TAG_SOURCES,
    USER_ROLE_EDITOR,
    USER_ROLE_OWNER,
    USER_ROLES,
)
from finance_app.database import connection as connection_module
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
    recurring_patterns as recurring_patterns_table,
)
from finance_app.database.tables import (
    statement_types as statement_types_table,
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


def create_legacy_statements_table_without_date_order(conn):
    """Create the statements table shape used before date_order was added."""
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


def assert_table_uses_allowed_values(table_name, column_name, values):
    """Verify a constrained table column derives its CHECK values from constants."""
    constraints = {
        str(constraint.sqltext)
        for constraint in metadata.tables[table_name].constraints
        if isinstance(constraint, CheckConstraint)
    }
    assert allowed_values_check_sql(column_name, values) in constraints


def test_core_schema_has_no_retired_tables(schema_conn):
    """Verify Core metadata does not create retired compatibility or review tables."""
    retired_tables = set(inspect(schema_conn).get_table_names()) & {
        "settings",
        "schema_migrations",
        "app_metadata",
        "category_suggestions",
        "category_suggestion_tags",
        "merchant_normalization_cache",
        "merchant_normalization_review_queue",
    }

    assert retired_tables == set()


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


def test_init_core_db_rejects_legacy_statements_table_without_date_order():
    """Verify startup refuses an outdated schema instead of patching it."""
    engine = create_engine("sqlite://")
    try:
        with engine.begin() as conn:
            create_legacy_statements_table_without_date_order(conn)

        with pytest.raises(RuntimeError, match="statements.date_order"):
            connection_module.init_core_db(engine)
    finally:
        engine.dispose()


def test_init_core_db_rejects_retired_schema_tables():
    """Verify startup rejects retired compatibility tables."""
    engine = create_engine("sqlite://")
    try:
        with engine.begin() as conn:
            conn.execute(text("CREATE TABLE schema_migrations (version TEXT PRIMARY KEY)"))

        with pytest.raises(RuntimeError, match="retired tables: schema_migrations"):
            connection_module.init_core_db(engine)
    finally:
        engine.dispose()


def test_core_schema_creates_category_tag_tables(schema_conn):
    """Verify Core metadata creates taxonomy and merchant identity tables."""
    tables = set(inspect(schema_conn).get_table_names())

    assert {"tags", "transaction_tags", "category_rule_tags"}.issubset(tables)
    assert "merchants" in tables
    assert "merchant_aliases" not in tables


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
    income_id = category_id(schema_conn, "Income")
    schema_conn.execute(
        insert(transactions_table).values(
            tx_date="2026-01-01",
            description="PAYROLL",
            amount=-100,
            category_id=income_id,
            category="Income",
            fingerprint="tx-income",
        )
    )
    schema_conn.execute(
        insert(category_rules_table).values(
            keyword="PAYROLL",
            category_id=income_id,
            category="Income",
        )
    )

    assert rename_category(schema_conn, "Income", "Earnings") == "Earnings"

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
    unknown_id = category_id(schema_conn, "UNKNOWN")
    transfers_id = category_id(schema_conn, "Transfers")

    rows = {
        row["name"]: row
        for row in schema_conn.execute(
            select(
                categories_table.c.id,
                categories_table.c.name,
                categories_table.c.builtin_key,
            ).where(categories_table.c.id.in_((unknown_id, transfers_id)))
        ).mappings()
    }
    assert rows["UNKNOWN"]["builtin_key"] == "unknown"
    assert rows["Transfers"]["builtin_key"] == "transfers"
    assert rename_category(schema_conn, "UNKNOWN", "UNCATEGORIZED") is None
    assert rename_category(schema_conn, "Transfers", "Balance movement") is None
    assert get_unknown_category(schema_conn) == "UNKNOWN"
