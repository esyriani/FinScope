"""Read-side SQL helpers for the upload feature.

These helpers build uploaded-statement and account list read models. They do
not validate upload forms, queue background work, or shape template fields.
"""

from typing import Any

from sqlalchemy import func, select

from finance_app.core.category_sql import transaction_category_label_expression
from finance_app.database.tables import accounts as accounts_table
from finance_app.database.tables import statement_types as statement_types_table
from finance_app.database.tables import statements as statements_table
from finance_app.database.tables import transactions as transactions_table

STATEMENT_TEXT_PREVIEW_CHARS = 2000


def fetch_upload_accounts(conn: Any) -> Any:
    """Return account options shown on the upload page."""
    return (
        conn.execute(
            select(
                accounts_table.c.id,
                accounts_table.c.name,
                accounts_table.c.account_type,
                accounts_table.c.paid_from_account_id,
            ).order_by(func.lower(accounts_table.c.name), accounts_table.c.name)
        )
        .mappings()
        .fetchall()
    )


def count_uploaded_statements(conn: Any) -> int:
    """Return the total number of uploaded statement rows."""
    return conn.execute(select(func.count()).select_from(statements_table)).scalar_one()


def fetch_uploaded_statements(conn: Any, unknown_category: str, page_size: int, offset: int) -> Any:
    """Return uploaded statement rows for the paginated statement list."""
    paid_from_accounts = accounts_table.alias("paid_from_accounts")
    transaction_count = (
        select(func.count())
        .select_from(transactions_table)
        .where(transactions_table.c.statement_id == statements_table.c.id)
        .scalar_subquery()
    )
    unknown_transaction_count = (
        select(func.count())
        .select_from(transactions_table)
        .where(
            transactions_table.c.statement_id == statements_table.c.id,
            transactions_table.c.ignored == 0,
            transaction_category_label_expression(unknown_category) == unknown_category,
        )
        .scalar_subquery()
    )
    statements_join = (
        statements_table.outerjoin(
            accounts_table,
            accounts_table.c.id == statements_table.c.account_id,
        )
        .outerjoin(
            paid_from_accounts,
            paid_from_accounts.c.id == accounts_table.c.paid_from_account_id,
        )
        .outerjoin(
            statement_types_table,
            statement_types_table.c.id == statements_table.c.statement_type_id,
        )
    )
    return (
        conn.execute(
            select(
                statements_table.c.id,
                statements_table.c.filename,
                statements_table.c.extension,
                statements_table.c.interac_direction,
                statements_table.c.date_order,
                statement_types_table.c.name.label("statement_type_name"),
                statement_types_table.c.parser_type,
                statement_types_table.c.import_mode,
                statements_table.c.uploaded_at,
                statements_table.c.import_status,
                statements_table.c.import_error,
                statements_table.c.import_started_at,
                statements_table.c.import_finished_at,
                statements_table.c.imported_count,
                statements_table.c.skipped_count,
                statements_table.c.ignored_count,
                statements_table.c.llm_candidate_count,
                func.substr(
                    func.coalesce(statements_table.c.raw_text, ""),
                    1,
                    STATEMENT_TEXT_PREVIEW_CHARS,
                ).label("raw_text_preview"),
                func.length(func.coalesce(statements_table.c.raw_text, "")).label("raw_text_size"),
                transaction_count.label("transaction_count"),
                unknown_transaction_count.label("unknown_transaction_count"),
                accounts_table.c.name.label("account_name"),
                accounts_table.c.account_type.label("account_type"),
                paid_from_accounts.c.name.label("paid_from_account_name"),
            )
            .select_from(statements_join)
            .order_by(statements_table.c.uploaded_at.desc())
            .limit(page_size)
            .offset(offset)
        )
        .mappings()
        .fetchall()
    )
