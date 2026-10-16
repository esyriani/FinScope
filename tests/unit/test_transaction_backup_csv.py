"""Backup CSV validation and reversible spreadsheet escaping contracts."""

import json

import pytest
from tests.support.transaction_backups import backup_csv, backup_row

from finance_app.core.csv_export import CSV_FORMULA_PREFIXES
from finance_app.modules.transactions import backup_payloads
from finance_app.modules.transactions.backup_import import backup_import_value, parse_transactions_backup_csv
from finance_app.modules.transactions.backup_payloads import TransactionBackupImportPayloadStore
from finance_app.modules.transactions.exporter import escape_backup_cell


@pytest.mark.parametrize(
    "value",
    [
        "=SUM(1,2)",
        "+Credit",
        "-Debit",
        "@Account",
        "\tMemo",
        "\rMemo",
        "'=Literal",
        "''=Literal",
        "'Memo",
        'Text, "quoted"\nnext line',
        "  Spaced text  ",
    ],
)
def test_backup_cells_restore_exact_text_without_enabling_formulas(value):
    """Neutralize formulas and retain literal quotes, control characters and whitespace."""
    escaped = escape_backup_cell(value)
    assert not str(escaped).startswith(CSV_FORMULA_PREFIXES)
    assert backup_import_value(escaped) == value


@pytest.mark.parametrize(
    ("csv_text", "message"),
    [
        ("", "The transaction backup CSV is empty."),
        ("not a backup\n", "missing columns"),
        ('"unclosed header\n', "The backup CSV could not be read"),
    ],
)
def test_backup_parser_rejects_invalid_headers(csv_text, message):
    """Validate file-level errors without initializing Flask or a database."""
    with pytest.raises(ValueError, match=message):
        parse_transactions_backup_csv(csv_text)


@pytest.mark.parametrize("amount", ["NaN", "Infinity", "USD12.34oops", "1.001", "1000000000000"])
def test_backup_parser_rejects_invalid_money(amount):
    """Reject non-finite, malformed, over-precise and overflowing amounts without rounding."""
    parsed = parse_transactions_backup_csv(backup_csv([backup_row(amount=amount)]))

    assert not parsed.transactions
    assert len(parsed.row_errors) == 1
    assert parsed.row_errors[0].startswith("Row 2: amount must")


@pytest.mark.parametrize("malformation", ["repeated_heading", "missing_cell", "extra_cell", "unclosed_quote"])
def test_backup_parser_rejects_structural_corruption(malformation):
    """Corrupted row shapes must not silently drop or reinterpret transaction details."""
    csv_text = backup_csv([backup_row()])
    header, row = csv_text.splitlines()
    if malformation == "repeated_heading":
        csv_text = f"{header},amount\n{row},2.00\n"
    elif malformation == "missing_cell":
        csv_text = f"{header}\n{row.rsplit(',', 1)[0]}\n"
    elif malformation == "extra_cell":
        csv_text = f"{header}\n{row},unexpected\n"
    else:
        csv_text = f'{header}\n"unclosed value\n'
    if malformation in {"missing_cell", "extra_cell"}:
        parsed = parse_transactions_backup_csv(csv_text)
        assert not parsed.transactions
        assert parsed.row_errors == ("Row 2: The number of values does not match the column headings.",)
    else:
        with pytest.raises(ValueError):
            parse_transactions_backup_csv(csv_text)


def test_compact_backup_tags_preserve_names_containing_separators_and_literal_quotes():
    """JSON tag-name lists must not split punctuation or unescape nested user data."""
    names = ["Comma, tag", "Semi; tag", "Pipe | tag", "'=Literal", "=Formula"]
    parsed = parse_transactions_backup_csv(backup_csv([backup_row(tags=json.dumps(names))]))

    assert parsed.row_errors == ()
    assert parsed.transactions[0]["tags"] == tuple(names)


@pytest.mark.parametrize("tags", ['"not a list"', '[{"name":"obsolete details"}]', "[null]", '[""]'])
def test_compact_backup_rejects_invalid_tag_name_lists(tags):
    """Malformed tag names must block import rather than fabricate or silently discard tags."""
    parsed = parse_transactions_backup_csv(backup_csv([backup_row(tags=tags)]))

    assert not parsed.transactions
    assert len(parsed.row_errors) == 1


@pytest.mark.parametrize("link", [{"amount": "1.00"}, {"expense_fingerprint": "expense"}])
def test_compact_backup_requires_a_link_fingerprint_and_exact_amount(link):
    """Portable relationship records must contain both their destination identity and money value."""
    parsed = parse_transactions_backup_csv(backup_csv([backup_row(reimbursement_allocations=json.dumps([link]))]))

    assert not parsed.transactions
    assert len(parsed.row_errors) == 1


def test_backup_preview_expiry_does_not_reset_on_retry(monkeypatch):
    """Reading or releasing a claimed upload must not extend its original lifetime."""
    now = 100.0
    monkeypatch.setattr(backup_payloads.time, "monotonic", lambda: now)
    store = TransactionBackupImportPayloadStore()
    reference = store.store("private CSV", "owner")
    expires_at = now + backup_payloads.TRANSACTION_BACKUP_IMPORT_PAYLOAD_TTL_SECONDS
    assert store.get(reference, "other") is None
    assert store.claim(reference, "other") is None
    now += 60
    claimed = store.claim(reference, "owner")
    assert claimed is not None
    assert store.get(reference, "owner") is None
    store.release(reference, claimed)
    assert store.get(reference, "owner") == claimed
    now = expires_at - 1
    assert store.get(reference, "owner") == claimed
    now = expires_at
    assert store.get(reference, "owner") is None
    assert store.claim(reference, "owner") is None
    store.release(reference, claimed)
    assert store.get(reference, "owner") is None
