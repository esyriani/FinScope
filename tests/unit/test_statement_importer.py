"""Tests for statement parsing helpers."""

import pytest

from finance_app.core.constants import (
    DATE_ORDER_AUTO,
    DATE_ORDER_DAY_FIRST,
    DATE_ORDER_MONTH_FIRST,
    UNKNOWN_CATEGORY,
)
from finance_app.modules.statements.importer import (
    analyze_slash_date_order,
    build_transaction,
    date_formats_for_order,
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
        ("01-01-24", "2024-01-01"),
        ("13/02/2026", "2026-02-13"),
        ("2 janvier 2026", "2026-01-02"),
        ("08-May-26", "2026-05-08"),
        ("not a date", None),
    ],
)
def test_parse_date_accepts_supported_statement_formats(raw_value, expected):
    """Verify that supported date formats normalize to ISO dates."""
    assert parse_date(raw_value) == expected


@pytest.mark.parametrize(
    ("values", "date_order", "expected"),
    [
        (
            ["05/18/2026", "05/06/2026"],
            DATE_ORDER_AUTO,
            {
                "effective_order": DATE_ORDER_MONTH_FIRST,
                "inferred_order": DATE_ORDER_MONTH_FIRST,
                "source": "detected",
                "requires_choice": False,
                "month_first_evidence_count": 1,
                "day_first_evidence_count": 0,
                "ambiguous_count": 1,
                "slash_date_count": 2,
            },
        ),
        (
            ["18/05/2026", "07/05/2026"],
            DATE_ORDER_AUTO,
            {
                "effective_order": DATE_ORDER_DAY_FIRST,
                "inferred_order": DATE_ORDER_DAY_FIRST,
                "source": "detected",
                "requires_choice": False,
                "month_first_evidence_count": 0,
                "day_first_evidence_count": 1,
                "ambiguous_count": 1,
                "slash_date_count": 2,
            },
        ),
        (
            ["18-05-26", "07-05-26"],
            DATE_ORDER_AUTO,
            {
                "effective_order": DATE_ORDER_DAY_FIRST,
                "inferred_order": DATE_ORDER_DAY_FIRST,
                "source": "detected",
                "requires_choice": False,
                "month_first_evidence_count": 0,
                "day_first_evidence_count": 1,
                "ambiguous_count": 1,
                "slash_date_count": 2,
            },
        ),
        (
            ["05/12/2026", "06/07/2026"],
            DATE_ORDER_AUTO,
            {
                "effective_order": DATE_ORDER_AUTO,
                "inferred_order": None,
                "source": "auto",
                "requires_choice": True,
                "month_first_evidence_count": 0,
                "day_first_evidence_count": 0,
                "ambiguous_count": 2,
                "slash_date_count": 2,
            },
        ),
        (
            ["05/18/2026", "18/05/2026"],
            DATE_ORDER_AUTO,
            {
                "effective_order": DATE_ORDER_AUTO,
                "inferred_order": None,
                "source": "auto",
                "requires_choice": True,
                "month_first_evidence_count": 1,
                "day_first_evidence_count": 1,
                "ambiguous_count": 0,
                "slash_date_count": 2,
            },
        ),
        (
            ["05/12/2026"],
            DATE_ORDER_DAY_FIRST,
            {
                "effective_order": DATE_ORDER_DAY_FIRST,
                "inferred_order": None,
                "source": "selected",
                "requires_choice": False,
                "month_first_evidence_count": 0,
                "day_first_evidence_count": 0,
                "ambiguous_count": 1,
                "slash_date_count": 1,
            },
        ),
    ],
)
def test_analyze_slash_date_order_table_driven(values, date_order, expected):
    """Verify numeric date-order inference reports evidence and selected overrides."""
    analysis = analyze_slash_date_order(values, date_order=date_order)

    for key, expected_value in expected.items():
        assert analysis[key] == expected_value


