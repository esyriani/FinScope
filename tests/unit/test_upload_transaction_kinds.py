"""Tests for upload transaction-kind and linked-payment helpers."""

import json
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from finance_app.core.constants import (
    ACCOUNT_TYPE_CHECKING,
    ACCOUNT_TYPE_CREDIT_CARD,
    ACCOUNT_TYPE_SAVINGS,
    STATEMENT_IMPORT_MODE_ENRICHMENT,
    STATEMENT_IMPORT_MODE_LEDGER,
    STATEMENT_TYPE_PARSER_BANK_ACCOUNT,
    STATEMENT_TYPE_PARSER_CREDIT_CARD,
    STATEMENT_TYPE_PARSER_INTERAC_ETRANSFER,
    TRANSACTION_KIND_EXPENSE,
    TRANSACTION_KIND_INCOME,
    TRANSACTION_KIND_PAYMENT,
    TRANSACTION_KIND_REFUND,
    TRANSACTION_KIND_TRANSFER,
    TRANSFER_CATEGORY,
    UNKNOWN_CATEGORY,
)
from finance_app.database.tables import transactions as transactions_table
from finance_app.modules.upload.transaction_kinds import (
    abs_date_delta,
    apply_transaction_kind_categories,
    classify_transaction_kind,
    date_window_end,
    date_window_start,
    default_import_mode,
    is_linked_payment_description,
    is_payment_description,
    is_payment_to_linked_credit_account,
    mark_linked_account_payments,
    nearest_unique_match,
    record_transaction_undo_state,
    transaction_snapshot_select,
)


def test_default_import_mode_uses_enrichment_only_for_interac_parser():
    """Verify parser type defaults separate ledger imports from Interac enrichment."""
    dynamic_interac_value = "_".join(["interac", "etransfer"])

    assert dynamic_interac_value == STATEMENT_TYPE_PARSER_INTERAC_ETRANSFER
    assert dynamic_interac_value is not STATEMENT_TYPE_PARSER_INTERAC_ETRANSFER
    assert default_import_mode(dynamic_interac_value) == STATEMENT_IMPORT_MODE_ENRICHMENT
    assert default_import_mode(STATEMENT_TYPE_PARSER_CREDIT_CARD) == STATEMENT_IMPORT_MODE_LEDGER
    assert default_import_mode(STATEMENT_TYPE_PARSER_BANK_ACCOUNT) == STATEMENT_IMPORT_MODE_LEDGER
    assert default_import_mode(f"{STATEMENT_TYPE_PARSER_INTERAC_ETRANSFER} import") == STATEMENT_IMPORT_MODE_LEDGER


def test_apply_transaction_kind_categories_only_updates_account_movements():
    """Verify payment and transfer rows become non-review transfer-category rows."""
    rows = [
        {"transaction_kind": TRANSACTION_KIND_EXPENSE, "category": UNKNOWN_CATEGORY, "needs_review": 1},
        {"transaction_kind": TRANSACTION_KIND_PAYMENT, "category": UNKNOWN_CATEGORY, "needs_review": 1},
        {"transaction_kind": TRANSACTION_KIND_TRANSFER, "category": UNKNOWN_CATEGORY, "needs_review": 1},
    ]

    apply_transaction_kind_categories(rows)

    assert [(row["category"], row["needs_review"]) for row in rows] == [
        (UNKNOWN_CATEGORY, 1),
        (TRANSFER_CATEGORY, 0),
        (TRANSFER_CATEGORY, 0),
    ]
    payment_metadata = json.loads(rows[1]["category_metadata"])
    assert rows[1]["category_confidence"] == 1.0
    assert payment_metadata["final_confidence"] == 1.0
    assert payment_metadata["review_required"] is False
    assert rows[0].get("category_metadata") is None


