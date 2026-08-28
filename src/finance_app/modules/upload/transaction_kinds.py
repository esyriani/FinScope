"""Upload transaction-kind helpers.

Classifies imported rows as reportable transactions or account movements and
updates linked payment rows. Callers provide an active SQLAlchemy connection.
"""

from collections.abc import Iterable, Mapping, MutableMapping, Sequence
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select, update

from finance_app.core.constants import (
    ACCOUNT_TYPE_CHECKING,
    ACCOUNT_TYPE_CREDIT_CARD,
    ACCOUNT_TYPE_SAVINGS,
    STATEMENT_IMPORT_MODE_ENRICHMENT,
    STATEMENT_IMPORT_MODE_LEDGER,
    STATEMENT_TYPE_PARSER_INTERAC_ETRANSFER,
    TRANSACTION_KIND_EXPENSE,
    TRANSACTION_KIND_INCOME,
    TRANSACTION_KIND_PAYMENT,
    TRANSACTION_KIND_REFUND,
    TRANSACTION_KIND_TRANSFER,
    TRANSFER_CATEGORY,
    UNKNOWN_CATEGORY,
)
from finance_app.core.money import MoneyValue, money_to_decimal
from finance_app.database.dates import coerce_date
from finance_app.database.tables import (
    accounts as accounts_table,
)
from finance_app.database.tables import (
    transactions as transactions_table,
)
from finance_app.modules.categories.decision import DECISION_SOURCE_RULE
from finance_app.modules.categories.repository import resolve_category_id
from finance_app.modules.categories.sources import (
    CATEGORY_SOURCE_RULE,
    TransactionCategoryState,
    category_assignment,
)
from finance_app.modules.categories.taxonomy import get_transaction_tag_names
from finance_app.modules.settings.runtime import get_unknown_category

PAYMENT_MATCH_DATE_TOLERANCE_DAYS = 5
PAYMENT_MATCH_AMOUNT_TOLERANCE = Decimal("0.005")
PAYMENT_DESCRIPTION_MARKERS = (
    "PAYMENT THANK YOU",
    "PAIEMENT",
    "PAIEMEN T MERCI",
    "CREDIT CARD PAYMENT",
)


def default_import_mode(statement_type: object) -> str:
    """Return the default import mode for a parser type."""
    if statement_type == STATEMENT_TYPE_PARSER_INTERAC_ETRANSFER:
        return STATEMENT_IMPORT_MODE_ENRICHMENT
    return STATEMENT_IMPORT_MODE_LEDGER


def apply_transaction_kind_categories(
    transactions: Iterable[MutableMapping[str, Any]],
    unknown_category: str = UNKNOWN_CATEGORY,
) -> None:
    """Set category metadata for payment and transfer transactions."""
    for tx in transactions:
        if tx.get("transaction_kind") not in {TRANSACTION_KIND_PAYMENT, TRANSACTION_KIND_TRANSFER}:
            continue

        state = TransactionCategoryState(
            category=TRANSFER_CATEGORY,
            needs_review=0,
            assignment=category_assignment(
                TRANSFER_CATEGORY,
                unknown_category,
                CATEGORY_SOURCE_RULE,
                confidence=1.0,
                rule_id=None,
                metadata={
                    "decision_source": DECISION_SOURCE_RULE,
                    "reason": "transaction_kind_payment_or_transfer",
                    "final_category": TRANSFER_CATEGORY,
                    "final_confidence": 1.0,
                    "review_required": False,
                },
            ),
        )
        state.apply_to(tx)


def classify_transaction_kind(conn: Any, account_id: object, transaction: Mapping[str, Any]) -> str:
    """Classify a transaction as reportable spending/income or balance movement."""
    amount = transaction.get("amount") or 0
    account = account_row(conn, account_id)
    account_type = account["account_type"] if account else ACCOUNT_TYPE_CHECKING
    description = transaction.get("description", "")

    if is_payment_description(description):
        return TRANSACTION_KIND_PAYMENT
    if account_type in {ACCOUNT_TYPE_CHECKING, ACCOUNT_TYPE_SAVINGS} and amount > 0:
        if is_payment_to_linked_credit_account(conn, account_id, description):
            return TRANSACTION_KIND_PAYMENT
    if account_type == ACCOUNT_TYPE_CREDIT_CARD and amount < 0:
        return TRANSACTION_KIND_PAYMENT if is_payment_description(description) else TRANSACTION_KIND_REFUND

    return TRANSACTION_KIND_INCOME if amount < 0 else TRANSACTION_KIND_EXPENSE


