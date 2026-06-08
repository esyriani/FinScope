"""Shared database setup helpers for tests.

Provides row factories and builder objects backed by SQLAlchemy Core table
metadata and existing application taxonomy helpers. Callers pass an active test
connection and the helpers commit the setup rows they create.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from itertools import count

from sqlalchemy import insert, select, update

from finance_app.core.constants import (
    ACCOUNT_TYPE_CHECKING,
    CATEGORY_RULE_DIRECTION_ANY,
    CATEGORY_RULE_SOURCE_MANUAL,
    CATEGORY_SOURCE_UNKNOWN,
    STATEMENT_IMPORT_MODE_LEDGER,
    STATEMENT_TYPE_PARSER_CREDIT_CARD,
    TRANSACTION_KIND_EXPENSE,
    USER_ROLE_EDITOR,
)
from finance_app.database.tables import (
    accounts as accounts_table,
    category_rules as category_rules_table,
    merchants as merchants_table,
    statement_types as statement_types_table,
    statements as statements_table,
    tags as tags_table,
    transactions as transactions_table,
    user_settings as user_settings_table,
    users as users_table,
)
from finance_app.modules.categories.repository import resolve_category_id
from finance_app.modules.categories.taxonomy import (
    set_rule_tags,
    set_transaction_tags,
    upsert_tag_metadata,
)
from finance_app.modules.merchants.repository import get_or_create_merchant_for_description
from finance_app.modules.auth import repository as auth_repository

UNIQUE_COUNTER = count(1)


@dataclass(frozen=True)
class UserBuilder:
    """Builder for persisted user rows created through ``TestDataFactory``."""

    factory: "TestDataFactory"

    def create(self, **overrides):
        """Create a user row and return its generated id."""
        return self.factory.user(**overrides)


@dataclass(frozen=True)
class AccountBuilder:
    """Builder for persisted account rows created through ``TestDataFactory``."""

    factory: "TestDataFactory"

    def create(self, **overrides):
        """Create or update an account row and return its id."""
        return self.factory.account(**overrides)


@dataclass(frozen=True)
class StatementBuilder:
    """Builder for persisted statement rows created through ``TestDataFactory``."""

    factory: "TestDataFactory"

    def create(self, **overrides):
        """Create a statement row and return its generated id."""
        return self.factory.statement(**overrides)


@dataclass(frozen=True)
class TransactionBuilder:
    """Builder for persisted transaction rows created through ``TestDataFactory``."""

    factory: "TestDataFactory"

    def create(self, **overrides):
        """Create a transaction row and return its generated id."""
        return self.factory.transaction(**overrides)


@dataclass(frozen=True)
class RuleBuilder:
    """Builder for persisted category rule rows created through ``TestDataFactory``."""

    factory: "TestDataFactory"

    def create(self, **overrides):
        """Create a category rule row and return its generated id."""
        return self.factory.rule(**overrides)


@dataclass(frozen=True)
class TagBuilder:
    """Builder for persisted tag rows created through ``TestDataFactory``."""

    factory: "TestDataFactory"

    def create(self, **overrides):
        """Create or update a tag row and return its id."""
        return self.factory.tag(**overrides)


class TestDataFactory:
    """Coordinate shared builders around one active test database connection."""

    __test__ = False

    def __init__(self, conn):
        """Store the connection and expose domain-specific builders."""
        self.conn = conn
        self.users = UserBuilder(self)
        self.accounts = AccountBuilder(self)
        self.statements = StatementBuilder(self)
        self.transactions = TransactionBuilder(self)
        self.rules = RuleBuilder(self)
        self.tags = TagBuilder(self)

    def unique(self, prefix):
        """Return a deterministic unique value for this test process."""
        return unique_test_value(prefix)

    def user(self, **overrides):
        """Create a user row and return its generated id."""
        values = dict(overrides)
        values.setdefault("username", self.unique("user"))
        return insert_user(self.conn, **values)

    def account(self, **overrides):
        """Create or update an account row and return its id."""
        values = dict(overrides)
        values.setdefault("name", self.unique("account"))
        return insert_account(self.conn, **values)

    def statement(self, **overrides):
        """Create a statement row and return its generated id."""
        values = dict(overrides)
        values.setdefault("filename", f"{self.unique('statement')}.csv")
        values.setdefault("checksum", self.unique("statement-checksum"))
        return insert_statement(self.conn, **values)

    def transaction(self, **overrides):
        """Create a transaction row and return its generated id."""
        values = dict(overrides)
        values.setdefault("fingerprint", self.unique("transaction"))
        return insert_transaction(self.conn, **values)

    def rule(self, **overrides):
        """Create a category rule row and return its generated id."""
        return insert_rule(self.conn, **overrides)

    def tag(self, **overrides):
        """Create or update a tag row and return its id."""
        values = dict(overrides)
        values.setdefault("name", self.unique("Tag"))
        return insert_tag(self.conn, **values)


def unique_test_value(prefix):
    """Return a deterministic unique value for persisted test data."""
    return f"{prefix}-{next(UNIQUE_COUNTER)}"


def current_utc_datetime():
    """Return the current UTC datetime for test rows with timestamp columns."""
    return datetime.now(timezone.utc).replace(microsecond=0)


def inserted_primary_key(result):
    """Return the primary key generated by a Core INSERT result.

    Args:
        result: SQLAlchemy cursor result returned by ``Connection.execute``.

    Returns:
        The generated integer primary key when SQLAlchemy exposes one.
    """
    if result.inserted_primary_key:
        return result.inserted_primary_key[0]
    return result.lastrowid


def insert_user(
    conn,
    username=None,
    *,
    password_hash="test-password-hash",
    role=USER_ROLE_EDITOR,
    must_change_password=0,
    is_active=1,
    display_name=None,
    now=None,
):
    """Insert a user row and return its generated id.

    Args:
        conn: Active SQLAlchemy Core connection or compatible test connection.
        username: Login username. A unique test value is generated when absent.
        password_hash: Persisted password hash used by authentication tests.
        role: Persisted user role.
        must_change_password: Password-change flag.
        is_active: Active-user flag.
        display_name: Optional UI display name.
        now: Optional timestamp for created and updated columns.

    Returns:
        The inserted user id.
    """
    created_at = now or current_utc_datetime()
    user_id = auth_repository.insert_user(
        conn,
        username or unique_test_value("user"),
        password_hash,
        role,
        must_change_password,
        created_at,
        is_active=is_active,
        display_name=display_name,
    )
    conn.commit()
    return user_id


def insert_account(
    conn,
    name=None,
    *,
    account_type=ACCOUNT_TYPE_CHECKING,
    paid_from_account_id=None,
    paid_from_account_name=None,
):
    """Insert or update an account row and return its id.

    Args:
        conn: Active SQLAlchemy Core connection or compatible test connection.
        name: Account display name. A unique test value is generated when absent.
        account_type: Persisted account role.
        paid_from_account_id: Optional funding account id for credit cards.
        paid_from_account_name: Optional funding account name to create first.

    Returns:
        The account id.
    """
    account_name = name or unique_test_value("account")
    if paid_from_account_name is not None and paid_from_account_id is None:
        paid_from_account_id = insert_account(
            conn,
            paid_from_account_name,
            account_type=ACCOUNT_TYPE_CHECKING,
        )

    row = conn.execute(select(accounts_table.c.id).where(accounts_table.c.name == account_name)).mappings().fetchone()
    if row is None:
        result = conn.execute(
            insert(accounts_table).values(
                name=account_name,
                account_type=account_type,
                paid_from_account_id=paid_from_account_id,
            )
        )
        account_id = inserted_primary_key(result)
    else:
        account_id = row["id"]
        values = {"account_type": account_type}
        if paid_from_account_id is not None or paid_from_account_name is not None:
            values["paid_from_account_id"] = paid_from_account_id
        conn.execute(update(accounts_table).where(accounts_table.c.id == account_id).values(**values))

    conn.commit()
    return account_id


def insert_statement_type(
    conn,
    name=None,
    *,
    parser_type=STATEMENT_TYPE_PARSER_CREDIT_CARD,
    import_mode=STATEMENT_IMPORT_MODE_LEDGER,
    default_account_type=ACCOUNT_TYPE_CHECKING,
    active=1,
):
    """Insert or update a statement type and return its id.

    Args:
        conn: Active SQLAlchemy Core connection or compatible test connection.
        name: Statement type display name. A unique value is generated when absent.
        parser_type: Parser key stored on the statement type.
        import_mode: Import behavior stored on the statement type.
        default_account_type: Default account role for uploads of this type.
        active: Active statement-type flag.

    Returns:
        The statement type id.
    """
    type_name = name or unique_test_value("statement-type")
    row = (
        conn.execute(select(statement_types_table.c.id).where(statement_types_table.c.name == type_name))
        .mappings()
        .fetchone()
    )
    values = {
        "parser_type": parser_type,
        "import_mode": import_mode,
        "default_account_type": default_account_type,
        "active": 1 if active else 0,
    }
    if row is None:
        result = conn.execute(insert(statement_types_table).values(name=type_name, **values))
        statement_type_id = inserted_primary_key(result)
    else:
        statement_type_id = row["id"]
        conn.execute(
            update(statement_types_table).where(statement_types_table.c.id == statement_type_id).values(**values)
        )

    conn.commit()
    return statement_type_id


def default_statement_type_id(conn):
    """Return an active statement type id, creating one if the seed is absent."""
    row = (
        conn.execute(
            select(statement_types_table.c.id)
            .where(statement_types_table.c.active == 1)
            .order_by(statement_types_table.c.id)
            .limit(1)
        )
        .mappings()
        .fetchone()
    )
    if row is not None:
        return row["id"]
    return insert_statement_type(conn)


def insert_statement(
    conn,
    *,
    account_id=None,
    statement_type_id=None,
    filename=None,
    checksum=None,
    extension=None,
    interac_direction=None,
    date_order=None,
    raw_text="",
    import_status=None,
    import_error=None,
    import_started_at=None,
    import_finished_at=None,
    imported_count=None,
    skipped_count=None,
    ignored_count=None,
    llm_candidate_count=None,
    uploaded_at=None,
):
    """Insert a statement row and return its generated id.

    Args:
        conn: Active SQLAlchemy Core connection or compatible test connection.
        account_id: Optional linked account id. A test account is created when
            omitted.
        statement_type_id: Optional linked statement type id. The first active
            type is used when omitted.
        filename: Persisted upload filename.
        checksum: Unique persisted upload checksum.
        extension: Optional file extension override.
        interac_direction: Optional Interac parsing direction override.
        date_order: Optional date-order override.
        raw_text: Raw statement text.
        import_status: Optional import status override.
        import_error: Optional import error text.
        import_started_at: Optional import start timestamp.
        import_finished_at: Optional import finish timestamp.
        imported_count: Optional imported-row count.
        skipped_count: Optional skipped-row count.
        ignored_count: Optional ignored-row count.
        llm_candidate_count: Optional LLM candidate count.
        uploaded_at: Optional upload timestamp.

    Returns:
        The inserted statement id.
    """
    values = {
        "account_id": account_id if account_id is not None else insert_account(conn),
        "statement_type_id": statement_type_id or default_statement_type_id(conn),
        "filename": filename or f"{unique_test_value('statement')}.csv",
        "checksum": checksum or unique_test_value("statement-checksum"),
        "raw_text": raw_text,
    }
    optional_values = {
        "extension": extension,
        "interac_direction": interac_direction,
        "date_order": date_order,
        "import_status": import_status,
        "import_error": import_error,
        "import_started_at": import_started_at,
        "import_finished_at": import_finished_at,
        "imported_count": imported_count,
        "skipped_count": skipped_count,
        "ignored_count": ignored_count,
        "llm_candidate_count": llm_candidate_count,
        "uploaded_at": uploaded_at,
    }
    values.update({key: value for key, value in optional_values.items() if value is not None})
    result = conn.execute(insert(statements_table).values(**values))
    conn.commit()
    return inserted_primary_key(result)


def insert_tag(conn, name=None, *, description="", instruction="", color=None):
    """Insert or update a tag row and return its id.

    Args:
        conn: Active SQLAlchemy Core connection or compatible test connection.
        name: Tag label. A unique test value is generated when absent.
        description: Optional taxonomy description.
        instruction: Optional LLM taxonomy instruction.
        color: Optional hex color.

    Returns:
        The tag id.

    Raises:
        ValueError: If the supplied name normalizes to a blank tag.
    """
    tag_name = upsert_tag_metadata(
        conn,
        name or unique_test_value("Tag"),
        description,
        instruction,
        color,
    )
    if tag_name is None:
        raise ValueError("Tag name cannot be blank.")

    tag_id = conn.execute(select(tags_table.c.id).where(tags_table.c.name == tag_name)).scalar_one()
    conn.commit()
    return tag_id


def ensure_tag_rows(conn, tag_names):
    """Ensure all supplied tag names exist before linking tag relationships."""
    for tag_name in tag_names or []:
        upsert_tag_metadata(conn, tag_name)


def insert_rule(
    conn,
    keyword="METRO",
    category="Food",
    amount_min=None,
    amount_max=None,
    *,
    source=CATEGORY_RULE_SOURCE_MANUAL,
    ai_approved=0,
    merchant_id=None,
    account_id=None,
    direction=CATEGORY_RULE_DIRECTION_ANY,
    tags=None,
):
    """Insert a category rule and return its generated id.

    Args:
        conn: Active SQLAlchemy Core connection or compatible test connection.
        keyword: Rule keyword stored exactly as supplied.
        category: Category label assigned by the rule.
        amount_min: Optional lower amount bound.
        amount_max: Optional upper amount bound.
        source: Persisted rule source.
        ai_approved: Approval flag for automatic rules.
        merchant_id: Optional merchant scope.
        account_id: Optional account scope.
        direction: Optional transaction direction scope.
        tags: Optional tag names to associate with the rule.

    Returns:
        The inserted rule id.
    """
    result = conn.execute(
        insert(category_rules_table).values(
            account_id=account_id,
            merchant_id=merchant_id,
            keyword=keyword,
            category=category,
            category_id=resolve_category_id(conn, category),
            amount_min=amount_min,
            amount_max=amount_max,
            direction=direction,
            source=source,
            ai_approved=ai_approved,
        )
    )
    rule_id = inserted_primary_key(result)
    if tags is not None:
        ensure_tag_rows(conn, tags)
        set_rule_tags(conn, rule_id, tags)
    conn.commit()
    return rule_id


def insert_merchant(conn, name="TEST MERCHANT"):
    """Insert a merchant row and return its generated id.

    Args:
        conn: Active SQLAlchemy Core connection or compatible test connection.
        name: Merchant key to persist.

    Returns:
        The inserted merchant id.
    """
    result = conn.execute(insert(merchants_table).values(merchant_key=name))
    conn.commit()
    return inserted_primary_key(result)


def insert_transaction(
    conn,
    description="Metro Grocery",
    amount=25.0,
    category="UNKNOWN",
    *,
    fingerprint="test-transaction",
    tx_date="2026-01-02",
    account_id=None,
    statement_id=None,
    merchant_id=None,
    merchant_from_description=False,
    category_id=None,
    category_source=CATEGORY_SOURCE_UNKNOWN,
    category_confidence=None,
    category_rule_id=None,
    category_metadata=None,
    categorized_at=None,
    reviewed_at=None,
    needs_review=1,
    ignored=0,
    transaction_kind=TRANSACTION_KIND_EXPENSE,
    tags=None,
    tag_source=CATEGORY_SOURCE_UNKNOWN,
):
    """Insert a transaction row and return its generated id.

    Args:
        conn: Active SQLAlchemy Core connection or compatible test connection.
        description: Transaction description.
        amount: Transaction amount.
        category: Persisted category label.
        fingerprint: Unique test fingerprint.
        tx_date: ISO transaction date.
        account_id: Optional linked account id.
        statement_id: Optional linked statement id.
        merchant_id: Optional linked merchant id.
        merchant_from_description: Whether to create/link a merchant from the
            description when ``merchant_id`` is not provided.
        category_id: Optional category taxonomy id. When omitted, the helper
            resolves the id for the supplied category label.
        category_source: Persisted categorization source.
        category_confidence: Optional categorization confidence.
        category_rule_id: Optional linked category rule id.
        category_metadata: Optional JSON metadata string.
        categorized_at: Optional categorization timestamp.
        reviewed_at: Optional review timestamp.
        needs_review: Review flag.
        ignored: Ignored flag.
        transaction_kind: Persisted transaction kind.
        tags: Optional tag names to associate with the transaction.
        tag_source: Source stored for tag assignments.

    Returns:
        The inserted transaction id.
    """
    if merchant_id is None and merchant_from_description:
        merchant = get_or_create_merchant_for_description(conn, description)
        merchant_id = merchant["id"] if merchant else None
    if category_id is None:
        category_id = resolve_category_id(conn, category)

    result = conn.execute(
        insert(transactions_table).values(
            statement_id=statement_id,
            account_id=account_id,
            merchant_id=merchant_id,
            tx_date=tx_date,
            description=description,
            amount=amount,
            category=category,
            category_id=category_id,
            needs_review=needs_review,
            category_source=category_source,
            category_confidence=category_confidence,
            category_rule_id=category_rule_id,
            category_metadata=category_metadata,
            categorized_at=categorized_at,
            reviewed_at=reviewed_at,
            ignored=ignored,
            transaction_kind=transaction_kind,
            fingerprint=fingerprint,
        )
    )
    transaction_id = inserted_primary_key(result)
    if tags is not None:
        ensure_tag_rows(conn, tags)
        set_transaction_tags(conn, transaction_id, tags, source=tag_source, rule_id=category_rule_id)
    conn.commit()
    return transaction_id


def set_owner_setting(conn, key, value):
    """Persist one owner-bound runtime setting.

    Args:
        conn: Active SQLAlchemy Core connection or compatible test connection.
        key: Setting key.
        value: Setting value, converted to text for storage.
    """
    owner_id = conn.execute(select(users_table.c.id).where(users_table.c.username == "owner")).scalar_one()
    result = conn.execute(
        update(user_settings_table)
        .where(
            user_settings_table.c.user_id == owner_id,
            user_settings_table.c.key == key,
        )
        .values(value=str(value))
    )
    if result.rowcount == 0:
        conn.execute(
            insert(user_settings_table).values(
                user_id=owner_id,
                key=key,
                value=str(value),
            )
        )
    conn.commit()
