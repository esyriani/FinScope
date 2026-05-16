"""Tests for merchant identity persistence."""

from finance_app.modules.merchants.normalization import normalize_merchant
from finance_app.modules.merchants.repository import (
    find_merchant_by_alias,
    get_or_create_merchant,
    get_or_create_merchant_for_description,
)


def test_get_or_create_merchant_preserves_user_display_name(db_conn):
    """Verify aliases map to a stable merchant while user labels are preserved."""
    merchant = get_or_create_merchant_for_description(db_conn, "AMZN Mktp CA*QI44D1DJ3")
    db_conn.execute(
        """
        UPDATE merchants
        SET display_name = 'Amazon',
            display_name_source = 'user'
        WHERE id = ?
        """,
        (merchant["id"],),
    )

    same_merchant = get_or_create_merchant_for_description(db_conn, "Amazon Mktplace CA*ABCD1234")
    normalized = normalize_merchant("AMZN Mktp CA*ZZ999", conn=db_conn)

    assert same_merchant["id"] == merchant["id"]
    assert same_merchant["display_name"] == "Amazon"
    assert normalized.canonical_name == "Amazon"


def test_alias_collision_moves_alias_to_latest_canonical_merchant(db_conn):
    """Verify a cleaned alias follows the latest canonical merchant mapping."""
    original = get_or_create_merchant(
        db_conn,
        "ORIGINAL MERCHANT",
        display_name="Original Merchant",
        alias_key="SHARED ALIAS",
    )
    replacement = get_or_create_merchant(
        db_conn,
        "REPLACEMENT MERCHANT",
        display_name="Replacement Merchant",
        alias_key="SHARED ALIAS",
    )

    alias = find_merchant_by_alias(db_conn, "SHARED ALIAS")

    assert original["id"] != replacement["id"]
    assert alias["id"] == replacement["id"]
    assert alias["canonical_key"] == "REPLACEMENT MERCHANT"
