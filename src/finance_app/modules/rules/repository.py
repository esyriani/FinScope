"""Persistence helpers for the rules feature."""

from sqlalchemy import and_, delete, func, insert, select

from finance_app.core.money import optional_money_to_float
from finance_app.database.tables import (
    accounts as accounts_table,
    categories as categories_table,
    category_rules as category_rules_table,
    merchants as merchants_table,
    transactions as transactions_table,
)
from finance_app.modules.categories.repository import (
    normalize_optional_account_id,
    normalize_optional_merchant_id,
    normalize_rule_direction,
    resolve_category_id,
)
from finance_app.modules.categories.taxonomy import get_rule_tags_by_rule_id, set_rule_tags
from finance_app.modules.categories.service import create_category
from finance_app.modules.merchants.repository import find_merchant_by_name, get_or_create_merchant_for_name


def existing_category_names(conn):
    """Return existing category names."""
    return {
        row["name"]
        for row in conn.execute(select(categories_table.c.name)).mappings().fetchall()
    }


def ensure_import_category(conn, category, existing_categories, created_categories):
    """Ensure import category."""
    if category in existing_categories:
        return

    create_category(conn, category)
    existing_categories.add(category)
    created_categories.append(category)


def category_rule_exists(conn, rule):
    """Build rule exists."""
    merchant_id = resolve_rule_merchant_id(conn, rule)
    account_id = resolve_rule_account_id(conn, rule)
    if rule.get("merchant_name") and merchant_id is None:
        return False
    if rule.get("account_name") and account_id is None:
        return False

    amount_min = rule.get("amount_min")
    amount_max = rule.get("amount_max")
    direction = normalize_rule_direction(rule.get("direction"))
    scope_condition = (
        category_rules_table.c.merchant_id == merchant_id
        if merchant_id is not None
        else and_(
            category_rules_table.c.merchant_id.is_(None),
            category_rules_table.c.keyword == rule["keyword"],
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
    return conn.execute(
        select(category_rules_table.c.id)
        .where(
            scope_condition,
            account_condition,
            category_rules_table.c.direction == direction,
            amount_min_condition,
            amount_max_condition,
        )
        .limit(1)
    ).fetchone() is not None


def insert_imported_rule(conn, rule):
    """Handle insert imported rule."""
    merchant_id = resolve_rule_merchant_id(conn, rule, create=True)
    account_id = resolve_rule_account_id(conn, rule, require_existing=True)
    values = {
        "account_id": account_id,
        "merchant_id": merchant_id,
        "keyword": rule["keyword"],
        "category": rule["category"],
        "category_id": resolve_category_id(conn, rule["category"]),
        "amount_min": rule["amount_min"],
        "amount_max": rule["amount_max"],
        "direction": normalize_rule_direction(rule.get("direction")),
        "source": rule["source"],
        "ai_approved": int(rule.get("ai_approved") or 0),
    }
    if rule.get("created_at"):
        values["created_at"] = rule["created_at"]

    result = conn.execute(insert(category_rules_table).values(**values))
    rule_id = result.inserted_primary_key[0]
    set_rule_tags(conn, rule_id, rule.get("tags", []))
    return rule_id


def resolve_rule_merchant_id(conn, rule, create=False):
    """Resolve an imported or snapshotted rule to an optional merchant ID."""
    merchant_id = normalize_optional_merchant_id(rule.get("merchant_id"))
    if merchant_id is not None:
        return merchant_id

    merchant_name = str(rule.get("merchant_name") or "").strip()
    if not merchant_name:
        return None

    merchant = (
        get_or_create_merchant_for_name(conn, merchant_name)
        if create
        else find_merchant_by_name(conn, merchant_name)
    )
    return merchant["id"] if merchant else None


def resolve_rule_account_id(conn, rule, require_existing=False):
    """Resolve an imported or snapshotted rule to an optional account ID.

    When ``require_existing`` is true, an explicit account name must already
    exist. Import code uses this strict path so a misspelled account cannot
    silently turn an account-scoped rule into a broad rule.
    """
    account_id = normalize_optional_account_id(rule.get("account_id"))
    if account_id is not None:
        return account_id

    account_name = str(rule.get("account_name") or "").strip()
    if not account_name:
        return None

    row = conn.execute(
        select(accounts_table.c.id).where(accounts_table.c.name == account_name)
    ).mappings().fetchone()
    if row:
        return row["id"]
    if require_existing:
        raise ValueError(f"Account {account_name!r} was not found.")
    return None


def snapshot_category_rules(conn):
    """Handle snapshot category rules."""
    rows = [
        dict(row)
        for row in conn.execute(
            select(
                category_rules_table.c.id,
                category_rules_table.c.account_id,
                accounts_table.c.name.label("account_name"),
                category_rules_table.c.merchant_id,
                merchants_table.c.merchant_key.label("merchant_name"),
                category_rules_table.c.keyword,
                category_rules_table.c.category,
                category_rules_table.c.category_id,
                category_rules_table.c.amount_min,
                category_rules_table.c.amount_max,
                category_rules_table.c.direction,
                category_rules_table.c.source,
                category_rules_table.c.ai_approved,
                category_rules_table.c.created_at,
            )
            .select_from(
                category_rules_table
                .outerjoin(
                    accounts_table,
                    accounts_table.c.id == category_rules_table.c.account_id,
                )
                .outerjoin(
                    merchants_table,
                    merchants_table.c.id == category_rules_table.c.merchant_id,
                )
            )
            .order_by(category_rules_table.c.id)
        ).mappings().fetchall()
    ]
    tags_by_rule_id = get_rule_tags_by_rule_id(conn, [row["id"] for row in rows])
    for row in rows:
        row["tags"] = tags_by_rule_id.get(row["id"], [])
    return [rule_snapshot(row) for row in rows]


def snapshot_rule_by_id(conn, rule_id):
    """Handle snapshot rule by ID."""
    row = conn.execute(
        select(
            category_rules_table.c.id,
            category_rules_table.c.account_id,
            accounts_table.c.name.label("account_name"),
            category_rules_table.c.merchant_id,
            merchants_table.c.merchant_key.label("merchant_name"),
            category_rules_table.c.keyword,
            category_rules_table.c.category,
            category_rules_table.c.category_id,
            category_rules_table.c.amount_min,
            category_rules_table.c.amount_max,
            category_rules_table.c.direction,
            category_rules_table.c.source,
            category_rules_table.c.ai_approved,
            category_rules_table.c.created_at,
        )
        .select_from(
            category_rules_table
            .outerjoin(
                accounts_table,
                accounts_table.c.id == category_rules_table.c.account_id,
            )
            .outerjoin(
                merchants_table,
                merchants_table.c.id == category_rules_table.c.merchant_id,
            )
        )
        .where(category_rules_table.c.id == rule_id)
    ).mappings().fetchone()
    if not row:
        return None
    row = dict(row)
    row["tags"] = get_rule_tags_by_rule_id(conn, [rule_id]).get(rule_id, [])
    return rule_snapshot(row)


def rule_snapshot(row):
    """Build snapshot."""
    return {
        "id": row["id"],
        "account_id": row.get("account_id"),
        "account_name": row.get("account_name"),
        "merchant_id": row.get("merchant_id"),
        "merchant_name": row.get("merchant_name"),
        "keyword": row["keyword"],
        "category": row["category"],
        "category_id": row.get("category_id"),
        "amount_min": optional_money_to_float(row["amount_min"]),
        "amount_max": optional_money_to_float(row["amount_max"]),
        "direction": normalize_rule_direction(row.get("direction")),
        "source": row["source"],
        "ai_approved": row.get("ai_approved", 0),
        "created_at": row["created_at"],
        "tags": list(row.get("tags") or []),
    }


def rule_snapshots_equal(left, right):
    """Build snapshots equal."""
    columns = (
        "id",
        "account_id",
        "merchant_id",
        "keyword",
        "category",
        "category_id",
        "amount_min",
        "amount_max",
        "direction",
        "source",
        "ai_approved",
        "created_at",
    )
    return [
        tuple(rule[column] for column in columns) + (tuple(rule.get("tags") or []),)
        for rule in left
    ] == [
        tuple(rule[column] for column in columns) + (tuple(rule.get("tags") or []),)
        for rule in right
    ]


def snapshot_transaction_rule_refs(conn):
    """Handle snapshot transaction rule refs."""
    rows = conn.execute(
        select(
            transactions_table.c.id.label("transaction_id"),
            transactions_table.c.category_rule_id,
        )
        .where(transactions_table.c.category_rule_id.is_not(None))
        .order_by(transactions_table.c.id)
    ).mappings().fetchall()
    return [
        {
            "transaction_id": row["transaction_id"],
            "category_rule_id": row["category_rule_id"],
        }
        for row in rows
    ]


def rule_reference_count(conn, rule_ids):
    """Build reference count."""
    if isinstance(rule_ids, int):
        rule_ids = [rule_ids]

    rule_ids = [rule_id for rule_id in rule_ids if rule_id is not None]
    if not rule_ids:
        return 0

    return conn.execute(
        select(func.count().label("count"))
        .select_from(transactions_table)
        .where(transactions_table.c.category_rule_id.in_(rule_ids))
    ).scalar_one()


def restore_category_rules(conn, rules):
    """Restore category rules."""
    for rule in rules:
        category_id = rule.get("category_id")
        if category_id is None:
            category_id = resolve_category_id(conn, rule["category"])

        conn.execute(
            insert(category_rules_table).values(
                id=rule["id"],
                account_id=rule.get("account_id"),
                merchant_id=rule["merchant_id"],
                keyword=rule["keyword"],
                category=rule["category"],
                category_id=category_id,
                amount_min=rule["amount_min"],
                amount_max=rule["amount_max"],
                direction=normalize_rule_direction(rule.get("direction")),
                source=rule["source"],
                ai_approved=int(rule.get("ai_approved") or 0),
                created_at=rule["created_at"],
            )
        )
        set_rule_tags(conn, rule["id"], rule.get("tags", []))


def remove_imported_categories(conn, categories):
    """Remove imported categories."""
    removed = 0

    for category in categories:
        transaction_count = conn.execute(
            select(func.count().label("count"))
            .select_from(transactions_table)
            .where(transactions_table.c.category == category)
        ).scalar_one()
        rule_count = conn.execute(
            select(func.count().label("count"))
            .select_from(category_rules_table)
            .where(category_rules_table.c.category == category)
        ).scalar_one()
        if transaction_count or rule_count:
            continue

        result = conn.execute(
            delete(categories_table).where(categories_table.c.name == category)
        )
        removed += result.rowcount or 0

    return removed
