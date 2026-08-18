"""Shared context service test data builders."""

from datetime import date as real_date
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import select, text

from finance_app.core.constants import ACCOUNT_TYPE_CREDIT_CARD, TRANSACTION_KIND_EXPENSE, TRANSACTION_KIND_INCOME
from finance_app.database.tables import merchants as merchants_table
from finance_app.database.tables import transactions as transactions_table
from finance_app.modules.categories.repository import create_category, resolve_category_id
from finance_app.modules.categories.taxonomy import set_transaction_tags


class FixedDate(real_date):
    """Fixed replacement for date.today in comparison service tests."""

    @classmethod
    def today(cls):
        """Return a deterministic current date."""
        return cls(2026, 5, 9)


def transaction_seed_params(conn, rows, columns):
    """Return transaction seed parameter dictionaries with canonical category IDs."""
    params = []
    for row in rows:
        values = dict(zip(columns, row))
        create_category(conn, values["category"])
        values["category_id"] = resolve_category_id(conn, values["category"])
        params.append(values)
    return params


def seed_reporting_data(conn):
    """Seed realistic accounts, statements, and transactions for context tests."""
    account_id = conn.execute(text("""
        INSERT INTO accounts (name)
        VALUES ('Personal Checking')
        """)).lastrowid
    statement_type_id = conn.execute(text("""
        SELECT id
        FROM statement_types
        WHERE parser_type = 'bank_account'
        LIMIT 1
        """)).fetchone()._mapping["id"]
    conn.execute(
        text("""
        INSERT INTO statements (account_id, statement_type_id, filename, checksum, raw_text, uploaded_at)
        VALUES (:p0, :p1, 'latest.csv', 'latest-checksum', 'raw', '2026-05-01T12:00:00Z')
        """),
        {"p0": account_id, "p1": statement_type_id},
    )
    rows = [
        ("2025-01-05", "Metro Grocery", 80.00, "Food", 0, "rule", 0, "expense", "seed-2025-food"),
        ("2025-02-10", "Hydro Quebec", 90.00, "Utilities", 0, "rule", 0, "expense", "seed-2025-utilities"),
        ("2025-05-02", "Metro Grocery", 60.00, "Food", 0, "rule", 0, "expense", "seed-2025-may-food"),
        ("2025-05-03", "Unknown Shop", 30.00, "UNKNOWN", 1, "unknown", 0, "expense", "seed-2025-unknown"),
        ("2025-05-04", "Payroll", -900.00, "Income", 0, "rule", 0, "income", "seed-2025-income"),
        ("2026-01-05", "Metro Grocery", 100.00, "Food", 0, "rule", 0, "expense", "seed-2026-food-jan"),
        ("2026-01-06", "Cafe Bistro", 40.00, "Food", 0, "manual", 0, "expense", "seed-2026-food-cafe"),
        ("2026-01-07", "Payroll", -1000.00, "Income", 0, "rule", 0, "income", "seed-2026-income-jan"),
        ("2026-02-10", "Hydro Quebec", 120.00, "Utilities", 0, "rule", 0, "expense", "seed-2026-utilities"),
        ("2026-02-11", "Unknown Shop", 30.00, "UNKNOWN", 1, "unknown", 0, "expense", "seed-2026-unknown"),
        ("2026-05-02", "Metro Grocery", 200.00, "Food", 0, "rule", 0, "expense", "seed-2026-may-food"),
        ("2026-05-03", "Unknown Shop", 50.00, "UNKNOWN", 1, "unknown", 0, "expense", "seed-2026-may-unknown"),
        ("2026-05-04", "Payroll", -1200.00, "Income", 0, "rule", 0, "income", "seed-2026-may-income"),
        ("2026-02-12", "Ignored Store", 999.00, "Food", 0, "rule", 1, "expense", "seed-ignored"),
    ]
    conn.execute(
        text("""
        INSERT INTO transactions (
            tx_date,
            description,
            amount,
            category,
            category_id,
            needs_review,
            category_source,
            ignored,
            transaction_kind,
            fingerprint
        )
        VALUES (
            :tx_date,
            :description,
            :amount,
            :category,
            :category_id,
            :needs_review,
            :category_source,
            :ignored,
            :transaction_kind,
            :fingerprint
        )
        """),
        transaction_seed_params(
            conn,
            rows,
            (
                "tx_date",
                "description",
                "amount",
                "category",
                "needs_review",
                "category_source",
                "ignored",
                "transaction_kind",
                "fingerprint",
            ),
        ),
    )
    tag_assignments = (
        ("seed-2025-may-food", ["Tax"]),
        ("seed-2026-food-jan", ["Tax"]),
        ("seed-2026-food-cafe", ["Shared"]),
        ("seed-2026-utilities", ["Government"]),
        ("seed-2026-may-food", ["Tax"]),
    )
    for fingerprint, tags in tag_assignments:
        transaction_id = (
            conn.execute(
                text("""
            SELECT id
            FROM transactions
            WHERE fingerprint = :p0
            """),
                {"p0": fingerprint},
            )
            .fetchone()
            ._mapping["id"]
        )
        set_transaction_tags(conn, transaction_id, tags, source="rule")
    conn.execute(
        text("""
        INSERT INTO transactions (
            tx_date,
            description,
            amount,
            category,
            category_id,
            needs_review,
            category_source,
            ignored,
            transaction_kind,
            fingerprint
        )
        VALUES (
            :tx_date,
            :description,
            :amount,
            :category,
            :category_id,
            :needs_review,
            :category_source,
            :ignored,
            :transaction_kind,
            :fingerprint
        )
        """),
        transaction_seed_params(
            conn,
            [
                (
                    "2026-01-08",
                    "Savings transfer",
                    500.00,
                    "Transfers",
                    0,
                    "rule",
                    0,
                    "transfer",
                    "seed-2026-transfer-jan",
                ),
                (
                    "2026-05-05",
                    "Card payment",
                    700.00,
                    "Transfers",
                    0,
                    "rule",
                    0,
                    "payment",
                    "seed-2026-transfer-may",
                ),
            ],
            (
                "tx_date",
                "description",
                "amount",
                "category",
                "needs_review",
                "category_source",
                "ignored",
                "transaction_kind",
                "fingerprint",
            ),
        ),
    )
    conn.execute(text("""
        UPDATE user_settings
        SET value = '2'
        WHERE key = 'home_top_category_limit'
          AND user_id = (SELECT id FROM users WHERE username = 'owner')
        """))
    conn.execute(text("""
        UPDATE user_settings
        SET value = '2'
        WHERE key = 'merchant_table_limit'
          AND user_id = (SELECT id FROM users WHERE username = 'owner')
        """))
    conn.commit()


