"""Optional live MySQL runtime coverage for backend portability.

These tests use a disposable database created from ``FINSCOPE_TEST_MYSQL_URL``.
They are excluded from default pytest runs by the ``optional`` marker and should
be selected deliberately when a local or CI MySQL service is available.
"""

import os
import re
import uuid
from collections.abc import Iterator
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, func, insert, select, text
from sqlalchemy.engine import URL, Engine, make_url
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from tests.support.database import TestDataFactory

from finance_app.background import repository as background_repository
from finance_app.core.analytics import REPORT_BASIS_CASH_FLOW
from finance_app.core.builtin_taxonomy import BUILTIN_CATEGORY_UNKNOWN, BUILTIN_TAG_REIMBURSABLE
from finance_app.core.constants import (
    BACKGROUND_JOB_STATUS_FAILED,
    BACKGROUND_JOB_STATUS_RUNNING,
    CATEGORY_SOURCE_RULE,
    REIMBURSEMENT_CATEGORY,
    STATEMENT_IMPORT_STATUS_FAILED,
    STATEMENT_IMPORT_STATUS_RUNNING,
    TRANSACTION_KIND_EXPENSE,
    TRANSACTION_KIND_INCOME,
    USER_ROLE_EDITOR,
    USER_ROLE_OWNER,
)
from finance_app.core.money import money_to_decimal
from finance_app.database import connection as connection_module
from finance_app.database import engine as engine_module
from finance_app.database.engine import (
    create_database_engine,
    db_core_transaction,
    dispose_database_engine,
    server_database_url,
)
from finance_app.database.tables import accounts as accounts_table
from finance_app.database.tables import background_job_events as background_job_events_table
from finance_app.database.tables import background_jobs as background_jobs_table
from finance_app.database.tables import categories as categories_table
from finance_app.database.tables import statement_types as statement_types_table
from finance_app.database.tables import statements as statements_table
from finance_app.database.tables import tags as tags_table
from finance_app.database.tables import transactions as transactions_table
from finance_app.database.tables import user_settings as user_settings_table
from finance_app.modules.categories.repository import resolve_category_id
from finance_app.modules.categories.taxonomy import get_transaction_tag_names
from finance_app.modules.reimbursements import queries as reimbursement_queries
from finance_app.modules.reimbursements.service import create_reimbursement_allocation
from finance_app.modules.reports.queries import fetch_report_summary
from finance_app.modules.rules.workflow import apply_single_rule_to_transactions
from finance_app.modules.settings.runtime import get_unknown_category
from finance_app.modules.statements.importer import transaction_fingerprint
from finance_app.modules.transactions.importer import filter_new_transactions

MYSQL_TEST_URL_ENV = "FINSCOPE_TEST_MYSQL_URL"
MYSQL_DIALECTS = {"mariadb", "mysql"}
pytestmark = [pytest.mark.optional, pytest.mark.mysql, pytest.mark.db]


def mysql_test_url() -> URL:
    """Return a SQLAlchemy URL for a disposable live MySQL database."""
    configured_url = os.environ.get(MYSQL_TEST_URL_ENV)
    if not configured_url:
        pytest.skip(f"Set {MYSQL_TEST_URL_ENV} to run optional live MySQL tests.")

    try:
        url = make_url(configured_url)
    except Exception as exc:
        pytest.fail(f"{MYSQL_TEST_URL_ENV} is not a valid SQLAlchemy URL: {exc}")

    dialect_name = url.drivername.split("+", 1)[0].lower()
    if dialect_name not in MYSQL_DIALECTS:
        pytest.fail(f"{MYSQL_TEST_URL_ENV} must use a MySQL or MariaDB SQLAlchemy URL.")

    return url.set(database=disposable_database_name(url.database))


def disposable_database_name(template_name: object) -> str:
    """Return a safe short database name for one optional test run."""
    prefix = re.sub(r"[^0-9A-Za-z_]+", "_", str(template_name or "finscope_test")).strip("_")
    if not prefix:
        prefix = "finscope_test"
    if not prefix.startswith("finscope"):
        prefix = f"finscope_test_{prefix}"
    return f"{prefix[:45]}_{uuid.uuid4().hex[:12]}"


def visible_database_url(url: URL) -> str:
    """Render a URL without hiding the password from SQLAlchemy itself."""
    return url.render_as_string(hide_password=False)


@pytest.fixture
def mysql_database_url() -> Iterator[str]:
    """Create and later drop a disposable MySQL database URL."""
    url = mysql_test_url()
    database_url = visible_database_url(url)
    try:
        yield database_url
    finally:
        drop_disposable_database(url)


@pytest.fixture
def mysql_engine(mysql_database_url: str) -> Iterator[Engine]:
    """Create a live MySQL engine for the disposable test database."""
    try:
        engine = create_database_engine(mysql_database_url)
    except SQLAlchemyError as exc:
        pytest.fail(f"Could not create the disposable MySQL test database: {exc}")

    try:
        yield engine
    finally:
        engine.dispose()


