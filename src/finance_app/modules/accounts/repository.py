"""Account persistence helpers.

Provides SQLAlchemy Core helpers for account records used by statement upload
and account-aware reporting. Callers manage database transactions and pass
Core connections bound to the application metadata.
"""

from typing import Any

from sqlalchemy import insert, select, update
from sqlalchemy.engine import Connection as CoreConnection

from finance_app.core.constants import ACCOUNT_TYPE_CHECKING, ACCOUNT_TYPES
from finance_app.database.tables import accounts as accounts_table
from finance_app.database.tables import normalize_name_key
from finance_app.database.upsert import insert_or_select_unique_row


def require_core_connection(conn: object) -> None:
    """Validate that an account repository caller passed a Core connection."""
    if not isinstance(conn, CoreConnection):
        raise TypeError("Account repository helpers require a SQLAlchemy Core connection.")


def normalize_account_type(value: object) -> str:
    """Return a supported account role, falling back to checking."""
    text = str(value or "").strip()
    return text if text in ACCOUNT_TYPES else ACCOUNT_TYPE_CHECKING


def get_or_create_account(
    conn: CoreConnection,
    name: object,
    account_type: object = ACCOUNT_TYPE_CHECKING,
    paid_from_account_name: object | None = None,
) -> Any:
    """Return an account row, creating or updating role metadata as needed.

    Args:
        conn: Open SQLAlchemy Core connection. The caller owns transaction
            commit or rollback.
        name: Account display name. Blank values become ``Personal``.
        account_type: Desired persisted account role.
        paid_from_account_name: Optional funding account name for credit cards.

    Returns:
        A mapping row with ``id``, ``name``, ``account_type``, and
        ``paid_from_account_id``.
    """
    require_core_connection(conn)

    account_name = str(name or "").strip() or "Personal"
    account_key = normalize_name_key(account_name)
    normalized_account_type = normalize_account_type(account_type)
    paid_from_id = None
    update_paid_from = paid_from_account_name is not None
    paid_from_name = str(paid_from_account_name or "").strip() if update_paid_from else ""

    if paid_from_name and normalize_name_key(paid_from_name) != account_key:
        paid_from_account = get_or_create_account(
            conn,
            paid_from_name,
            account_type=ACCOUNT_TYPE_CHECKING,
        )
        paid_from_id = paid_from_account["id"]

    account_id_select = select(accounts_table.c.id).where(accounts_table.c.name_key == account_key)
    existing = conn.execute(account_id_select).mappings().fetchone()
    if existing is None:
        insert_or_select_unique_row(
            conn,
            insert(accounts_table).values(
                name=account_name,
                account_type=normalized_account_type,
                paid_from_account_id=paid_from_id,
            ),
            account_id_select,
        )

    values: dict[str, object] = {"account_type": normalized_account_type}
    if update_paid_from:
        values["paid_from_account_id"] = paid_from_id

    conn.execute(update(accounts_table).where(accounts_table.c.name_key == account_key).values(**values))
    return (
        conn.execute(
            select(
                accounts_table.c.id,
                accounts_table.c.name,
                accounts_table.c.account_type,
                accounts_table.c.paid_from_account_id,
            ).where(accounts_table.c.name_key == account_key)
        )
        .mappings()
        .fetchone()
    )
