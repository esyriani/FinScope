"""Pure boundary tests for login lockout expiry and failed-attempt counting."""

from datetime import datetime, timedelta, timezone

import pytest

from finance_app.modules.auth.service import failed_count_after_attempt, login_lock_active


@pytest.mark.parametrize(
    ("seconds_after_expiry", "locked", "failed_count"),
    [(-1, True, 6), (0, False, 1), (1, False, 1)],
)
def test_lockout_expiry_resets_failure_count_at_the_deadline(seconds_after_expiry, locked, failed_count):
    """The lock expires inclusively; the next failure starts a new attempt sequence."""
    deadline = datetime(2026, 10, 16, 12)
    row = {"locked_until": deadline, "failed_login_count": 5}
    now = deadline.replace(tzinfo=timezone.utc) + timedelta(seconds=seconds_after_expiry)

    assert login_lock_active(row, now) is locked
    assert failed_count_after_attempt(row, now) == failed_count
