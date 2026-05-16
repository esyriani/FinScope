"""Tests for statement parsing helpers."""

import pytest

from finance_app.core.constants import UNKNOWN_CATEGORY
from finance_app.modules.statements.importer import (
    build_transaction,
    parse_csv_transactions,
    parse_date,
    parse_money,
    transaction_fingerprint,
)


@pytest.mark.parametrize(
    ("raw_value", "expected"),
    [
        ("$1,234.56", 1234.56),
        ("CA$ 1 234,56", 1234.56),
        ("(42.10)", -42.10),
        ("12.34-", -12.34),
        ("N/A", None),
        ("", None),
    ],
)
def test_parse_money_accepts_statement_number_formats(raw_value, expected):
    """Verify that common bank and card amount formats parse consistently."""
    assert parse_money(raw_value) == expected


@pytest.mark.parametrize(
    ("raw_value", "expected"),
    [
        ("2026-01-02", "2026-01-02"),
        ("13/02/2026", "2026-02-13"),
        ("2 janvier 2026", "2026-01-02"),
        ("08-May-26", "2026-05-08"),
        ("not a date", None),
    ],
)
def test_parse_date_accepts_supported_statement_formats(raw_value, expected):
    """Verify that supported date formats normalize to ISO dates."""
    assert parse_date(raw_value) == expected


def test_build_transaction_prefers_debit_credit_columns():
    """Verify that debit and credit columns map to app spending conventions."""
    debit_tx = build_transaction("2026-01-02", "GROCERY", "credit_card", raw_debit="$12.34")
    credit_tx = build_transaction("2026-01-03", "PAYMENT", "credit_card", raw_credit="$100.00")

    assert debit_tx == {
        "tx_date": "2026-01-02",
        "description": "GROCERY",
        "amount": 12.34,
        "category": UNKNOWN_CATEGORY,
        "needs_review": 1,
    }
    assert credit_tx["amount"] == -100.00


def test_parse_csv_transactions_detects_header_after_intro_rows():
    """Verify that CSV parsing skips report preambles and imports valid rows."""
    raw_text = "\n".join(
        [
            "Monthly activity",
            "Generated,2026-05-09",
            "Transaction Date,Description,Debit,Credit",
            "2026-01-02,GROCERY,$12.34,",
            "2026-01-03,PAYMENT,,$100.00",
            "bad,row,,",
        ]
    )

    result = parse_csv_transactions(raw_text, statement_type="credit_card")

    assert result["ignored_rows"] == 1
    assert [
        (tx["tx_date"], tx["description"], tx["amount"])
        for tx in result["transactions"]
    ] == [
        ("2026-01-02", "GROCERY", 12.34),
        ("2026-01-03", "PAYMENT", -100.00),
    ]


def test_parse_csv_transactions_uses_compact_fallback_without_header():
    """Verify that simple date, description, amount CSV files still import."""
    result = parse_csv_transactions(
        "2026-01-02,CORNER STORE,8.50\ninvalid,row\n",
        statement_type="credit_card",
    )

    assert result["ignored_rows"] == 1
    assert result["transactions"][0]["description"] == "CORNER STORE"
    assert result["transactions"][0]["amount"] == 8.50


def test_parse_csv_transactions_counts_malformed_rows_without_losing_valid_rows():
    """Verify mixed real-world CSV problems are skipped row by row."""
    raw_text = "\n".join(
        [
            "Downloaded from bank portal",
            "Date;Description;Amount",
            "2026-01-02;Quoted, Merchant;CA$ 1 234,56",
            "not-a-date;Bad Date;$10.00",
            "2026-01-03;Missing Amount;",
            "2026-01-04;Zero Amount;0.00",
            "2026-01-05;Valid Cafe;(42.10)",
        ]
    )

    result = parse_csv_transactions(raw_text, statement_type="bank_account")

    assert result["ignored_rows"] == 3
    assert [
        (tx["tx_date"], tx["description"], tx["amount"])
        for tx in result["transactions"]
    ] == [
        ("2026-01-02", "Quoted, Merchant", -1234.56),
        ("2026-01-05", "Valid Cafe", 42.10),
    ]


def test_parse_csv_transactions_parses_interac_sent_history():
    """Verify sent Interac history rows become spending enrichment rows."""
    raw_text = "\n".join(
        [
            "Date Sent,Recipient,Amount,Method,Status",
            "08-May-26,Kiet Menage,$350.00,Mobile,DepositedGo to Details",
            "01-May-26,Cancelled Person,$25.00,Email,CancelledGo to Details",
        ]
    )

    result = parse_csv_transactions(raw_text, statement_type="interac_etransfer")

    assert result["ignored_rows"] == 1
    assert result["transactions"] == [
        {
            "tx_date": "2026-05-08",
            "description": "Kiet Menage",
            "amount": 350.00,
            "category": UNKNOWN_CATEGORY,
            "needs_review": 1,
            "interac_direction": "sent",
            "interac_counterparty": "Kiet Menage",
            "interac_method": "Mobile",
            "interac_status": "DepositedGo to Details",
        }
    ]


def test_parse_csv_transactions_parses_interac_received_history():
    """Verify received Interac history rows become income enrichment rows."""
    raw_text = "\n".join(
        [
            "Date Deposited,Received From,Amount,Method,Status",
            "02-Jan-23,CHARLES-ANTOINE DEMERS,\"$1,250.00\",Email/Mobile,Autodeposited",
        ]
    )

    result = parse_csv_transactions(raw_text, statement_type="interac_etransfer")

    assert result["ignored_rows"] == 0
    assert result["transactions"][0]["tx_date"] == "2023-01-02"
    assert result["transactions"][0]["description"] == "CHARLES-ANTOINE DEMERS"
    assert result["transactions"][0]["amount"] == -1250.00
    assert result["transactions"][0]["interac_direction"] == "received"


def test_parse_csv_transactions_applies_interac_direction_override():
    """Verify Interac direction override signs generic positive-amount exports."""
    raw_text = "\n".join(
        [
            "Date,Name,Amount",
            "2026-05-08,Kiet Menage,$350.00",
        ]
    )

    sent = parse_csv_transactions(
        raw_text,
        statement_type="interac_etransfer",
        interac_direction="sent",
    )
    received = parse_csv_transactions(
        raw_text,
        statement_type="interac_etransfer",
        interac_direction="received",
    )

    assert sent["transactions"][0]["amount"] == 350.00
    assert sent["transactions"][0]["interac_direction"] == "sent"
    assert received["transactions"][0]["amount"] == -350.00
    assert received["transactions"][0]["interac_direction"] == "received"


def test_transaction_fingerprint_includes_account_boundary():
    """Verify that identical statement rows from different accounts do not collide."""
    tx = {
        "tx_date": "2026-01-02",
        "description": "GROCERY",
        "amount": 12.34,
    }

    assert transaction_fingerprint(tx, account_id=1) == transaction_fingerprint(tx, account_id=1)
    assert transaction_fingerprint(tx, account_id=1) != transaction_fingerprint(tx, account_id=2)
