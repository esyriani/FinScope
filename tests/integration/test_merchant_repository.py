"""Tests for merchant identity persistence."""

from finance_app.modules.merchants.normalization import normalize_merchant
from finance_app.modules.merchants.repository import (
    get_or_create_merchant,
    get_or_create_merchant_for_description,
)


def test_get_or_create_merchant_reuses_same_deterministic_key(db_conn):
    """Verify equivalent cleaned descriptions map to one merchant key."""
    merchant = get_or_create_merchant_for_description(db_conn, "AMZN Mktp CA*QI44D1DJ3")
    same_merchant = get_or_create_merchant_for_description(db_conn, "AMZN Mktp CA*ZZ999")
    normalized = normalize_merchant("AMZN Mktp CA*1234", conn=db_conn)

    assert same_merchant["id"] == merchant["id"]
    assert same_merchant["merchant_key"] == "AMZN MKTP"
    assert normalized.merchant_key == "AMZN MKTP"


def test_get_or_create_merchant_returns_only_durable_key_fields(db_conn):
    """Verify merchant rows expose only the deterministic merchant identity."""
    merchant = get_or_create_merchant(db_conn, "SQ *COSMETA")

    assert merchant["merchant_key"] == "COSMETA"
    assert "display_name" not in merchant
    assert "canonical_key" not in merchant
