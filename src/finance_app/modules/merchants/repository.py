"""Merchant persistence helpers.

Provides durable merchant-key identity helpers for SQLAlchemy Core callers.
Merchant rows are deterministic keys derived from transaction descriptions;
there is no user-managed alias or display-name layer.
"""

from collections.abc import Mapping
from typing import Any

from sqlalchemy import insert, select

from finance_app.database.tables import merchants as merchants_table
from finance_app.database.upsert import insert_or_select_unique_row
from finance_app.modules.merchants.normalization import normalize_merchant


def get_or_create_merchant_for_description(conn: Any, description: object) -> Any:
    """Return a merchant row for an imported transaction description.

    The raw description is cleaned into a deterministic merchant key. The
    merchant row stores only that key so no hidden alias or display-name state
    can change transaction grouping.
    """
    normalized = normalize_merchant(description)
    return get_or_create_merchant(conn, normalized.merchant_key)


def get_or_create_merchant_for_name(conn: Any, merchant_name: object) -> Any:
    """Return a merchant row for a merchant key or raw merchant label.

    This is used by features, such as recurring pattern edits, that receive a
    merchant label instead of a raw bank transaction description. The label is
    normalized the same way as imported transaction descriptions.
    """
    existing = find_merchant_by_name(conn, merchant_name)
    if existing:
        return existing

    normalized = normalize_merchant(merchant_name)
    return get_or_create_merchant(conn, normalized.merchant_key)


def get_or_create_merchant(
    conn: Any,
    merchant_key: object,
) -> Any:
    """Return a merchant row, inserting a deterministic merchant key if needed."""
    merchant_key = normalize_merchant(merchant_key).merchant_key
    if not merchant_key:
        return None

    existing = find_merchant_by_key(conn, merchant_key)
    if existing is None:
        existing, _inserted = insert_or_select_unique_row(
            conn,
            insert(merchants_table).values(merchant_key=merchant_key),
            merchant_select_by_key(merchant_key),
        )
        merchant_id = existing["id"]
    else:
        merchant_id = existing["id"]

    return find_merchant_by_id(conn, merchant_id)


def find_merchant_by_id(conn: Any, merchant_id: object) -> Any:
    """Return a merchant row by ID."""
    if merchant_id in (None, ""):
        return None

    return conn.execute(merchant_select().where(merchants_table.c.id == merchant_id)).mappings().fetchone()


def find_merchant_by_key(conn: Any, merchant_key: object) -> Any:
    """Return a merchant row by deterministic merchant key."""
    merchant_key = normalize_merchant(merchant_key).merchant_key
    if not merchant_key:
        return None
    return conn.execute(merchant_select_by_key(merchant_key)).mappings().fetchone()


def merchant_select_by_key(merchant_key: str) -> Any:
    """Return the shared unique-key select for one merchant key."""
    return merchant_select().where(merchants_table.c.merchant_key == merchant_key)


def merchant_select() -> Any:
    """Return the shared merchant select."""
    return select(
        merchants_table.c.id,
        merchants_table.c.merchant_key,
        merchants_table.c.created_at,
        merchants_table.c.updated_at,
    )


def find_merchant_by_name(conn: Any, merchant_name: object) -> Any:
    """Return a merchant row matching a deterministic merchant key."""
    return find_merchant_by_key(conn, merchant_name)


def merchant_identity_from_row(row: Mapping[str, Any], conn: Any | None = None) -> dict[str, Any]:
    """Return merchant identity metadata for a transaction query row.

    Query rows that include `merchant_id` and merchant labels use the durable
    merchant key. Rows that only provide `description` fall back to
    deterministic normalization.
    """
    merchant_id = row_value(row, "merchant_id")
    merchant_name = row_value(row, "merchant_name")
    merchant_key = row_value(row, "merchant_key")
    if merchant_id and merchant_name:
        return {
            "id": merchant_id,
            "key": merchant_identity_key(merchant_id),
            "name": merchant_name,
            "cleaned_key": merchant_key or merchant_name,
        }

    normalized = normalize_merchant(row_value(row, "description") or "", conn=conn)
    return {
        "id": None,
        "key": normalized.merchant_key,
        "name": normalized.merchant_key,
        "cleaned_key": normalized.merchant_key,
    }


def merchant_identity_key(merchant_id: object) -> str:
    """Return the stable in-memory grouping key for a merchant ID."""
    return f"merchant:{merchant_id}"


def row_value(row: Mapping[str, Any], key: str, default: object | None = None) -> Any:
    """Return a row value when the row supports the requested key."""
    try:
        if key in row.keys():
            return row[key]
    except (AttributeError, KeyError, TypeError):
        try:
            return row[key]
        except (KeyError, IndexError, TypeError):
            return default
    return default
