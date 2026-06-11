"""Read-side account query helpers.

Provides small SQLAlchemy Core lookups for account selectors shared by
analytics and rule-management pages. Callers pass an active Core connection and
own the surrounding transaction.
"""

from typing import Any

from sqlalchemy import func, select

from finance_app.database.tables import accounts as accounts_table


def list_account_options(conn: Any) -> list[dict[str, Any]]:
    """Return account rows for filter and scope controls ordered by display name."""
    return [
        dict(row)
        for row in conn.execute(
            select(
                accounts_table.c.id,
                accounts_table.c.name,
                accounts_table.c.account_type,
            ).order_by(func.lower(accounts_table.c.name), accounts_table.c.name)
        )
        .mappings()
        .fetchall()
    ]