def seed_entity_report_data(data_factory, conn):
    """Seed linked account and merchant rows for Reports entity tests."""
    checking_id = data_factory.accounts.create(name="Personal Checking")
    card_id = data_factory.accounts.create(
        name="Travel Card",
        account_type=ACCOUNT_TYPE_CREDIT_CARD,
    )
    metro_tx_id = data_factory.transactions.create(
        description="Metro Grocery",
        amount=Decimal("100.00"),
        tx_date="2026-01-05",
        category="Food",
        account_id=checking_id,
        merchant_from_description=True,
        needs_review=0,
        transaction_kind=TRANSACTION_KIND_EXPENSE,
        tags=["Tax"],
    )
    cafe_tx_id = data_factory.transactions.create(
        description="Cafe Bistro",
        amount=Decimal("40.00"),
        tx_date="2026-01-06",
        category="Food",
        account_id=checking_id,
        merchant_from_description=True,
        needs_review=0,
        transaction_kind=TRANSACTION_KIND_EXPENSE,
        tags=["Shared"],
    )
    payroll_tx_id = data_factory.transactions.create(
        description="Payroll",
        amount=Decimal("-1000.00"),
        tx_date="2026-01-07",
        category="Income",
        account_id=checking_id,
        merchant_from_description=True,
        needs_review=0,
        transaction_kind=TRANSACTION_KIND_INCOME,
    )
    hotel_tx_id = data_factory.transactions.create(
        description="Hotel Stay",
        amount=Decimal("200.00"),
        tx_date="2026-01-08",
        category="Travel",
        account_id=card_id,
        merchant_from_description=True,
        needs_review=0,
        transaction_kind=TRANSACTION_KIND_EXPENSE,
    )
    metro_merchant = merchant_target_for_transaction(conn, metro_tx_id)
    cafe_merchant = merchant_target_for_transaction(conn, cafe_tx_id)
    payroll_merchant = merchant_target_for_transaction(conn, payroll_tx_id)
    hotel_merchant = merchant_target_for_transaction(conn, hotel_tx_id)
    return {
        "checking_id": checking_id,
        "card_id": card_id,
        "metro_merchant_id": metro_merchant["id"],
        "metro_merchant_name": metro_merchant["name"],
        "cafe_merchant_id": cafe_merchant["id"],
        "cafe_merchant_name": cafe_merchant["name"],
        "payroll_merchant_id": payroll_merchant["id"],
        "payroll_merchant_name": payroll_merchant["name"],
        "hotel_merchant_id": hotel_merchant["id"],
        "hotel_merchant_name": hotel_merchant["name"],
    }