def account_row(conn: Any, account_id: object) -> Mapping[str, Any] | None:
    """Return account metadata for import classification."""
    if account_id is None:
        return None
    return (
        conn.execute(
            select(
                accounts_table.c.id,
                accounts_table.c.name,
                accounts_table.c.account_type,
                accounts_table.c.paid_from_account_id,
            ).where(accounts_table.c.id == account_id)
        )
        .mappings()
        .fetchone()
    )


def is_payment_description(description: object) -> bool:
    """Return whether a description clearly denotes a card/account payment."""
    normalized = " ".join(str(description or "").upper().split())
    return any(marker in normalized for marker in PAYMENT_DESCRIPTION_MARKERS)


def is_payment_to_linked_credit_account(conn: Any, paid_from_account_id: object, description: object) -> bool:
    """Return whether a checking row appears to pay a linked credit account."""
    normalized_description = " ".join(str(description or "").upper().split())
    if not normalized_description:
        return False

    linked_accounts = (
        conn.execute(
            select(accounts_table.c.name).where(
                accounts_table.c.paid_from_account_id == paid_from_account_id,
                accounts_table.c.account_type == ACCOUNT_TYPE_CREDIT_CARD,
            )
        )
        .mappings()
        .fetchall()
    )

    for account in linked_accounts:
        account_tokens = [token for token in " ".join(account["name"].upper().split()).split() if len(token) >= 4]
        if account_tokens and any(token in normalized_description for token in account_tokens):
            return True

    return False


def mark_linked_account_payments(
    conn: Any,
    account_id: object,
    imported_transactions: Iterable[Mapping[str, Any]],
    undo_state: MutableMapping[str, Any] | None = None,
) -> int:
    """Mark existing funding-account rows as payments for a credit card import."""
    credit_account = account_row(conn, account_id)
    if (
        credit_account is None
        or credit_account["account_type"] != ACCOUNT_TYPE_CREDIT_CARD
        or credit_account["paid_from_account_id"] is None
    ):
        return 0

    updated_count = 0
    for tx in imported_transactions:
        if tx.get("transaction_kind") != TRANSACTION_KIND_PAYMENT or tx.get("amount", 0) >= 0:
            continue

        match = find_linked_payment_match(
            conn,
            credit_account,
            abs(tx["amount"]),
            tx["tx_date"],
        )
        if match is None or match == "ambiguous":
            continue

        assert isinstance(match, Mapping)
        record_transaction_undo_state(conn, match, undo_state)
        mark_transaction_as_payment(conn, match["id"])
        updated_count += 1

    return updated_count


def find_linked_payment_match(
    conn: Any,
    credit_account: Mapping[str, Any],
    amount: MoneyValue,
    tx_date: object,
) -> Mapping[str, Any] | str | None:
    """Return the unique funding-account row that matches a card payment."""
    amount_decimal = money_to_decimal(amount)
    rows = (
        conn.execute(
            transaction_snapshot_select()
            .where(
                transactions_table.c.account_id == credit_account["paid_from_account_id"],
                transactions_table.c.ignored == 0,
                transactions_table.c.amount > 0,
                transactions_table.c.amount > amount_decimal - PAYMENT_MATCH_AMOUNT_TOLERANCE,
                transactions_table.c.amount < amount_decimal + PAYMENT_MATCH_AMOUNT_TOLERANCE,
                transactions_table.c.tx_date >= date_window_start(tx_date, PAYMENT_MATCH_DATE_TOLERANCE_DAYS),
                transactions_table.c.tx_date <= date_window_end(tx_date, PAYMENT_MATCH_DATE_TOLERANCE_DAYS),
                transactions_table.c.transaction_kind != TRANSACTION_KIND_PAYMENT,
            )
            .order_by(transactions_table.c.tx_date, transactions_table.c.id)
        )
        .mappings()
        .fetchall()
    )
    return nearest_unique_match(
        [row for row in rows if is_linked_payment_description(row["description"], credit_account["name"])],
        tx_date,
    )


def transaction_snapshot_select() -> Any:
    """Return a Core select for transaction fields needed by upload undo and matching."""
    return select(
        transactions_table.c.id,
        transactions_table.c.statement_id,
        transactions_table.c.account_id,
        transactions_table.c.merchant_id,
        transactions_table.c.tx_date,
        transactions_table.c.description,
        transactions_table.c.amount,
        transactions_table.c.category,
        transactions_table.c.category_id,
        transactions_table.c.needs_review,
        transactions_table.c.category_source,
        transactions_table.c.category_confidence,
        transactions_table.c.category_rule_id,
        transactions_table.c.category_metadata,
        transactions_table.c.categorized_at,
        transactions_table.c.reviewed_at,
        transactions_table.c.ignored,
        transactions_table.c.transaction_kind,
    )