def test_classify_transaction_kind_respects_account_role_amount_sign_and_payment_text(core_conn, data_factory):
    """Verify imported transaction kind inference for account roles and signed amounts."""
    checking_id = data_factory.accounts.create(name="Main checking", account_type=ACCOUNT_TYPE_CHECKING)
    savings_id = data_factory.accounts.create(name="Emergency savings", account_type=ACCOUNT_TYPE_SAVINGS)
    credit_card_id = data_factory.accounts.create(
        name="CIBC Mastercard",
        account_type=ACCOUNT_TYPE_CREDIT_CARD,
        paid_from_account_id=checking_id,
    )

    cases = [
        (checking_id, {"amount": Decimal("25.00"), "description": "Metro"}, TRANSACTION_KIND_EXPENSE),
        (checking_id, {"amount": Decimal("-25.00"), "description": "Payroll"}, TRANSACTION_KIND_INCOME),
        (
            checking_id,
            {"amount": Decimal("-25.00"), "description": "Transfer to CIBC Mastercard"},
            TRANSACTION_KIND_INCOME,
        ),
        (
            checking_id,
            {"amount": Decimal("100.00"), "description": "Transfer to CIBC Mastercard"},
            TRANSACTION_KIND_PAYMENT,
        ),
        (savings_id, {"amount": Decimal("0.00"), "description": "Opening balance"}, TRANSACTION_KIND_EXPENSE),
        (credit_card_id, {"amount": Decimal("25.00"), "description": "Store"}, TRANSACTION_KIND_EXPENSE),
        (credit_card_id, {"amount": Decimal("0.00"), "description": "Zero adjustment"}, TRANSACTION_KIND_EXPENSE),
        (credit_card_id, {"amount": Decimal("-25.00"), "description": "Refund"}, TRANSACTION_KIND_REFUND),
        (
            credit_card_id,
            {"amount": Decimal("-25.00"), "description": " payment   thank   you "},
            TRANSACTION_KIND_PAYMENT,
        ),
        (None, {"amount": Decimal("-1.00"), "description": "Loose credit"}, TRANSACTION_KIND_INCOME),
    ]

    assert [classify_transaction_kind(core_conn, account_id, tx) for account_id, tx, _expected in cases] == [
        expected for _account_id, _tx, expected in cases
    ]


def test_payment_description_helpers_normalize_text_and_account_tokens(core_conn, data_factory):
    """Verify linked-payment descriptions match normalized payment and account text."""
    checking_id = data_factory.accounts.create(name="Main checking", account_type=ACCOUNT_TYPE_CHECKING)
    other_checking_id = data_factory.accounts.create(name="Other checking", account_type=ACCOUNT_TYPE_CHECKING)
    data_factory.accounts.create(
        name="CIBC Mastercard",
        account_type=ACCOUNT_TYPE_CREDIT_CARD,
        paid_from_account_id=checking_id,
    )
    data_factory.accounts.create(
        name="VISA",
        account_type=ACCOUNT_TYPE_CREDIT_CARD,
        paid_from_account_id=checking_id,
    )
    data_factory.accounts.create(
        name="Amex Platinum",
        account_type=ACCOUNT_TYPE_CREDIT_CARD,
        paid_from_account_id=other_checking_id,
    )
    data_factory.accounts.create(
        name="Line of credit",
        account_type=ACCOUNT_TYPE_SAVINGS,
        paid_from_account_id=checking_id,
    )

    assert is_payment_description("  paiement   merci  ")
    assert is_payment_to_linked_credit_account(core_conn, checking_id, "Transfer to CIBC mastercard account")
    assert is_payment_to_linked_credit_account(core_conn, checking_id, "Transfer to visa")
    assert not is_payment_to_linked_credit_account(core_conn, checking_id, "")
    assert not is_payment_to_linked_credit_account(core_conn, checking_id, "Transfer to Amex Platinum")
    assert not is_payment_to_linked_credit_account(core_conn, checking_id, "Transfer to line of credit")
    assert is_linked_payment_description("Online credit card payment", "Short")
    assert is_linked_payment_description("CIBC MC X6A2W2", "MC")
    assert is_linked_payment_description("VISA payment", "VISA")
    assert not is_linked_payment_description("Utility bill", "MC")


