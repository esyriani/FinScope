"""Tests for merchant identity persistence."""

import pytest

from finance_app.modules.merchants.normalization import normalize_merchant
from finance_app.modules.merchants.repository import (
    get_or_create_merchant,
    get_or_create_merchant_for_description,
)


def test_get_or_create_merchant_reuses_same_deterministic_key(core_conn):
    """Verify equivalent cleaned descriptions map to one merchant key."""
    merchant = get_or_create_merchant_for_description(core_conn, "AMZN Mktp CA*QI44D1DJ3")
    same_merchant = get_or_create_merchant_for_description(core_conn, "AMZN Mktp CA*ZZ999")
    normalized = normalize_merchant("AMZN Mktp CA*1234", conn=core_conn)

    assert same_merchant["id"] == merchant["id"]
    assert same_merchant["merchant_key"] == "AMZN MKTP"
    assert normalized.merchant_key == "AMZN MKTP"


def test_get_or_create_merchant_returns_only_durable_key_fields(core_conn):
    """Verify merchant rows expose only the deterministic merchant identity."""
    merchant = get_or_create_merchant(core_conn, "SQ *COSMETA")

    assert merchant["merchant_key"] == "COSMETA"
    assert "display_name" not in merchant
    assert "canonical_key" not in merchant


def test_get_or_create_merchant_rejects_removed_legacy_fields(core_conn):
    """Verify obsolete merchant-schema fields fail loudly at the repository boundary."""
    with pytest.raises(TypeError):
        get_or_create_merchant(core_conn, "Cosmeta", display_name="Cosmeta")
