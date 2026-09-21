"""Integration tests for shared financial reporting SQL expressions."""

from decimal import Decimal

import pytest
from sqlalchemy import select, update

from finance_app.core.constants import (
    REIMBURSEMENT_CATEGORY,
    TRANSACTION_KIND_EXPENSE,
    TRANSACTION_KIND_INCOME,
    TRANSACTION_KIND_REFUND,
    TRANSACTION_KIND_TRANSFER,
)
from finance_app.core.money import money_to_decimal
from finance_app.core.reporting import (
    cashflow_amount_expression,
    income_amount_expression,
    income_or_tagged_transfer_credit_clause,
    reimbursement_credit_clause,
    reportable_or_tagged_transfer_credit_clause,
    reportable_transaction_clause,
    spending_impact_amount_expression,
    spending_impact_clause,
    tagged_transfer_credit_clause,
)
from finance_app.database.tables import transactions as transactions_table
from finance_app.modules.reimbursements.service import create_reimbursement_allocation


def expression_snapshot(conn, transaction_id):
    """Return boolean reporting predicates and spending impact for one row."""
    row = (
        conn.execute(
            select(
                reportable_transaction_clause().label("reportable"),
                reportable_or_tagged_transfer_credit_clause().label("reportable_default"),
                reportable_or_tagged_transfer_credit_clause(True).label("reportable_with_transfer"),
                income_or_tagged_transfer_credit_clause().label("income_default"),
                income_or_tagged_transfer_credit_clause(True).label("income_with_transfer"),
                reimbursement_credit_clause().label("reimbursement_credit"),
                spending_impact_clause().label("spending_impact"),
                spending_impact_amount_expression().label("spending_amount"),
                tagged_transfer_credit_clause().label("tagged_transfer_credit"),
            )
            .select_from(transactions_table)
            .where(transactions_table.c.id == transaction_id)
        )
        .mappings()
        .one()
    )
    snapshot = dict(row)
    for key in (
        "reportable",
        "reportable_default",
        "reportable_with_transfer",
        "income_default",
        "income_with_transfer",
        "reimbursement_credit",
        "spending_impact",
        "tagged_transfer_credit",
    ):
        snapshot[key] = bool(snapshot[key])
    snapshot["spending_amount"] = money_to_decimal(snapshot["spending_amount"])
    return snapshot


@pytest.mark.parametrize(
    ("description", "amount", "category", "transaction_kind", "legacy_category_label", "expected"),
    [
        (
            "ordinary expense",
            "100.00",
            "Food",
            TRANSACTION_KIND_EXPENSE,
            False,
            {
                "reportable": True,
                "reportable_default": True,
                "reportable_with_transfer": True,
                "income_default": False,
                "income_with_transfer": False,
                "reimbursement_credit": False,
                "spending_impact": True,
                "spending_amount": Decimal("100.00"),
                "tagged_transfer_credit": False,
            },
        ),
        (
            "zero expense",
            "0.00",
            "Food",
            TRANSACTION_KIND_EXPENSE,
            False,
            {
                "reportable": True,
                "reportable_default": True,
                "reportable_with_transfer": True,
                "income_default": False,
                "income_with_transfer": False,
                "reimbursement_credit": False,
                "spending_impact": False,
                "spending_amount": Decimal("0.00"),
                "tagged_transfer_credit": False,
            },
        ),
        (
            "ordinary refund",
            "-20.00",
            "Food",
            TRANSACTION_KIND_REFUND,
            False,
            {
                "reportable": True,
                "reportable_default": True,
                "reportable_with_transfer": True,
                "income_default": False,
                "income_with_transfer": False,
                "reimbursement_credit": False,
                "spending_impact": True,
                "spending_amount": Decimal("-20.00"),
                "tagged_transfer_credit": False,
            },
        ),
        (
            "zero refund",
            "0.00",
            "Food",
            TRANSACTION_KIND_REFUND,
            False,
            {
                "reportable": True,
                "reportable_default": True,
                "reportable_with_transfer": True,
                "income_default": False,
                "income_with_transfer": False,
                "reimbursement_credit": False,
                "spending_impact": False,
                "spending_amount": Decimal("0.00"),
                "tagged_transfer_credit": False,
            },
        ),
        (
            "ordinary income",
            "-100.00",
            "Income",
            TRANSACTION_KIND_INCOME,
            False,
            {
                "reportable": True,
                "reportable_default": True,
                "reportable_with_transfer": True,
                "income_default": True,
                "income_with_transfer": True,
                "reimbursement_credit": False,
                "spending_impact": False,
                "spending_amount": Decimal("0.00"),
                "tagged_transfer_credit": False,
            },
        ),
        (
            "reimbursement credit",
            "-50.00",
            REIMBURSEMENT_CATEGORY,
            TRANSACTION_KIND_INCOME,
            False,
            {
                "reportable": False,
                "reportable_default": False,
                "reportable_with_transfer": False,
                "income_default": False,
                "income_with_transfer": False,
                "reimbursement_credit": True,
                "spending_impact": False,
                "spending_amount": Decimal("0.00"),
                "tagged_transfer_credit": False,
            },
        ),
        (
            "zero reimbursement",
            "0.00",
            REIMBURSEMENT_CATEGORY,
            TRANSACTION_KIND_INCOME,
            False,
            {
                "reportable": True,
                "reportable_default": True,
                "reportable_with_transfer": True,
                "income_default": True,
                "income_with_transfer": True,
                "reimbursement_credit": False,
                "spending_impact": False,
                "spending_amount": Decimal("0.00"),
                "tagged_transfer_credit": False,
            },
        ),
        (
            "legacy reimbursement label without category id",
            "-50.00",
            REIMBURSEMENT_CATEGORY,
            TRANSACTION_KIND_INCOME,
            True,
            {
                "reportable": True,
                "reportable_default": True,
                "reportable_with_transfer": True,
                "income_default": True,
                "income_with_transfer": True,
                "reimbursement_credit": False,
                "spending_impact": False,
                "spending_amount": Decimal("0.00"),
                "tagged_transfer_credit": False,
            },
        ),
        (
            "tagged transfer credit",
            "-30.00",
            "Transfers",
            TRANSACTION_KIND_TRANSFER,
            False,
            {
                "reportable": False,
                "reportable_default": False,
                "reportable_with_transfer": True,
                "income_default": False,
                "income_with_transfer": True,
                "reimbursement_credit": False,
                "spending_impact": False,
                "spending_amount": Decimal("0.00"),
                "tagged_transfer_credit": True,
            },
        ),
        (
            "transfer debit",
            "30.00",
            "Transfers",
            TRANSACTION_KIND_TRANSFER,
            False,
            {
                "reportable": False,
                "reportable_default": False,
                "reportable_with_transfer": False,
                "income_default": False,
                "income_with_transfer": False,
                "reimbursement_credit": False,
                "spending_impact": False,
                "spending_amount": Decimal("0.00"),
                "tagged_transfer_credit": False,
            },
        ),
        (
            "zero transfer",
            "0.00",
            "Transfers",
            TRANSACTION_KIND_TRANSFER,
            False,
            {
                "reportable": False,
                "reportable_default": False,
                "reportable_with_transfer": False,
                "income_default": False,
                "income_with_transfer": False,
                "reimbursement_credit": False,
                "spending_impact": False,
                "spending_amount": Decimal("0.00"),
                "tagged_transfer_credit": False,
            },
        ),
    ],
)
def test_reporting_expressions_preserve_financial_classification_boundaries(
    core_conn,
    data_factory,
    description,
    amount,
    category,
    transaction_kind,
    legacy_category_label,
    expected,
):
    """Verify reporting predicates classify signs, kinds, and legacy category labels."""
    transaction_id = data_factory.transactions.create(
        description=description,
        amount=Decimal(amount),
        category=category,
        transaction_kind=transaction_kind,
        needs_review=0,
    )
    if legacy_category_label:
        core_conn.execute(
            update(transactions_table).where(transactions_table.c.id == transaction_id).values(category_id=None)
        )
        core_conn.commit()

    assert expression_snapshot(core_conn, transaction_id) == expected