def test_nearest_unique_match_returns_closest_row_or_ambiguity():
    """Verify nearest matching uses absolute date distance and detects ties."""
    rows = [
        {"id": 1, "tx_date": date(2026, 5, 1)},
        {"id": 2, "tx_date": date(2026, 5, 4)},
        {"id": 3, "tx_date": date(2026, 5, 9)},
    ]
    tied_rows = [
        {"id": 1, "tx_date": date(2026, 5, 3)},
        {"id": 2, "tx_date": date(2026, 5, 7)},
    ]

    assert nearest_unique_match(rows, date(2026, 5, 5))["id"] == 2
    assert nearest_unique_match(tied_rows, date(2026, 5, 5)) == "ambiguous"
    assert nearest_unique_match([], date(2026, 5, 5)) is None


def test_date_window_helpers_are_inclusive_and_reject_invalid_dates():
    """Verify payment matching date windows preserve exact tolerance boundaries."""
    assert date_window_start("2026-05-10", 5) == date(2026, 5, 5)
    assert date_window_end("2026-05-10", 5) == date(2026, 5, 15)
    assert abs_date_delta("2026-05-10", "2026-05-15") == 5

    with pytest.raises(ValueError):
        date_window_start("not-a-date", 5)
    with pytest.raises(ValueError):
        abs_date_delta("2026-05-10", "not-a-date")


def test_mark_linked_account_payments_continues_after_unmatched_imported_payment(core_conn, data_factory):
    """Verify one unmatched imported card payment does not stop later payment matching."""
    checking_id = data_factory.accounts.create(name="Main checking", account_type=ACCOUNT_TYPE_CHECKING)
    credit_card_id = data_factory.accounts.create(
        name="CIBC Mastercard",
        account_type=ACCOUNT_TYPE_CREDIT_CARD,
        paid_from_account_id=checking_id,
    )
    funding_tx_id = data_factory.transactions.create(
        account_id=checking_id,
        tx_date="2026-05-06",
        description="CIBC Mastercard payment",
        amount=Decimal("200.00"),
        category=UNKNOWN_CATEGORY,
        needs_review=1,
        fingerprint="linked-payment-funding-row",
    )
    undo_state = {}

    updated_count = mark_linked_account_payments(
        core_conn,
        credit_card_id,
        [
            {
                "transaction_kind": TRANSACTION_KIND_EXPENSE,
                "amount": Decimal("-200.00"),
                "tx_date": "2026-05-06",
            },
            {
                "transaction_kind": TRANSACTION_KIND_PAYMENT,
                "amount": Decimal("-100.00"),
                "tx_date": "2026-05-05",
            },
            {
                "transaction_kind": TRANSACTION_KIND_PAYMENT,
                "amount": Decimal("-200.00"),
                "tx_date": "2026-05-06",
            },
        ],
        undo_state,
    )
    row = core_conn.execute(
        select(
            transactions_table.c.transaction_kind,
            transactions_table.c.category,
            transactions_table.c.needs_review,
        ).where(transactions_table.c.id == funding_tx_id)
    ).one()

    assert updated_count == 1
    assert tuple(row) == (TRANSACTION_KIND_PAYMENT, TRANSFER_CATEGORY, 0)
    assert [change["id"] for change in undo_state["updated_transactions"]] == [funding_tx_id]