def drop_disposable_database(url: URL) -> None:
    """Drop the per-test MySQL database created for the optional lane."""
    if not url.database:
        return

    server_engine = create_engine(
        server_database_url(url),
        isolation_level="AUTOCOMMIT",
        pool_pre_ping=True,
    )
    try:
        quoted_database = server_engine.dialect.identifier_preparer.quote_identifier(url.database)
        with server_engine.connect() as conn:
            conn.execute(text(f"DROP DATABASE IF EXISTS {quoted_database}"))
    finally:
        server_engine.dispose()


def test_live_mysql_initialization_validates_seeds_and_repairs_runtime_state(mysql_engine: Engine) -> None:
    """Exercise schema initialization, validation, seeding, and startup repair on MySQL."""
    connection_module.init_core_db(mysql_engine)

    with mysql_engine.connect() as conn:
        factory = TestDataFactory(conn)
        owner_id = factory.users.create(username="mysql_owner", role=USER_ROLE_OWNER)
        statement_id = factory.statements.create(import_status=STATEMENT_IMPORT_STATUS_RUNNING)
        background_repository.save_job_snapshot(
            conn,
            {
                "id": "mysql-runtime-repair",
                "label": "MySQL runtime repair",
                "status": BACKGROUND_JOB_STATUS_RUNNING,
                "created_at": "2026-01-01T00:00:00Z",
            },
        )
        conn.commit()

    connection_module.init_core_db(mysql_engine)

    with mysql_engine.connect() as conn:
        connection_module.validate_core_schema(conn)
        assert get_unknown_category(conn) == "UNKNOWN"
        assert conn.execute(select(func.count()).select_from(statement_types_table)).scalar_one() > 0
        assert (
            conn.execute(
                select(categories_table.c.name).where(categories_table.c.builtin_key == BUILTIN_CATEGORY_UNKNOWN)
            ).scalar_one()
            == "UNKNOWN"
        )
        assert (
            conn.execute(select(tags_table.c.name).where(tags_table.c.builtin_key == BUILTIN_TAG_REIMBURSABLE))
            .scalar_one()
            .lower()
            == "reimbursable"
        )
        assert (
            conn.execute(
                select(func.count()).select_from(user_settings_table).where(user_settings_table.c.user_id == owner_id)
            ).scalar_one()
            > 0
        )
        assert (
            conn.execute(
                select(statements_table.c.import_status).where(statements_table.c.id == statement_id)
            ).scalar_one()
            == STATEMENT_IMPORT_STATUS_FAILED
        )
        assert (
            conn.execute(
                select(background_jobs_table.c.status).where(background_jobs_table.c.id == "mysql-runtime-repair")
            ).scalar_one()
            == BACKGROUND_JOB_STATUS_FAILED
        )
        assert (
            conn.execute(
                select(func.count())
                .select_from(background_job_events_table)
                .where(background_job_events_table.c.job_id == "mysql-runtime-repair")
            ).scalar_one()
            == 1
        )