def merchant_target_for_transaction(conn, transaction_id):
    """Return merchant id and key for a seeded transaction."""
    row = (
        conn.execute(
            select(
                transactions_table.c.merchant_id,
                merchants_table.c.merchant_key,
            )
            .select_from(
                transactions_table.join(
                    merchants_table,
                    merchants_table.c.id == transactions_table.c.merchant_id,
                )
            )
            .where(transactions_table.c.id == transaction_id)
        )
        .mappings()
        .one()
    )
    return {"id": int(row["merchant_id"]), "name": str(row["merchant_key"])}


def category_totals(context):
    """Return dashboard category totals by category label."""
    return {row["category"]: row["total"] for row in context["category_rows"]}


def merchant_totals(context):
    """Return dashboard merchant totals by merchant label."""
    return {row["merchant"]: row["total"] for row in context["merchant_rows"]}


def seed_dashboard_spending_only(conn):
    """Seed a dashboard range with spending but no income."""
    conn.execute(
        text("""
        INSERT INTO transactions (tx_date, description, amount, category, category_id, category_source, fingerprint)
        VALUES ('2026-06-01', 'Coffee Stand', 25.00, 'Food', :category_id, 'rule', 'dashboard-spending-only')
        """),
        {"category_id": resolve_category_id(conn, "Food")},
    )
    conn.commit()


def seed_dashboard_unknown_only(conn):
    """Seed a dashboard range where all transactions are UNKNOWN."""
    rows = [
        ("2026-07-01", "Unknown Shop", 20.00, "dashboard-unknown-1"),
        ("2026-07-02", "Unknown Cafe", 40.00, "dashboard-unknown-2"),
    ]
    conn.execute(
        text("""
        INSERT INTO transactions (
            tx_date,
            description,
            amount,
            category,
            category_id,
            needs_review,
            category_source,
            fingerprint
        )
        VALUES (:tx_date, :description, :amount, 'UNKNOWN', :category_id, 1, 'unknown', :fingerprint)
        """),
        transaction_seed_params(
            conn,
            [
                (tx_date, description, amount, "UNKNOWN", fingerprint)
                for tx_date, description, amount, fingerprint in rows
            ],
            ("tx_date", "description", "amount", "category", "fingerprint"),
        ),
    )
    conn.commit()


def seed_dashboard_review_queue_data(conn):
    """Seed dashboard data with both UNKNOWN and categorized review candidates."""
    rows = [
        ("2026-08-01", "Unknown Market", 20.00, "UNKNOWN", 1, "unknown", "dashboard-review-unknown"),
        ("2026-08-02", "Low Confidence Cafe", 30.00, "Food", 1, "ai", "dashboard-review-food"),
        ("2026-08-03", "Approved Grocery", 40.00, "Food", 0, "rule", "dashboard-review-approved"),
    ]
    conn.execute(
        text("""
        INSERT INTO transactions (
            tx_date,
            description,
            amount,
            category,
            category_id,
            needs_review,
            category_source,
            fingerprint
        )
        VALUES (:tx_date, :description, :amount, :category, :category_id, :needs_review, :category_source, :fingerprint)
        """),
        transaction_seed_params(
            conn,
            rows,
            ("tx_date", "description", "amount", "category", "needs_review", "category_source", "fingerprint"),
        ),
    )
    conn.commit()


def seed_reimbursable_dashboard_data(conn):
    """Seed tagged expenses and reimbursement credits for dashboard cash flow."""
    rows = [
        ("2026-03-05", "Work hotel", 300.00, "Travel", "expense", "dashboard-reimbursable-expense"),
        (
            "2026-03-20",
            "Employer reimbursement",
            -250.00,
            "Transfers",
            "transfer",
            "dashboard-reimbursable-credit",
        ),
    ]
    conn.execute(
        text("""
        INSERT INTO transactions (
            tx_date,
            description,
            amount,
            category,
            category_id,
            transaction_kind,
            needs_review,
            category_source,
            ignored,
            fingerprint
        )
        VALUES (
            :tx_date,
            :description,
            :amount,
            :category,
            :category_id,
            :transaction_kind,
            0,
            'manual',
            0,
            :fingerprint
        )
        """),
        transaction_seed_params(
            conn,
            rows,
            ("tx_date", "description", "amount", "category", "transaction_kind", "fingerprint"),
        ),
    )
    for fingerprint in ("dashboard-reimbursable-expense", "dashboard-reimbursable-credit"):
        transaction_id = (
            conn.execute(
                text("""
            SELECT id
            FROM transactions
            WHERE fingerprint = :p0
            """),
                {"p0": fingerprint},
            )
            .fetchone()
            ._mapping["id"]
        )
        set_transaction_tags(conn, transaction_id, ["Reimbursable"], source="manual")
    conn.commit()


