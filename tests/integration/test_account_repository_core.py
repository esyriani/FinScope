"""Tests for SQLAlchemy Core account repository helpers."""

import pytest

from finance_app.database.engine import db_core_transaction
from finance_app.modules.accounts.repository import get_or_create_account, normalize_account_type


def test_normalize_account_type_defaults_to_checking():
    """Return checking for blank or unsupported account type values."""
    assert normalize_account_type("") == "checking"
    assert normalize_account_type("not-real") == "checking"
    assert normalize_account_type("credit_card") == "credit_card"


def test_get_or_create_account_links_credit_card_to_funding_account(app):
    """Create account rows and preserve the funding-account relationship."""
    del app
    with db_core_transaction() as conn:
        credit_card = get_or_create_account(
            conn,
            "Travel card",
            account_type="credit_card",
            paid_from_account_name="Main checking",
        )

        assert credit_card["account_type"] == "credit_card"
        assert credit_card["paid_from_account_id"] is not None

        funding_account = get_or_create_account(conn, "Main checking")
        assert funding_account["account_type"] == "checking"
        assert credit_card["paid_from_account_id"] == funding_account["id"]


def test_get_or_create_account_uses_database_name_key(app):
    """Match existing accounts by name key without overwriting metadata."""
    del app
    with db_core_transaction() as conn:
        original = get_or_create_account(conn, "Travel card", account_type="credit_card")
        matched = get_or_create_account(conn, " travel CARD ", account_type="checking")

        assert matched["id"] == original["id"]
        assert matched["name"] == "Travel card"
        assert matched["account_type"] == "credit_card"


def test_get_or_create_account_does_not_overwrite_funding_account(app):
    """Return existing card metadata instead of changing the funding account."""
    del app
    with db_core_transaction() as conn:
        original = get_or_create_account(
            conn,
            "Travel card",
            account_type="credit_card",
            paid_from_account_name="Main checking",
        )
        matched = get_or_create_account(
            conn,
            " travel CARD ",
            account_type="credit_card",
            paid_from_account_name="Other checking",
        )

        other_account = get_or_create_account(conn, "Other checking")

        assert matched["id"] == original["id"]
        assert matched["paid_from_account_id"] == original["paid_from_account_id"]
        assert matched["paid_from_account_id"] != other_account["id"]


def test_get_or_create_account_requires_core_connection(core_conn):
    """Reject non-Core objects at the account repository boundary."""
    del core_conn
    with pytest.raises(TypeError):
        get_or_create_account(object(), "Not Core")