def nearest_unique_match(rows: Sequence[Mapping[str, Any]], target_date: object) -> Mapping[str, Any] | str | None:
    """Return the nearest transaction row or the ambiguity sentinel."""
    if not rows:
        return None

    nearest_delta = min(abs_date_delta(row["tx_date"], target_date) for row in rows)
    nearest_rows = [row for row in rows if abs_date_delta(row["tx_date"], target_date) == nearest_delta]
    if len(nearest_rows) != 1:
        return "ambiguous"
    return nearest_rows[0]


def date_window_start(value: object, tolerance_days: int) -> date:
    """Return the inclusive start date for a date-tolerance window."""
    parsed = coerce_date(value)
    if parsed is None:
        raise ValueError("Invalid transaction date.")
    return parsed - timedelta(days=tolerance_days)


def date_window_end(value: object, tolerance_days: int) -> date:
    """Return the inclusive end date for a date-tolerance window."""
    parsed = coerce_date(value)
    if parsed is None:
        raise ValueError("Invalid transaction date.")
    return parsed + timedelta(days=tolerance_days)


def is_linked_payment_description(description: object, credit_account_name: object) -> bool:
    """Return whether a funding row description points to a credit account."""
    normalized_description = " ".join(str(description or "").upper().split())
    normalized_account = " ".join(str(credit_account_name or "").upper().split())
    account_tokens = [token for token in normalized_account.split() if len(token) >= 4]
    if account_tokens and any(token in normalized_description for token in account_tokens):
        return True
    return "CREDIT CARD" in normalized_description or " CIBC MC" in f" {normalized_description}"


def mark_transaction_as_payment(conn: Any, transaction_id: object) -> None:
    """Mark a transaction as a non-reportable account payment."""
    metadata = category_assignment(
        TRANSFER_CATEGORY,
        get_unknown_category(conn) or UNKNOWN_CATEGORY,
        CATEGORY_SOURCE_RULE,
        confidence=1.0,
        rule_id=None,
        metadata={
            "decision_source": DECISION_SOURCE_RULE,
            "reason": "linked_account_payment",
            "final_category": TRANSFER_CATEGORY,
            "final_confidence": 1.0,
            "review_required": False,
        },
    )
    conn.execute(
        update(transactions_table)
        .where(transactions_table.c.id == transaction_id)
        .values(
            transaction_kind=TRANSACTION_KIND_PAYMENT,
            category=TRANSFER_CATEGORY,
            category_id=resolve_category_id(conn, TRANSFER_CATEGORY),
            needs_review=0,
            category_source=metadata.category_source,
            category_confidence=metadata.category_confidence,
            category_rule_id=metadata.category_rule_id,
            category_metadata=metadata.category_metadata,
            categorized_at=metadata.categorized_at,
            reviewed_at=metadata.reviewed_at,
        )
    )


def abs_date_delta(left: object, right: object) -> int:
    """Return absolute date distance in days."""
    left_date = coerce_date(left)
    right_date = coerce_date(right)
    if left_date is None or right_date is None:
        raise ValueError("Invalid transaction date.")
    return abs((left_date - right_date).days)


def record_interac_undo_state(
    conn: Any,
    transaction: Mapping[str, Any],
    undo_state: MutableMapping[str, Any] | None,
) -> None:
    """Capture original transaction state before Interac enrichment."""
    record_transaction_undo_state(conn, transaction, undo_state)


def record_transaction_undo_state(
    conn: Any,
    transaction: Mapping[str, Any],
    undo_state: MutableMapping[str, Any] | None,
) -> None:
    """Capture original transaction state before cross-account enrichment."""
    if undo_state is None:
        return

    changes = undo_state.setdefault("updated_transactions", [])
    transaction_id = transaction["id"]
    if any(change["id"] == transaction_id for change in changes):
        return

    changes.append(
        {
            "id": transaction_id,
            "merchant_id": transaction["merchant_id"],
            "description": transaction["description"],
            "category": transaction["category"],
            "category_id": transaction["category_id"],
            "needs_review": transaction["needs_review"],
            "category_source": transaction["category_source"],
            "category_confidence": transaction["category_confidence"],
            "category_rule_id": transaction["category_rule_id"],
            "category_metadata": transaction.get("category_metadata"),
            "categorized_at": transaction["categorized_at"],
            "reviewed_at": transaction["reviewed_at"],
            "transaction_kind": transaction["transaction_kind"],
            "tags": get_transaction_tag_names(conn, transaction_id),
        }
    )