@pytest.mark.parametrize(
    ("candidate_overrides", "expected_updated"),
    [
        ({}, 1),
        ({"amount": Decimal("199.99")}, 0),
        ({"amount": Decimal("200.01")}, 0),
        ({"tx_date": "2026-04-30"}, 0),
        ({"tx_date": "2026-05-01"}, 1),
        ({"ignored": 1}, 0),
        ({"transaction_kind": TRANSACTION_KIND_PAYMENT}, 0),
        ({"description": "ordinary bill payment"}, 0),
    ],
)
def test_mark_linked_account_payments_requires_matching_funding_row(
    core_conn,
    data_factory,
    candidate_overrides,
    expected_updated,
):
    """Verify linked card payments only match eligible funding-account rows."""
    checking_id = data_factory.accounts.create(name="Main checking", account_type=ACCOUNT_TYPE_CHECKING)
    credit_card_id = data_factory.accounts.create(
        name="CIBC Mastercard",
        account_type=ACCOUNT_TYPE_CREDIT_CARD,
        paid_from_account_id=checking_id,
    )
    funding_values = {
        "account_id": checking_id,
        "tx_date": "2026-05-06",
        "description": "CIBC Mastercard payment",
        "amount": Decimal("200.00"),
        "category": UNKNOWN_CATEGORY,
        "needs_review": 1,
        "fingerprint": f"linked-payment-{expected_updated}-{len(candidate_overrides)}",
    }
    funding_values.update(candidate_overrides)
    funding_tx_id = data_factory.transactions.create(**funding_values)

    updated_count = mark_linked_account_payments(
        core_conn,
        credit_card_id,
        [
            {
                "transaction_kind": TRANSACTION_KIND_PAYMENT,
                "amount": Decimal("-200.00"),
                "tx_date": "2026-05-06",
            }
        ],
        {},
    )
    row = core_conn.execute(
        select(
            transactions_table.c.transaction_kind,
            transactions_table.c.category,
            transactions_table.c.needs_review,
            transactions_table.c.category_confidence,
            transactions_table.c.category_metadata,
        ).where(transactions_table.c.id == funding_tx_id)
    ).one()

    assert updated_count == expected_updated
    if expected_updated:
        metadata = json.loads(row.category_metadata)
        assert row.transaction_kind == TRANSACTION_KIND_PAYMENT
        assert row.category == TRANSFER_CATEGORY
        assert row.needs_review == 0
        assert row.category_confidence == 1.0
        assert metadata["review_required"] is False
        assert metadata["reason"] == "linked_account_payment"
    else:
        assert row.transaction_kind == candidate_overrides.get("transaction_kind", TRANSACTION_KIND_EXPENSE)
        assert row.category == UNKNOWN_CATEGORY
        assert row.needs_review == 1
        assert row.category_confidence is None
        assert row.category_metadata is None


def test_mark_linked_account_payments_requires_paid_from_account(core_conn, data_factory):
    """Verify a payment candidate in another account is not linked to the card import."""
    checking_id = data_factory.accounts.create(name="Main checking", account_type=ACCOUNT_TYPE_CHECKING)
    other_checking_id = data_factory.accounts.create(name="Other checking", account_type=ACCOUNT_TYPE_CHECKING)
    credit_card_id = data_factory.accounts.create(
        name="CIBC Mastercard",
        account_type=ACCOUNT_TYPE_CREDIT_CARD,
        paid_from_account_id=checking_id,
    )
    funding_tx_id = data_factory.transactions.create(
        account_id=other_checking_id,
        tx_date="2026-05-06",
        description="CIBC Mastercard payment",
        amount=Decimal("200.00"),
        category=UNKNOWN_CATEGORY,
        needs_review=1,
        fingerprint="linked-payment-wrong-source-account",
    )

    updated_count = mark_linked_account_payments(
        core_conn,
        credit_card_id,
        [
            {
                "transaction_kind": TRANSACTION_KIND_PAYMENT,
                "amount": Decimal("-200.00"),
                "tx_date": "2026-05-06",
            }
        ],
        {},
    )
    row = core_conn.execute(
        select(transactions_table.c.transaction_kind).where(transactions_table.c.id == funding_tx_id)
    ).one()

    assert updated_count == 0
    assert row.transaction_kind == TRANSACTION_KIND_EXPENSE


