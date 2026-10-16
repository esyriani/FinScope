"""Authentication service rejection and transaction invariants.

Exercise the real Core repositories to ensure failed ownership and password
changes preserve persisted access and audit state.
"""

import pytest
from sqlalchemy import select

from finance_app.database.tables import audit_log, users
from finance_app.modules.auth import service


@pytest.mark.parametrize("scenario", ["non-owner", "self", "inactive-target"])
def test_invalid_ownership_handoff_preserves_users_and_audit(core_conn, data_factory, scenario):
    """Reject invalid handoffs without changing roles, active flags or audit history."""
    owner = core_conn.execute(select(users.c.id).where(users.c.role == "owner")).scalar_one()
    target = data_factory.user(is_active=scenario != "inactive-target")
    actor = target if scenario == "non-owner" else owner
    recipient = owner if scenario == "self" else target
    before_users = core_conn.execute(select(users).order_by(users.c.id)).mappings().all()
    before_audit = core_conn.execute(select(audit_log)).mappings().all()

    with pytest.raises(
        ValueError,
        match={
            "non-owner": "Only the active owner",
            "self": "Choose another active user",
            "inactive-target": "active user",
        }[scenario],
    ):
        service.hand_off_ownership(actor, recipient)

    assert core_conn.execute(select(users).order_by(users.c.id)).mappings().all() == before_users
    assert core_conn.execute(select(audit_log)).mappings().all() == before_audit


def test_incorrect_current_password_preserves_credentials_and_audit(core_conn):
    """A valid new password does not bypass verification of the current password."""
    owner = core_conn.execute(select(users).where(users.c.role == "owner")).mappings().one()
    before_audit = core_conn.execute(select(audit_log)).mappings().all()

    with pytest.raises(ValueError, match="Current password is incorrect"):
        service.change_password(owner["id"], "incorrect", "DifferentPass123!", "DifferentPass123!")

    assert core_conn.execute(select(users).where(users.c.id == owner["id"])).mappings().one() == owner
    assert core_conn.execute(select(audit_log)).mappings().all() == before_audit