@pytest.mark.parametrize(
    ("raw_value", "date_order", "expected"),
    [
        ("05/12/2026", DATE_ORDER_MONTH_FIRST, "2026-05-12"),
        ("05/12/2026", DATE_ORDER_DAY_FIRST, "2026-12-05"),
        ("13/02/2026", DATE_ORDER_MONTH_FIRST, "2026-02-13"),
        ("02/13/2026", DATE_ORDER_DAY_FIRST, "2026-02-13"),
    ],
)
def test_parse_date_respects_selected_slash_date_order(raw_value, date_order, expected):
    """Verify selected date-order formats drive ambiguous numeric date parsing."""
    assert parse_date(raw_value, date_formats=date_formats_for_order(date_order)) == expected


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
    assert [(tx["tx_date"], tx["description"], tx["amount"]) for tx in result["transactions"]] == [
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


def test_parse_csv_transactions_infers_month_first_slash_dates_without_header():
    """Verify compact CSV date parsing keeps month-first order for the statement."""
    raw_text = "\n".join(
        [
            "05/18/2026,DISNEY PLUS,9.19,,4463.99",
            "05/06/2026,HANGTAG PARKING,2.25,,4449.80",
        ]
    )

    result = parse_csv_transactions(raw_text, statement_type="bank_account")

    assert result["ignored_rows"] == 0
    assert [(tx["tx_date"], tx["description"], tx["amount"]) for tx in result["transactions"]] == [
        ("2026-05-18", "DISNEY PLUS", 9.19),
        ("2026-05-06", "HANGTAG PARKING", 2.25),
    ]


def test_parse_csv_transactions_keeps_day_first_slash_dates_when_inferred():
    """Verify unambiguous day-first rows guide ambiguous rows in the same CSV."""
    raw_text = "\n".join(
        [
            "18/05/2026,DISNEY PLUS,9.19,,4463.99",
            "07/05/2026,HANGTAG PARKING,2.25,,4449.80",
        ]
    )

    result = parse_csv_transactions(raw_text, statement_type="bank_account")

    assert [tx["tx_date"] for tx in result["transactions"]] == [
        "2026-05-18",
        "2026-05-07",
    ]


def test_parse_csv_transactions_respects_date_order_override():
    """Verify confirmed date order overrides ambiguous numeric CSV parsing."""
    raw_text = "05/12/2026,AMZN Mktp CA*PF2WC4HM3,134.56,,3922.64"

    month_first = parse_csv_transactions(
        raw_text,
        statement_type="bank_account",
        date_order="month_first",
    )
    day_first = parse_csv_transactions(
        raw_text,
        statement_type="bank_account",
        date_order="day_first",
    )

    assert month_first["transactions"][0]["tx_date"] == "2026-05-12"
    assert day_first["transactions"][0]["tx_date"] == "2026-12-05"


def test_parse_csv_transactions_parses_quoted_iso_debit_credit_without_header():
    """Verify quoted ISO compact bank rows import with debit and credit signs."""
    raw_text = "\n".join(
        [
            '"2026-04-29","PMT PRET  *326060301","2400",,"7355"',
            '"2026-04-30","UDEM            PAIE",,"3505.37","10860.37"',
        ]
    )

    result = parse_csv_transactions(raw_text, statement_type="bank_account")

    assert result["ignored_rows"] == 0
    assert [(tx["tx_date"], tx["description"], tx["amount"]) for tx in result["transactions"]] == [
        ("2026-04-29", "PMT PRET  *326060301", 2400.00),
        ("2026-04-30", "UDEM            PAIE", -3505.37),
    ]


def test_parse_csv_transactions_parses_td_checking_two_digit_hyphen_dates():
    """Verify TD-style headerless checking exports import debit and credit rows."""
    raw_text = "\n".join(
        [
            "01-01-24,Recept - VFC ***F3I REN,,1440.8,19866.4",
            "13-01-24,HYPOTHEQUE MAISON DESJARDINS,1760.38,,19776.81",
        ]
    )

    result = parse_csv_transactions(raw_text, statement_type="bank_account")

    assert result["ignored_rows"] == 0
    assert [(tx["tx_date"], tx["description"], tx["amount"]) for tx in result["transactions"]] == [
        ("2024-01-01", "Recept - VFC ***F3I REN", -1440.80),
        ("2024-01-13", "HYPOTHEQUE MAISON DESJARDINS", 1760.38),
    ]


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
    assert [(tx["tx_date"], tx["description"], tx["amount"]) for tx in result["transactions"]] == [
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
            "interac_merchant": "Kiet Menage",
            "interac_method": "Mobile",
            "interac_status": "DepositedGo to Details",
        }
    ]


def test_parse_csv_transactions_parses_interac_received_history():
    """Verify received Interac history rows become income enrichment rows."""
    raw_text = "\n".join(
        [
            "Date Deposited,Received From,Amount,Method,Status",
            '02-Jan-23,CHARLES-ANTOINE DEMERS,"$1,250.00",Email/Mobile,Autodeposited',
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