def test_mark_linked_account_payments_updates_only_the_matched_row(core_conn, data_factory):
    """Verify linked payment marking does not update neighboring transaction ids."""
    checking_id = data_factory.accounts.create(name="Main checking", account_type=ACCOUNT_TYPE_CHECKING)
    credit_card_id = data_factory.accounts.create(
        name="CIBC Mastercard",
        account_type=ACCOUNT_TYPE_CREDIT_CARD,
        paid_from_account_id=checking_id,
    )
    lower_decoy_id = data_factory.transactions.create(
        account_id=checking_id,
        tx_date="2026-05-06",
        description="Coffee shop",
        amount=Decimal("4.00"),
        category=UNKNOWN_CATEGORY,
        needs_review=1,
        fingerprint="linked-payment-lower-decoy",
    )
    target_id = data_factory.transactions.create(
        account_id=checking_id,
        tx_date="2026-05-06",
        description="CIBC Mastercard payment",
        amount=Decimal("200.00"),
        category=UNKNOWN_CATEGORY,
        needs_review=1,
        fingerprint="linked-payment-target-only",
    )
    higher_decoy_id = data_factory.transactions.create(
        account_id=checking_id,
        tx_date="2026-05-06",
        description="Grocery store",
        amount=Decimal("12.00"),
        category=UNKNOWN_CATEGORY,
        needs_review=1,
        fingerprint="linked-payment-higher-decoy",
    )

    updated_count = mark_linked_account_payments(
        core_conn,
        credit_card_id,
        [
            {
                "transaction_kind": TRANSACTION_KIND_PAYMENT,
                "amount": Decimal("-200.00"),
                "tx_date": "2026-05-06",
            }
        ],
        {},
    )
    rows = {
        row.id: row.transaction_kind
        for row in core_conn.execute(
            select(transactions_table.c.id, transactions_table.c.transaction_kind).where(
                transactions_table.c.id.in_([lower_decoy_id, target_id, higher_decoy_id])
            )
        )
    }

    assert updated_count == 1
    assert rows == {
        lower_decoy_id: TRANSACTION_KIND_EXPENSE,
        target_id: TRANSACTION_KIND_PAYMENT,
        higher_decoy_id: TRANSACTION_KIND_EXPENSE,
    }


def test_mark_linked_account_payments_rejects_missing_account(core_conn):
    """Verify nonexistent account ids are ignored without attempting matching."""
    assert (
        mark_linked_account_payments(
            core_conn,
            999_999,
            [
                {
                    "transaction_kind": TRANSACTION_KIND_PAYMENT,
                    "amount": Decimal("-200.00"),
                    "tx_date": "2026-05-06",
                }
            ],
            {},
        )
        == 0
    )


def test_record_transaction_undo_state_records_each_transaction_once(core_conn, data_factory):
    """Verify repeated undo snapshots are deduplicated by transaction id."""
    first_id = data_factory.transactions.create(
        tx_date="2026-05-06",
        description="First payment",
        amount=Decimal("200.00"),
        category=UNKNOWN_CATEGORY,
        needs_review=1,
        fingerprint="undo-payment-first",
    )
    second_id = data_factory.transactions.create(
        tx_date="2026-05-07",
        description="Second payment",
        amount=Decimal("300.00"),
        category=UNKNOWN_CATEGORY,
        needs_review=1,
        fingerprint="undo-payment-second",
    )
    rows = core_conn.execute(
        transaction_snapshot_select()
        .where(transactions_table.c.id.in_([first_id, second_id]))
        .order_by(transactions_table.c.id)
    ).mappings()
    first_row, second_row = rows.fetchall()
    undo_state = {}

    record_transaction_undo_state(core_conn, first_row, undo_state)
    record_transaction_undo_state(core_conn, first_row, undo_state)
    record_transaction_undo_state(core_conn, second_row, undo_state)

    assert [change["id"] for change in undo_state["updated_transactions"]] == [first_id, second_id]
