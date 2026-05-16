"""Merchant persistence helpers.

Provides durable merchant identity and alias helpers for SQLAlchemy Core
callers. The helpers depend on merchant normalization for deriving stable keys
from imported transaction descriptions, and callers manage database
transactions.
"""

from sqlalchemy import func, insert, or_, select, update

from finance_app.core.constants import (
    MERCHANT_ALIAS_CONFIDENCE_HIGH,
    MERCHANT_ALIAS_CONFIDENCES,
    MERCHANT_ALIAS_SOURCE_SYSTEM,
    MERCHANT_ALIAS_SOURCES,
    MERCHANT_DISPLAY_NAME_SOURCE_SYSTEM,
)
from finance_app.database.tables import (
    merchant_aliases as merchant_aliases_table,
    merchants as merchants_table,
)
from finance_app.database.upsert import insert_or_select_unique_row
from finance_app.modules.merchants.normalization import normalize_merchant


def get_or_create_merchant_for_description(conn, description):
    """Return a merchant row for an imported transaction description.

    The raw description is normalized into a canonical merchant key and a
    cleaned alias. The merchant and alias rows are inserted when missing, while
    user-edited display names are preserved on existing merchants.
    """
    normalized = normalize_merchant(description)
    if not normalized.canonical_name:
        return None

    return get_or_create_merchant(
        conn,
        normalized.canonical_name,
        display_name=normalized.canonical_name,
        alias_key=normalized.cleaned_key,
        raw_example=normalized.raw_description,
        source=normalized.normalization_source,
        confidence=normalized.confidence,
    )


def get_or_create_merchant_for_name(conn, merchant_name):
    """Return a merchant row for a canonical or display merchant name.

    This is used by features, such as recurring pattern edits, that receive a
    merchant label instead of a raw bank transaction description.
    """
    existing = find_merchant_by_name(conn, merchant_name)
    if existing:
        return existing

    normalized = normalize_merchant(merchant_name)
    if not normalized.canonical_name:
        return None

    return get_or_create_merchant(
        conn,
        normalized.canonical_name,
        display_name=normalized.canonical_name,
        alias_key=normalized.cleaned_key,
        raw_example=normalized.raw_description,
        source=normalized.normalization_source,
        confidence=normalized.confidence,
    )


def get_or_create_merchant(
    conn,
    canonical_key,
    display_name=None,
    alias_key=None,
    raw_example=None,
    source=MERCHANT_ALIAS_SOURCE_SYSTEM,
    confidence=MERCHANT_ALIAS_CONFIDENCE_HIGH,
):
    """Return a merchant row, inserting the merchant and alias when needed.

    `canonical_key` is the stable machine key. `display_name` is the
    user-facing label and remains user-controlled once `display_name_source` is
    set to `user`. Alias rows map cleaned statement descriptions to the stable
    merchant ID.
    """
    canonical_key = str(canonical_key or "").strip()
    if not canonical_key:
        return None

    display_name = str(display_name or canonical_key).strip() or canonical_key
    source = normalize_alias_source(source)
    confidence = normalize_confidence(confidence)

    existing = find_merchant_by_canonical_key(conn, canonical_key)
    if existing is None:
        existing, inserted = insert_or_select_unique_row(
            conn,
            insert(merchants_table).values(
                canonical_key=canonical_key,
                system_name=canonical_key,
                display_name=display_name,
                display_name_source=MERCHANT_DISPLAY_NAME_SOURCE_SYSTEM,
                active=1,
            ),
            merchant_select_by_canonical_key(canonical_key),
        )
        merchant_id = existing["id"]
    else:
        inserted = False
        merchant_id = existing["id"]

    if not inserted:
        conn.execute(
            update(merchants_table)
            .where(merchants_table.c.id == merchant_id)
            .values(
                system_name=canonical_key,
                display_name=(
                    display_name
                    if existing["display_name_source"] == MERCHANT_DISPLAY_NAME_SOURCE_SYSTEM
                    else existing["display_name"]
                ),
                updated_at=func.current_timestamp(),
            )
        )

    upsert_merchant_alias(
        conn,
        merchant_id,
        canonical_key,
        raw_example=raw_example,
        source=MERCHANT_ALIAS_SOURCE_SYSTEM,
        confidence=confidence,
    )
    if alias_key and alias_key != canonical_key:
        upsert_merchant_alias(
            conn,
            merchant_id,
            alias_key,
            raw_example=raw_example,
            source=source,
            confidence=confidence,
        )

    return find_merchant_by_id(conn, merchant_id)


def upsert_merchant_alias(
    conn,
    merchant_id,
    alias_key,
    raw_example=None,
    source=MERCHANT_ALIAS_SOURCE_SYSTEM,
    confidence=MERCHANT_ALIAS_CONFIDENCE_HIGH,
):
    """Insert or update a cleaned merchant alias.

    Alias keys are globally unique because a cleaned statement key should map to
    one merchant identity. If the normalizer changes its canonical grouping, the
    alias follows the latest canonical merchant.
    """
    alias_key = str(alias_key or "").strip()
    if not alias_key:
        return

    alias_select = select(
        merchant_aliases_table.c.id,
        merchant_aliases_table.c.raw_example,
    ).where(merchant_aliases_table.c.alias_key == alias_key)
    existing = conn.execute(alias_select).mappings().fetchone()
    if existing is None:
        existing, inserted = insert_or_select_unique_row(
            conn,
            insert(merchant_aliases_table).values(
                merchant_id=merchant_id,
                alias_key=alias_key,
                raw_example=raw_example,
                source=normalize_alias_source(source),
                confidence=normalize_confidence(confidence),
            ),
            alias_select,
        )
        if inserted:
            return

    conn.execute(
        update(merchant_aliases_table)
        .where(merchant_aliases_table.c.id == existing["id"])
        .values(
            merchant_id=merchant_id,
            raw_example=existing["raw_example"] or raw_example,
            source=normalize_alias_source(source),
            confidence=normalize_confidence(confidence),
            updated_at=func.current_timestamp(),
        )
    )


