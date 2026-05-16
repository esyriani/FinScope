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


def test_get_or_create_account_requires_core_connection(db_conn):
    """Reject non-Core adapters at the account repository boundary."""
    with pytest.raises(TypeError):
        get_or_create_account(db_conn, "Legacy")
