"""Persistence helpers for the categories feature."""

import re
from collections.abc import Iterable
from decimal import Decimal
from typing import Any, cast

from sqlalchemy import and_, case, func, insert, or_, select, update
from sqlalchemy.exc import OperationalError as SqlAlchemyOperationalError
from sqlalchemy.exc import ProgrammingError as SqlAlchemyProgrammingError

from finance_app.core.category_sql import category_label_expression
from finance_app.core.constants import (
    CATEGORY_RULE_DIRECTION_ANY,
    CATEGORY_RULE_DIRECTIONS,
    CATEGORY_RULE_SOURCE_AUTOMATIC,
    CATEGORY_RULE_SOURCE_MANUAL,
    UNKNOWN_CATEGORY,
)
from finance_app.core.money import MoneyValue, quantize_money
from finance_app.database.engine import db_core_transaction
from finance_app.database.tables import (
    categories as categories_table,
)
from finance_app.database.tables import (
    category_rules as category_rules_table,
)
from finance_app.database.tables import (
    merchants as merchants_table,
)
from finance_app.database.tables import normalize_name_key
from finance_app.database.tables import (
    transactions as transactions_table,
)
from finance_app.database.upsert import insert_or_select_unique_row
from finance_app.modules.categories.builtins import builtin_category_names as fallback_builtin_category_names
from finance_app.modules.categories.taxonomy import (
    get_rule_tags_by_rule_id,
    seed_category_taxonomy,
    set_rule_tags,
)

CATEGORY_DATABASE_UNAVAILABLE_ERRORS: tuple[type[Exception], ...] = (
    SqlAlchemyOperationalError,
    SqlAlchemyProgrammingError,
)
CATEGORY_TABLE_MISSING_MARKERS = (
    "no such table: categories",
    "undefined table",
    "doesn't exist",
    "does not exist",
)


def normalize_rule_amount(value: object | None) -> Decimal | None:
    """Return a fixed-scale optional rule amount for persistence and lookup."""
    return quantize_money(cast(MoneyValue | None, value))


def save_category_rule(
    conn: Any,
    keyword: object,
    category: object,
    source: str = CATEGORY_RULE_SOURCE_MANUAL,
    amount_min: object | None = None,
    amount_max: object | None = None,
    tags: Iterable[object] | str | None = None,
    merchant_id: object | None = None,
    account_id: object | None = None,
    direction: object = CATEGORY_RULE_DIRECTION_ANY,
    protect_user_rule: bool = False,
) -> int | None:
    """Create or update a category rule for a merchant scope and amount bounds.

    When `protect_user_rule` is true, an existing manual rule with the
    same scope is left untouched and no replacement rule is created.
    """
    merchant_id = normalize_optional_merchant_id(merchant_id)
    account_id = normalize_optional_account_id(account_id)
    direction = normalize_rule_direction(direction)
    amount_min = normalize_rule_amount(amount_min)
    amount_max = normalize_rule_amount(amount_max)
    category_id = resolve_category_id(conn, category)
    rule_select = existing_rule_select(
        keyword,
        amount_min,
        amount_max,
        merchant_id,
        account_id=account_id,
        direction=direction,
    )
    existing = conn.execute(rule_select).mappings().fetchone()
    if existing is None:
        existing, inserted = insert_or_select_unique_row(
            conn,
            insert(category_rules_table).values(
                merchant_id=merchant_id,
                account_id=account_id,
                keyword=keyword,
                category=category,
                category_id=category_id,
                amount_min=amount_min,
                amount_max=amount_max,
                direction=direction,
                source=source,
            ),
            rule_select,
        )
        if inserted:
            set_rule_tags(conn, existing["id"], tags or [])
            return existing["id"]

    if protect_user_rule and existing["source"] == CATEGORY_RULE_SOURCE_MANUAL:
        return None

    conn.execute(
        update(category_rules_table)
        .where(category_rules_table.c.id == existing["id"])
        .values(
            merchant_id=merchant_id,
            account_id=account_id,
            keyword=keyword,
            category=category,
            category_id=category_id,
            amount_min=amount_min,
            amount_max=amount_max,
            direction=direction,
            source=source,
            ai_approved=0,
        )
    )
    set_rule_tags(conn, existing["id"], tags or [])
    return existing["id"]


def find_existing_rule(
    conn: Any,
    keyword: object,
    amount_min: object | None = None,
    amount_max: object | None = None,
    merchant_id: object | None = None,
    account_id: object | None = None,
    direction: object = CATEGORY_RULE_DIRECTION_ANY,
) -> Any:
    """Return the existing Core rule for a merchant or keyword scope."""
    return (
        conn.execute(
            existing_rule_select(
                keyword,
                amount_min,
                amount_max,
                merchant_id,
                account_id=account_id,
                direction=direction,
            )
        )
        .mappings()
        .fetchone()
    )