def seed_reimbursable_comparison_data(conn):
    """Seed period-comparison data with reimbursed transfer credits."""
    rows = [
        ("2025-05-06", "Prior work hotel", 100.00, "Travel", "expense", "comparison-reimbursable-prior-expense"),
        (
            "2025-05-07",
            "Prior employer reimbursement",
            -80.00,
            "Transfers",
            "transfer",
            "comparison-reimbursable-prior-credit",
        ),
        ("2026-05-06", "Current work hotel", 200.00, "Travel", "expense", "comparison-reimbursable-current-expense"),
        (
            "2026-05-07",
            "Current employer reimbursement",
            -150.00,
            "Transfers",
            "transfer",
            "comparison-reimbursable-current-credit",
        ),
    ]
    conn.execute(
        text("""
        INSERT INTO transactions (
            tx_date,
            description,
            amount,
            category,
            category_id,
            transaction_kind,
            needs_review,
            category_source,
            ignored,
            fingerprint
        )
        VALUES (
            :tx_date,
            :description,
            :amount,
            :category,
            :category_id,
            :transaction_kind,
            0,
            'manual',
            0,
            :fingerprint
        )
        """),
        transaction_seed_params(
            conn,
            rows,
            ("tx_date", "description", "amount", "category", "transaction_kind", "fingerprint"),
        ),
    )
    for fingerprint in (
        "comparison-reimbursable-prior-expense",
        "comparison-reimbursable-prior-credit",
        "comparison-reimbursable-current-expense",
        "comparison-reimbursable-current-credit",
    ):
        transaction_id = (
            conn.execute(
                text("""
            SELECT id
            FROM transactions
            WHERE fingerprint = :p0
            """),
                {"p0": fingerprint},
            )
            .fetchone()
            ._mapping["id"]
        )
        set_transaction_tags(conn, transaction_id, ["Reimbursable"], source="manual")
    conn.commit()


def seed_dashboard_period_delta_data(conn):
    """Seed current and previous rolling-month merchant spending."""
    current_date = (real_date.today() - timedelta(days=10)).isoformat()
    previous_date = (real_date.today() - timedelta(days=45)).isoformat()
    rows = [
        (current_date, "Metro Grocery", 150.00, "Food", "dashboard-delta-current"),
        (current_date, "New Bakery", 60.00, "Food", "dashboard-delta-new"),
        (previous_date, "Metro Grocery", 100.00, "Food", "dashboard-delta-previous"),
    ]
    conn.execute(
        text("""
        INSERT INTO transactions (tx_date, description, amount, category, category_id, category_source, fingerprint)
        VALUES (:tx_date, :description, :amount, :category, :category_id, 'rule', :fingerprint)
        """),
        transaction_seed_params(conn, rows, ("tx_date", "description", "amount", "category", "fingerprint")),
    )
    conn.commit()


def seed_comparison_unknown_warning_data(conn):
    """Seed comparison periods where UNKNOWN exceeds warning thresholds."""
    rows = [
        ("2026-04-02", "Unknown Prior", 40.00, "UNKNOWN", "comparison-unknown-prior"),
        ("2026-04-03", "Prior Grocery", 60.00, "Food", "comparison-food-prior"),
        ("2026-05-02", "Unknown Current", 70.00, "UNKNOWN", "comparison-unknown-current"),
        ("2026-05-03", "Current Grocery", 30.00, "Food", "comparison-food-current"),
    ]
    conn.execute(
        text("""
        INSERT INTO transactions (tx_date, description, amount, category, category_id, category_source, fingerprint)
        VALUES (:tx_date, :description, :amount, :category, :category_id, 'rule', :fingerprint)
        """),
        transaction_seed_params(conn, rows, ("tx_date", "description", "amount", "category", "fingerprint")),
    )
    conn.commit()
