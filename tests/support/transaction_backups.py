"""Canonical transaction backup CSV builders for parser and workflow tests."""

import csv
import io

from finance_app.modules.transactions.exporter import TRANSACTION_BACKUP_COLUMNS


def backup_csv(rows):
    """Return transaction backup CSV text for raw row dictionaries."""
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=TRANSACTION_BACKUP_COLUMNS)
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue()


def backup_row(**overrides):
    """Return a minimally valid transaction backup CSV row."""
    row = dict.fromkeys(TRANSACTION_BACKUP_COLUMNS, "")
    row.update(
        {
            "tx_date": "2026-05-03",
            "description": "Preview transaction",
            "amount": "12.34",
            "category": "UNKNOWN",
            "needs_review": "0",
            "category_source": "unknown",
            "ignored": "0",
            "transaction_kind": "expense",
            "fingerprint": "backup-preview-fingerprint",
            "reimbursement_expense_completed": "0",
        }
    )
    row.update(overrides)
    if row["paid_from_account_name"]:
        row["paid_from_account_type"] = overrides.get("paid_from_account_type", "checking")
    return row
