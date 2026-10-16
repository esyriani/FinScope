"""No-write guards for transaction approval and reopening persistence.

Empty or invalid selections must return accurate counts without executing SQL.
Database-backed behavior remains covered by transaction route tests.
"""

from unittest.mock import Mock

import pytest

from finance_app.modules.transactions.repository import mark_transactions_for_review, mark_transactions_verified


@pytest.mark.parametrize("mutator", [mark_transactions_verified, mark_transactions_for_review])
@pytest.mark.parametrize("ids", [[], ["bad", None, 0, -1]])
def test_empty_review_selection_returns_zero_without_database_writes(mutator, ids):
    """Neither bulk review operation may claim updates for an empty normalized selection."""
    conn = Mock()

    assert mutator(conn, ids) == 0

    conn.execute.assert_not_called()