def test_live_mysql_runtime_flows_cover_backend_semantics(
    mysql_database_url: str,
    mysql_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise high-risk Core flows against a live MySQL database."""
    connection_module.init_core_db(mysql_engine)
    assert_db_core_transaction_uses_mysql_savepoints(mysql_database_url, monkeypatch)

    with mysql_engine.connect() as conn:
        factory = TestDataFactory(conn)
        assert_username_uniqueness_is_case_insensitive(conn, factory)

        account_id = factory.accounts.create(name="MySQL Checking")
        statement_id = factory.statements.create(account_id=account_id, checksum="mysql-runtime-statement")

        matching_transaction_id = factory.transactions.create(
            description="Metro Grocery #123",
            amount=Decimal("42.00"),
            account_id=account_id,
            statement_id=statement_id,
            fingerprint="mysql-rule-match",
        )
        rule_id = factory.rules.create(
            keyword="METRO",
            category="Food",
            amount_min=Decimal("10.00"),
            amount_max=Decimal("50.00"),
            account_id=account_id,
            direction="debit",
            tags=["Tax"],
        )
        updated_count = apply_single_rule_to_transactions(
            conn,
            {
                "id": rule_id,
                "keyword": "METRO",
                "category": "Food",
                "category_id": resolve_category_id(conn, "Food"),
                "amount_min": Decimal("10.00"),
                "amount_max": Decimal("50.00"),
                "account_id": account_id,
                "direction": "debit",
                "tags": ["Tax"],
            },
        )
        conn.commit()

        assert updated_count == 1
        assert (
            conn.execute(
                select(transactions_table.c.category_source).where(transactions_table.c.id == matching_transaction_id)
            ).scalar_one()
            == CATEGORY_SOURCE_RULE
        )
        assert get_transaction_tag_names(conn, matching_transaction_id) == ["Tax"]

        assert_import_deduplication_uses_persisted_mysql_fingerprints(conn, factory, account_id, statement_id)
        assert_reimbursements_and_report_totals_use_mysql_runtime_rows(conn, factory)


def assert_db_core_transaction_uses_mysql_savepoints(
    mysql_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify the production transaction helper rolls back nested MySQL work."""
    monkeypatch.setattr(engine_module, "settings", SimpleNamespace(database_url=mysql_database_url))
    dispose_database_engine()
    try:
        with pytest.raises(RuntimeError, match="force mysql rollback"):
            with db_core_transaction() as outer:
                outer.execute(insert(accounts_table).values(name="MySQL rollback outer"))
                with db_core_transaction(conn=outer) as inner:
                    assert inner is outer
                    inner.execute(insert(accounts_table).values(name="MySQL rollback nested"))
                raise RuntimeError("force mysql rollback")

        with db_core_transaction() as conn:
            persisted = conn.execute(
                select(func.count())
                .select_from(accounts_table)
                .where(accounts_table.c.name.in_(("MySQL rollback outer", "MySQL rollback nested")))
            ).scalar_one()
        assert persisted == 0
    finally:
        dispose_database_engine()


def assert_username_uniqueness_is_case_insensitive(conn: object, factory: TestDataFactory) -> None:
    """Verify generated normalized-key uniqueness is enforced by MySQL."""
    factory.users.create(username="MySQL Editor", role=USER_ROLE_EDITOR)
    with pytest.raises(IntegrityError):
        factory.users.create(username=" mysql editor ", role=USER_ROLE_EDITOR)
    conn.rollback()


def assert_import_deduplication_uses_persisted_mysql_fingerprints(
    conn: object,
    factory: TestDataFactory,
    account_id: int,
    statement_id: int,
) -> None:
    """Verify statement import replay dedupe works against MySQL rows."""
    existing_tx = {
        "tx_date": "2026-02-01",
        "description": "Existing MySQL import",
        "amount": Decimal("11.11"),
    }
    factory.transactions.create(
        description=existing_tx["description"],
        amount=existing_tx["amount"],
        account_id=account_id,
        statement_id=statement_id,
        fingerprint=transaction_fingerprint(existing_tx, account_id, statement_id, import_index=1),
        ignored=1,
    )
    fresh_tx = {
        "tx_date": "2026-02-02",
        "description": "Fresh MySQL import",
        "amount": Decimal("22.22"),
    }

    new_transactions, skipped_count = filter_new_transactions(
        conn,
        [existing_tx, dict(existing_tx), fresh_tx],
        account_id,
        statement_id,
    )

    assert skipped_count == 1
    assert [transaction["description"] for transaction in new_transactions] == [
        "Existing MySQL import",
        "Fresh MySQL import",
    ]


def assert_reimbursements_and_report_totals_use_mysql_runtime_rows(conn: object, factory: TestDataFactory) -> None:
    """Verify reimbursement queries and report totals run against MySQL semantics."""
    expense_id = factory.transactions.create(
        description="Conference expense",
        amount=Decimal("300.00"),
        category="Food",
        transaction_kind=TRANSACTION_KIND_EXPENSE,
        needs_review=0,
        fingerprint="mysql-reimbursable-expense",
        tags=["Reimbursable"],
    )
    reimbursement_id = factory.transactions.create(
        description="Employer reimbursement",
        amount=Decimal("-120.00"),
        category=REIMBURSEMENT_CATEGORY,
        transaction_kind=TRANSACTION_KIND_INCOME,
        needs_review=0,
        fingerprint="mysql-reimbursement-credit",
    )

    allocation = create_reimbursement_allocation(reimbursement_id, expense_id, Decimal("120.00"), conn=conn)
    reimbursement_rows = reimbursement_queries.fetch_reimbursement_transactions(conn)
    expense_rows = reimbursement_queries.fetch_reimbursable_expense_transactions(conn)
    allocation_rows = reimbursement_queries.fetch_reimbursement_allocations(conn)
    summary = fetch_report_summary(conn, [transactions_table.c.ignored == 0], "UNKNOWN", REPORT_BASIS_CASH_FLOW)

    assert allocation.expense_remaining == Decimal("180.00")
    assert money_to_decimal(reimbursement_rows[0]["allocated"]) == Decimal("120.00")
    assert money_to_decimal(expense_rows[0]["allocated"]) == Decimal("120.00")
    assert len(allocation_rows) == 1
    assert money_to_decimal(summary["total_spending"]) == Decimal("222.00")
    assert money_to_decimal(summary["total_income"]) == Decimal("0.00")
    assert money_to_decimal(summary["net_cashflow"]) == Decimal("-222.00")
    assert summary["transaction_count"] == 2