def existing_rule_select(
    keyword: object,
    amount_min: object | None = None,
    amount_max: object | None = None,
    merchant_id: object | None = None,
    account_id: object | None = None,
    direction: object = CATEGORY_RULE_DIRECTION_ANY,
) -> Any:
    """Return the unique-key select for one category rule scope."""
    account_id = normalize_optional_account_id(account_id)
    direction = normalize_rule_direction(direction)
    amount_min = normalize_rule_amount(amount_min)
    amount_max = normalize_rule_amount(amount_max)
    scope_condition = (
        category_rules_table.c.merchant_id == merchant_id
        if merchant_id is not None
        else and_(
            category_rules_table.c.merchant_id.is_(None),
            category_rules_table.c.keyword == keyword,
        )
    )
    amount_min_condition = (
        category_rules_table.c.amount_min.is_(None)
        if amount_min is None
        else category_rules_table.c.amount_min == amount_min
    )
    amount_max_condition = (
        category_rules_table.c.amount_max.is_(None)
        if amount_max is None
        else category_rules_table.c.amount_max == amount_max
    )
    account_condition = (
        category_rules_table.c.account_id.is_(None)
        if account_id is None
        else category_rules_table.c.account_id == account_id
    )

    return (
        select(category_rules_table.c.id, category_rules_table.c.source)
        .where(
            scope_condition,
            account_condition,
            category_rules_table.c.direction == direction,
            amount_min_condition,
            amount_max_condition,
        )
        .order_by(
            case((category_rules_table.c.source == CATEGORY_RULE_SOURCE_MANUAL, 0), else_=1),
            category_rules_table.c.id,
        )
        .limit(1)
    )


def get_category_options(conn: Any | None = None) -> list[str]:
    """Return category options.

    Missing taxonomy tables fall back to UNKNOWN during early database
    initialization. Other query or schema errors are allowed to propagate so
    tests and operators see real defects.
    """
    if conn is None:
        with db_core_transaction() as conn:
            return get_category_options(conn)

    try:
        categories = fetch_category_names(conn)
        if not categories:
            seed_category_taxonomy(conn)
            categories = fetch_category_names(conn)
    except CATEGORY_DATABASE_UNAVAILABLE_ERRORS as exc:
        if not is_missing_categories_table_error(exc):
            raise
        return [UNKNOWN_CATEGORY]

    return categories or [UNKNOWN_CATEGORY]


def fetch_category_names(conn: Any) -> list[str]:
    """Fetch category names."""
    rows = (
        conn.execute(
            select(categories_table.c.name).order_by(
                case((categories_table.c.builtin_key.is_not(None), 1), else_=0),
                func.lower(categories_table.c.name),
                categories_table.c.name,
            )
        )
        .mappings()
        .fetchall()
    )
    return [row["name"] for row in rows]


def get_builtin_category_names(conn: Any | None = None) -> list[str]:
    """Return persisted category names managed by FinScope.

    Args:
        conn: Optional active database connection. When omitted, this helper
            opens its own transaction.

    Returns:
        A list of category names whose taxonomy rows have a ``builtin_key``.
        If the taxonomy table is not available yet, the static built-in
        definitions are returned as a fallback.
    """
    if conn is None:
        try:
            with db_core_transaction() as conn:
                return get_builtin_category_names(conn)
        except CATEGORY_DATABASE_UNAVAILABLE_ERRORS as exc:
            if not is_missing_categories_table_error(exc):
                raise
            return list(fallback_builtin_category_names())

    try:
        categories = fetch_builtin_category_names(conn)
        if not categories:
            seed_category_taxonomy(conn)
            categories = fetch_builtin_category_names(conn)
    except CATEGORY_DATABASE_UNAVAILABLE_ERRORS as exc:
        if not is_missing_categories_table_error(exc):
            raise
        return list(fallback_builtin_category_names())

    return categories or list(fallback_builtin_category_names())


def fetch_builtin_category_names(conn: Any) -> list[str]:
    """Fetch category names whose rows are marked with a built-in key."""
    rows = (
        conn.execute(
            select(categories_table.c.name)
            .where(categories_table.c.builtin_key.is_not(None))
            .order_by(
                func.lower(categories_table.c.name),
                categories_table.c.name,
            )
        )
        .mappings()
        .fetchall()
    )
    return [row["name"] for row in rows]


def is_missing_categories_table_error(exc: BaseException) -> bool:
    """Return whether a database error means the categories table is absent.

    Args:
        exc: SQLAlchemy operational or programming exception raised while
            reading taxonomy categories.

    Returns:
        ``True`` only for known table-missing messages. Missing columns,
        malformed SQL, server outages, and Python defects intentionally return
        ``False`` so callers do not hide broken schemas or query bugs.
    """
    message = " ".join(str(part) for part in (exc, getattr(exc, "orig", ""))).casefold()
    return any(marker in message for marker in CATEGORY_TABLE_MISSING_MARKERS)


def create_category(conn: Any, name: object) -> str | None:
    """Create category."""
    category = clean_category_name(name)
    if not category:
        return None

    category_select = select(categories_table.c.id).where(categories_table.c.name_key == normalize_name_key(category))
    existing = conn.execute(category_select).fetchone()
    if existing is None:
        insert_or_select_unique_row(
            conn,
            insert(categories_table).values(name=category),
            category_select,
        )
    return category