def find_merchant_by_id(conn, merchant_id):
    """Return a merchant row by ID."""
    if merchant_id in (None, ""):
        return None

    return conn.execute(
        select(
            merchants_table.c.id,
            merchants_table.c.canonical_key,
            merchants_table.c.system_name,
            merchants_table.c.display_name,
            merchants_table.c.display_name_source,
            merchants_table.c.active,
            merchants_table.c.created_at,
            merchants_table.c.updated_at,
        ).where(merchants_table.c.id == merchant_id)
    ).mappings().fetchone()


def find_merchant_by_canonical_key(conn, canonical_key):
    """Return a merchant row by canonical key."""
    canonical_key = str(canonical_key or "").strip()
    return conn.execute(merchant_select_by_canonical_key(canonical_key)).mappings().fetchone()


def merchant_select_by_canonical_key(canonical_key):
    """Return the shared unique-key select for one merchant canonical key."""
    return select(
        merchants_table.c.id,
        merchants_table.c.canonical_key,
        merchants_table.c.system_name,
        merchants_table.c.display_name,
        merchants_table.c.display_name_source,
        merchants_table.c.active,
        merchants_table.c.created_at,
        merchants_table.c.updated_at,
    ).where(merchants_table.c.canonical_key == canonical_key)


def find_merchant_by_name(conn, merchant_name):
    """Return a merchant row matching a display name, system name, or key."""
    text = str(merchant_name or "").strip()
    if not text:
        return None

    normalized = text.lower()
    return conn.execute(
        select(
            merchants_table.c.id,
            merchants_table.c.canonical_key,
            merchants_table.c.system_name,
            merchants_table.c.display_name,
            merchants_table.c.display_name_source,
            merchants_table.c.active,
            merchants_table.c.created_at,
            merchants_table.c.updated_at,
        ).where(
            or_(
                func.lower(merchants_table.c.display_name) == normalized,
                func.lower(merchants_table.c.system_name) == normalized,
                func.lower(merchants_table.c.canonical_key) == normalized,
            )
        )
    ).mappings().fetchone()


def find_merchant_by_alias(conn, alias_key):
    """Return a merchant and alias row for a cleaned alias key."""
    alias_key = str(alias_key or "").strip()
    if not alias_key:
        return None

    return conn.execute(
        select(
            merchants_table.c.id,
            merchants_table.c.canonical_key,
            merchants_table.c.system_name,
            merchants_table.c.display_name,
            merchants_table.c.display_name_source,
            merchants_table.c.active,
            merchant_aliases_table.c.alias_key,
            merchant_aliases_table.c.source,
            merchant_aliases_table.c.confidence,
        )
        .select_from(
            merchant_aliases_table.join(
                merchants_table,
                merchants_table.c.id == merchant_aliases_table.c.merchant_id,
            )
        )
        .where(merchant_aliases_table.c.alias_key == alias_key)
    ).mappings().fetchone()


def merchant_identity_from_row(row, conn=None):
    """Return merchant identity metadata for a transaction query row.

    Query rows that include `merchant_id` and merchant display columns use the
    durable database identity. Older tests and ad hoc queries that only provide
    `description` fall back to deterministic normalization.
    """
    merchant_id = row_value(row, "merchant_id")
    merchant_name = row_value(row, "merchant_name")
    canonical_key = row_value(row, "merchant_canonical_key")
    if merchant_id and merchant_name:
        return {
            "id": merchant_id,
            "key": merchant_identity_key(merchant_id),
            "name": merchant_name,
            "cleaned_key": canonical_key or merchant_name,
        }

    normalized = normalize_merchant(row_value(row, "description") or "", conn=conn)
    return {
        "id": None,
        "key": normalized.canonical_name,
        "name": normalized.canonical_name,
        "cleaned_key": normalized.cleaned_key,
    }


def merchant_identity_key(merchant_id):
    """Return the stable in-memory grouping key for a merchant ID."""
    return f"merchant:{merchant_id}"


def row_value(row, key, default=None):
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


def normalize_alias_source(source):
    """Return a valid merchant alias source."""
    text = str(source or MERCHANT_ALIAS_SOURCE_SYSTEM).strip().lower()
    return text if text in MERCHANT_ALIAS_SOURCES else MERCHANT_ALIAS_SOURCE_SYSTEM


def normalize_confidence(confidence):
    """Return a valid merchant alias confidence value."""
    text = str(confidence or MERCHANT_ALIAS_CONFIDENCE_HIGH).strip().lower()
    return text if text in MERCHANT_ALIAS_CONFIDENCES else MERCHANT_ALIAS_CONFIDENCE_HIGH
