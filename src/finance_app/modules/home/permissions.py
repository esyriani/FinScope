"""Request-aware permission read model for Home actions.

The service layer uses this helper to keep authorization-sensitive Home links
out of SQL query and presentation helpers.
"""

from typing import Any

from flask import has_request_context

from finance_app.modules.auth.permissions import (
    PERMISSION_EDIT_TRANSACTIONS,
    PERMISSION_IMPORT_STATEMENTS,
    PERMISSION_MANAGE_JOBS,
    PERMISSION_MANAGE_RULES,
    current_user_can,
)


def home_permissions() -> Any:
    """Return current-user permissions that affect Home links and actions."""
    if not has_request_context():
        return {
            "can_edit_transactions": True,
            "can_import_statements": True,
            "can_manage_jobs": True,
            "can_manage_rules": True,
        }
    return {
        "can_edit_transactions": current_user_can(PERMISSION_EDIT_TRANSACTIONS),
        "can_import_statements": current_user_can(PERMISSION_IMPORT_STATEMENTS),
        "can_manage_jobs": current_user_can(PERMISSION_MANAGE_JOBS),
        "can_manage_rules": current_user_can(PERMISSION_MANAGE_RULES),
    }