def resolve_category_id(conn: Any, category: object) -> int | None:
    """Return the persisted category ID for a category label.

    The resolver only links to an existing taxonomy row; callers that accept
    new categories must create them before resolving the foreign key.
    """
    category_name = clean_category_name(category)
    if not category_name:
        return None

    row = (
        conn.execute(
            select(categories_table.c.id).where(categories_table.c.name_key == normalize_name_key(category_name))
        )
        .mappings()
        .fetchone()
    )
    if row is not None:
        return row["id"]

    return None


def rename_category(conn: Any, old_name: object, new_name: object) -> str | None:
    """Rename category."""
    old_category = normalize_category(old_name, get_category_options(conn))
    new_category = clean_category_name(new_name)

    if not old_category or not new_category:
        return None

    if old_category == new_category:
        return new_category

    old_row = (
        conn.execute(
            select(categories_table.c.id, categories_table.c.builtin_key).where(
                categories_table.c.name_key == normalize_name_key(old_category)
            )
        )
        .mappings()
        .fetchone()
    )
    if old_row is None:
        return None
    if old_row["builtin_key"]:
        return None

    existing = (
        conn.execute(
            select(categories_table.c.id).where(categories_table.c.name_key == normalize_name_key(new_category))
        )
        .mappings()
        .fetchone()
    )
    if existing and existing["id"] != old_row["id"]:
        return None

    conn.execute(update(categories_table).where(categories_table.c.id == old_row["id"]).values(name=new_category))
    for table in (transactions_table, category_rules_table):
        conn.execute(update(table).where(table.c.category_id == old_row["id"]).values(category=new_category))

    return new_category


def get_category_rules(conn: Any | None = None) -> list[dict[str, Any]]:
    """Return category rules."""
    if conn is None:
        with db_core_transaction() as conn:
            return get_category_rules(conn)

    rows = (
        conn.execute(
            select(
                category_rules_table.c.id,
                category_rules_table.c.account_id,
                category_rules_table.c.merchant_id,
                merchants_table.c.merchant_key.label("merchant_name"),
                category_rules_table.c.keyword,
                category_label_expression(category_rules_table, UNKNOWN_CATEGORY).label("category"),
                category_rules_table.c.category_id,
                category_rules_table.c.amount_min,
                category_rules_table.c.amount_max,
                category_rules_table.c.direction,
                category_rules_table.c.source,
                category_rules_table.c.ai_approved,
            )
            .select_from(
                category_rules_table.outerjoin(
                    merchants_table,
                    merchants_table.c.id == category_rules_table.c.merchant_id,
                )
            )
            .order_by(
                case(
                    (category_rules_table.c.source == CATEGORY_RULE_SOURCE_MANUAL, 0),
                    (category_rules_table.c.source == CATEGORY_RULE_SOURCE_AUTOMATIC, 1),
                    else_=2,
                ),
                case(
                    (
                        or_(
                            category_rules_table.c.amount_min.is_not(None),
                            category_rules_table.c.amount_max.is_not(None),
                        ),
                        0,
                    ),
                    else_=1,
                ),
                case((category_rules_table.c.merchant_id.is_not(None), 0), else_=1),
                case((category_rules_table.c.account_id.is_not(None), 0), else_=1),
                category_rules_table.c.direction,
                func.length(category_rules_table.c.keyword).desc(),
            )
        )
        .mappings()
        .fetchall()
    )
    rules = [dict(row) for row in rows]
    tags_by_rule_id = get_rule_tags_by_rule_id(conn, [rule["id"] for rule in rules])
    for rule in rules:
        if rule["amount_min"] is not None:
            rule["amount_min"] = quantize_money(rule["amount_min"])
        if rule["amount_max"] is not None:
            rule["amount_max"] = quantize_money(rule["amount_max"])
        rule["tags"] = tags_by_rule_id.get(rule["id"], [])
    return rules


def normalize_category(category: object, allowed_categories: Iterable[str] | None = None) -> str:
    """Normalize category."""
    text = str(category or "").strip()

    for allowed in allowed_categories or ():
        if allowed.upper() == text.upper():
            return allowed
    return UNKNOWN_CATEGORY


def clean_category_name(value: object) -> str:
    """Clean category name."""
    return re.sub(r"\s+", " ", str(value or "").strip())


def normalize_optional_merchant_id(value: object) -> int | None:
    """Return an optional positive merchant ID."""
    if value in (None, ""):
        return None
    try:
        parsed = int(str(value))
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def normalize_optional_account_id(value: object) -> int | None:
    """Return an optional positive account ID."""
    if value in (None, ""):
        return None
    try:
        parsed = int(str(value))
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def normalize_rule_direction(value: object) -> str:
    """Return a valid category-rule direction constraint."""
    text = str(value or CATEGORY_RULE_DIRECTION_ANY).strip().lower()
    return text if text in CATEGORY_RULE_DIRECTIONS else CATEGORY_RULE_DIRECTION_ANY