@pytest.mark.parametrize(
    ("description", "amount", "category", "transaction_kind", "expected_income", "expected_cashflow"),
    [
        (
            "ordinary expense",
            "100.00",
            "Food",
            TRANSACTION_KIND_EXPENSE,
            Decimal("-100.00"),
            Decimal("-100.00"),
        ),
        (
            "ordinary refund",
            "-20.00",
            "Food",
            TRANSACTION_KIND_REFUND,
            Decimal("20.00"),
            Decimal("20.00"),
        ),
        (
            "ordinary income",
            "-100.00",
            "Income",
            TRANSACTION_KIND_INCOME,
            Decimal("100.00"),
            Decimal("100.00"),
        ),
    ],
)
def test_amount_expressions_preserve_income_and_cashflow_signs(
    core_conn,
    data_factory,
    description,
    amount,
    category,
    transaction_kind,
    expected_income,
    expected_cashflow,
):
    """Verify amount expressions preserve the report sign convention."""
    transaction_id = data_factory.transactions.create(
        description=description,
        amount=Decimal(amount),
        category=category,
        transaction_kind=transaction_kind,
        needs_review=0,
    )

    row = (
        core_conn.execute(
            select(
                income_amount_expression().label("income_amount"),
                cashflow_amount_expression().label("cashflow_amount"),
            )
            .select_from(transactions_table)
            .where(transactions_table.c.id == transaction_id)
        )
        .mappings()
        .one()
    )

    assert money_to_decimal(row["income_amount"]) == expected_income
    assert money_to_decimal(row["cashflow_amount"]) == expected_cashflow


def test_spending_impact_amount_subtracts_reimbursement_allocations(core_conn, data_factory):
    """Verify reimbursement allocations reduce the original expense amount."""
    expense_id = data_factory.transactions.create(
        description="Conference hotel",
        amount=Decimal("100.00"),
        category="Travel",
        transaction_kind=TRANSACTION_KIND_EXPENSE,
        needs_review=0,
    )
    reimbursement_id = data_factory.transactions.create(
        description="Employer reimbursement",
        amount=Decimal("-40.00"),
        category=REIMBURSEMENT_CATEGORY,
        transaction_kind=TRANSACTION_KIND_INCOME,
        needs_review=0,
    )
    create_reimbursement_allocation(reimbursement_id, expense_id, Decimal("40.00"), conn=core_conn)

    row = (
        core_conn.execute(
            select(
                spending_impact_amount_expression().label("spending_amount"),
                cashflow_amount_expression().label("cashflow_amount"),
            )
            .select_from(transactions_table)
            .where(transactions_table.c.id == expense_id)
        )
        .mappings()
        .one()
    )

    assert money_to_decimal(row["spending_amount"]) == Decimal("60.00")
    assert money_to_decimal(row["cashflow_amount"]) == Decimal("-60.00")
