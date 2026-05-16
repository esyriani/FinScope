"""Application orchestration for the home feature."""

from datetime import date

from sqlalchemy import case, func, select

from finance_app.core.config import settings
from finance_app.core.constants import NON_REPORTABLE_TRANSACTION_KINDS, UNKNOWN_CATEGORY
from finance_app.core.money import rounded_money_float
from finance_app.database.engine import db_core_transaction
from finance_app.database.tables import (
    accounts as accounts_table,
    statements as statements_table,
    transactions as transactions_table,
)
from finance_app.modules.settings.runtime import get_int_setting, get_unknown_category


def current_year_start():
    """Return the first date of the current local calendar year."""
    return date.today().replace(month=1, day=1)


def build_home_context():
    """Build home context."""
    with db_core_transaction() as conn:
        unknown_category = get_unknown_category(conn) or UNKNOWN_CATEGORY
        top_category_limit = get_int_setting(conn, "home_top_category_limit", settings.default_home_top_category_limit)
        start_date = current_year_start()

        overview = conn.execute(
            select(
                func.count().label("transaction_count"),
                func.coalesce(
                    func.sum(
                        case(
                            (
                                (transactions_table.c.amount > 0)
                                & (transactions_table.c.transaction_kind == "expense"),
                                transactions_table.c.amount,
                            ),
                            else_=0,
                        )
                    ),
                    0,
                ).label("ytd_spending"),
                func.coalesce(
                    func.sum(
                        case(
                            (
                                (transactions_table.c.amount < 0)
                                & (transactions_table.c.transaction_kind == "income"),
                                -transactions_table.c.amount,
                            ),
                            else_=0,
                        )
                    ),
                    0,
                ).label("ytd_income"),
                func.coalesce(
                    func.sum(
                        case(
                            (
                                func.coalesce(transactions_table.c.category, unknown_category) == unknown_category,
                                1,
                            ),
                            else_=0,
                        )
                    ),
                    0,
                ).label("uncategorized_count"),
                func.max(transactions_table.c.tx_date).label("latest_tx_date"),
            )
            .where(
                transactions_table.c.ignored == 0,
                transactions_table.c.transaction_kind.not_in(NON_REPORTABLE_TRANSACTION_KINDS),
                transactions_table.c.tx_date >= start_date,
            )
        ).mappings().fetchone()

        statement_count = conn.execute(
            select(func.count()).select_from(statements_table)
        ).scalar_one()

        latest_statement = conn.execute(
            select(
                statements_table.c.filename,
                statements_table.c.uploaded_at,
                accounts_table.c.name.label("account_name"),
            )
            .select_from(
                statements_table.outerjoin(
                    accounts_table,
                    accounts_table.c.id == statements_table.c.account_id,
                )
            )
            .order_by(statements_table.c.uploaded_at.desc())
            .limit(1)
        ).mappings().fetchone()

        top_categories = conn.execute(
            select(
                func.coalesce(transactions_table.c.category, unknown_category).label("category"),
                func.sum(transactions_table.c.amount).label("total"),
            )
            .where(
                transactions_table.c.amount > 0,
                transactions_table.c.transaction_kind == "expense",
                transactions_table.c.ignored == 0,
                transactions_table.c.tx_date >= start_date,
            )
            .group_by(transactions_table.c.category)
            .order_by(func.sum(transactions_table.c.amount).desc())
            .limit(top_category_limit)
        ).mappings().fetchall()

    ytd_spending = rounded_money_float(overview["ytd_spending"])
    ytd_income = rounded_money_float(overview["ytd_income"])

    return {
        "overview": overview,
        "statement_count": statement_count,
        "latest_statement": latest_statement,
        "top_categories": [
            {
                **dict(row),
                "total": rounded_money_float(row["total"]),
            }
            for row in top_categories
        ],
        "unknown_category": unknown_category,
        "ytd_spending": ytd_spending,
        "ytd_income": ytd_income,
        "ytd_cashflow": rounded_money_float(ytd_income - ytd_spending),
    }
