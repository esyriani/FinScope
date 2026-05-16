"""Background workflow helpers for the upload feature."""

from datetime import timedelta

from sqlalchemy import delete, func, insert, or_, select, update
from sqlalchemy.exc import IntegrityError as SqlAlchemyIntegrityError

from finance_app.background.runner import submit_background_job
from finance_app.core.constants import (
    ACCOUNT_TYPE_CHECKING,
    ACCOUNT_TYPE_CREDIT_CARD,
    ACCOUNT_TYPE_SAVINGS,
    INTERAC_DIRECTION_AUTO,
    STATEMENT_IMPORT_MODE_ENRICHMENT,
    STATEMENT_IMPORT_MODE_LEDGER,
    STATEMENT_IMPORT_STATUS_COMPLETED,
    STATEMENT_IMPORT_STATUS_FAILED,
    STATEMENT_IMPORT_STATUS_QUEUED,
    STATEMENT_IMPORT_STATUS_RUNNING,
    STATEMENT_TYPE_PARSER_INTERAC_ETRANSFER,
    TRANSACTION_KIND_EXPENSE,
    TRANSACTION_KIND_INCOME,
    TRANSACTION_KIND_PAYMENT,
    TRANSACTION_KIND_REFUND,
    TRANSACTION_KIND_TRANSFER,
    TRANSFER_CATEGORY,
    UNKNOWN_CATEGORY,
)
from finance_app.database.dates import coerce_date
from finance_app.database.engine import db_core_transaction
from finance_app.database.tables import (
    accounts as accounts_table,
    statements as statements_table,
    transactions as transactions_table,
)
from finance_app.modules.categories.sources import (
    CATEGORY_SOURCE_MANUAL,
    CATEGORY_SOURCE_RULE,
    CATEGORY_SOURCE_UNKNOWN,
    TransactionCategoryState,
    category_assignment,
    category_metadata_json,
    utc_timestamp,
)
from finance_app.modules.categories.decision import DECISION_SOURCE_RULE
from finance_app.modules.categories.repository import resolve_category_id
from finance_app.modules.categories.service import categorize_transactions
from finance_app.modules.categories.taxonomy import (
    get_transaction_tag_names,
    set_transaction_tags,
)
from finance_app.modules.merchants.repository import (
    get_or_create_merchant_for_description,
    get_or_create_merchant_for_name,
)
from finance_app.modules.settings.runtime import get_unknown_category
from finance_app.modules.statements.importer import parse_csv_transactions
from finance_app.modules.transactions.importer import filter_new_transactions


INTERAC_MATCH_DATE_TOLERANCE_DAYS = 5
PAYMENT_MATCH_DATE_TOLERANCE_DAYS = 5
INTERAC_DESCRIPTION_MARKERS = {
    "sent": ("ENVOI", "SENT E-TRANSFER"),
    "received": ("RECEPT", "RECEIVED E-TRANSFER"),
}
PAYMENT_DESCRIPTION_MARKERS = (
    "PAYMENT THANK YOU",
    "PAIEMENT",
    "PAIEMEN T MERCI",
    "CREDIT CARD PAYMENT",
)


def import_transactions(
    conn,
    statement_id,
    account_id,
    statement_type,
    extension,
    raw_text,
    undo_state=None,
    import_mode=None,
    interac_direction=INTERAC_DIRECTION_AUTO,
):
    """Import transactions."""
    inserted_count = 0
    skipped_count = 0
    ignored_count = 0
    import_mode = import_mode or default_import_mode(statement_type)
    if statement_type == STATEMENT_TYPE_PARSER_INTERAC_ETRANSFER:
        import_mode = STATEMENT_IMPORT_MODE_ENRICHMENT

    if extension != "csv":
        return inserted_count, skipped_count, ignored_count

    if statement_type == STATEMENT_TYPE_PARSER_INTERAC_ETRANSFER:
        parse_result = parse_csv_transactions(
            raw_text,
            statement_type,
            interac_direction=interac_direction,
        )
    else:
        parse_result = parse_csv_transactions(raw_text, statement_type)
    ignored_count = parse_result["ignored_rows"]
    if import_mode == STATEMENT_IMPORT_MODE_ENRICHMENT:
        if statement_type != STATEMENT_TYPE_PARSER_INTERAC_ETRANSFER:
            return inserted_count, skipped_count, ignored_count + len(parse_result["transactions"])
        return enrich_interac_transactions(
            conn,
            parse_result["transactions"],
            account_id,
            undo_state=undo_state,
            ignored_count=ignored_count,
        )

    transactions, duplicate_count = filter_new_transactions(
        conn,
        parse_result["transactions"],
        account_id,
    )
    skipped_count += duplicate_count
    for tx in transactions:
        tx["account_id"] = account_id
        merchant = get_or_create_merchant_for_description(conn, tx["description"])
        tx["merchant_id"] = merchant["id"] if merchant else None
        tx["transaction_kind"] = classify_transaction_kind(conn, account_id, tx)

    transactions = categorize_transactions(transactions, conn=conn, use_llm=False)
    apply_transaction_kind_categories(transactions, unknown_category=get_unknown_category(conn) or UNKNOWN_CATEGORY)

    inserted_fingerprints = set()
    for tx in transactions:
        if tx["fingerprint"] in inserted_fingerprints:
            skipped_count += 1
            continue

        transaction_id = insert_imported_transaction_if_new(conn, statement_id, account_id, tx)
        if transaction_id is None:
            skipped_count += 1
            continue

        set_transaction_tags(
            conn,
            transaction_id,
            tx.get("tags", []),
            source=tx.get("category_source", CATEGORY_SOURCE_UNKNOWN),
            rule_id=tx.get("category_rule_id"),
        )
        inserted_fingerprints.add(tx["fingerprint"])
        inserted_count += 1

    mark_linked_account_payments(conn, account_id, transactions, undo_state)
    return inserted_count, skipped_count, ignored_count


def insert_imported_transaction_if_new(conn, statement_id, account_id, tx):
    """Insert one imported transaction inside a savepoint.

    Duplicate fingerprints can still appear at insert time when another import
    committed after the prefilter ran. The savepoint keeps databases such as
    PostgreSQL from aborting the surrounding statement-import transaction.
    """
    try:
        with conn.begin_nested():
            return insert_imported_transaction(conn, statement_id, account_id, tx)
    except SqlAlchemyIntegrityError:
        return None


def insert_imported_transaction(conn, statement_id, account_id, tx):
    """Insert one imported transaction and return the new transaction ID."""
    values = {
        "statement_id": statement_id,
        "account_id": account_id,
        "merchant_id": tx.get("merchant_id"),
        "tx_date": tx["tx_date"],
        "description": tx["description"],
        "amount": tx["amount"],
        "category": tx["category"],
        "category_id": (
            tx.get("category_id")
            if tx.get("category_id") is not None
            else resolve_category_id(conn, tx["category"])
        ),
        "needs_review": int(tx.get("needs_review", 0)),
        "category_source": tx.get("category_source", CATEGORY_SOURCE_UNKNOWN),
        "category_confidence": tx.get("category_confidence"),
        "category_rule_id": tx.get("category_rule_id"),
        "category_metadata": category_metadata_json(tx.get("category_metadata")),
        "categorized_at": tx.get("categorized_at"),
        "reviewed_at": tx.get("reviewed_at"),
        "transaction_kind": tx.get("transaction_kind", TRANSACTION_KIND_EXPENSE),
        "fingerprint": tx["fingerprint"],
    }
    result = conn.execute(insert(transactions_table).values(**values))
    return result.inserted_primary_key[0]


def default_import_mode(statement_type):
    """Return the legacy-compatible import mode for a parser type."""
    if statement_type == STATEMENT_TYPE_PARSER_INTERAC_ETRANSFER:
        return STATEMENT_IMPORT_MODE_ENRICHMENT
    return STATEMENT_IMPORT_MODE_LEDGER


def apply_transaction_kind_categories(transactions, unknown_category=UNKNOWN_CATEGORY):
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


def classify_transaction_kind(conn, account_id, transaction):
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


def account_row(conn, account_id):
    """Return account metadata for import classification."""
    if account_id is None:
        return None
    return conn.execute(
        select(
            accounts_table.c.id,
            accounts_table.c.name,
            accounts_table.c.account_type,
            accounts_table.c.paid_from_account_id,
        ).where(accounts_table.c.id == account_id)
    ).mappings().fetchone()


def is_payment_description(description):
    """Return whether a description clearly denotes a card/account payment."""
    normalized = " ".join(str(description or "").upper().split())
    return any(marker in normalized for marker in PAYMENT_DESCRIPTION_MARKERS)


def is_payment_to_linked_credit_account(conn, paid_from_account_id, description):
    """Return whether a checking row appears to pay a linked credit account."""
    normalized_description = " ".join(str(description or "").upper().split())
    if not normalized_description:
        return False

    linked_accounts = conn.execute(
        select(accounts_table.c.name).where(
            accounts_table.c.paid_from_account_id == paid_from_account_id,
            accounts_table.c.account_type == ACCOUNT_TYPE_CREDIT_CARD,
        )
    ).mappings().fetchall()

    for account in linked_accounts:
        account_tokens = [
            token
            for token in " ".join(account["name"].upper().split()).split()
            if len(token) >= 4
        ]
        if account_tokens and any(token in normalized_description for token in account_tokens):
            return True

    return False


def mark_linked_account_payments(conn, account_id, imported_transactions, undo_state=None):
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

        record_transaction_undo_state(conn, match, undo_state)
        mark_transaction_as_payment(conn, match["id"])
        updated_count += 1

    return updated_count


def find_linked_payment_match(conn, credit_account, amount, tx_date):
    """Return the unique funding-account row that matches a card payment."""
    rows = conn.execute(
        transaction_snapshot_select()
        .where(
            transactions_table.c.account_id == credit_account["paid_from_account_id"],
            transactions_table.c.ignored == 0,
            transactions_table.c.amount > 0,
            transactions_table.c.amount > amount - 0.005,
            transactions_table.c.amount < amount + 0.005,
            transactions_table.c.tx_date >= date_window_start(tx_date, PAYMENT_MATCH_DATE_TOLERANCE_DAYS),
            transactions_table.c.tx_date <= date_window_end(tx_date, PAYMENT_MATCH_DATE_TOLERANCE_DAYS),
            transactions_table.c.transaction_kind != TRANSACTION_KIND_PAYMENT,
        )
        .order_by(transactions_table.c.tx_date, transactions_table.c.id)
    ).mappings().fetchall()
    return nearest_unique_match(
        [
            row for row in rows
            if is_linked_payment_description(row["description"], credit_account["name"])
        ],
        tx_date,
    )


def transaction_snapshot_select():
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


def nearest_unique_match(rows, target_date):
    """Return the nearest transaction row or the ambiguity sentinel."""
    if not rows:
        return None

    nearest_delta = min(abs_date_delta(row["tx_date"], target_date) for row in rows)
    nearest_rows = [
        row
        for row in rows
        if abs_date_delta(row["tx_date"], target_date) == nearest_delta
    ]
    if len(nearest_rows) != 1:
        return "ambiguous"
    return nearest_rows[0]


def date_window_start(value, tolerance_days):
    """Return the inclusive start date for a date-tolerance window."""
    return coerce_date(value) - timedelta(days=tolerance_days)


def date_window_end(value, tolerance_days):
    """Return the inclusive end date for a date-tolerance window."""
    return coerce_date(value) + timedelta(days=tolerance_days)


def is_linked_payment_description(description, credit_account_name):
    """Return whether a funding row description points to a credit account."""
    normalized_description = " ".join(str(description or "").upper().split())
    normalized_account = " ".join(str(credit_account_name or "").upper().split())
    account_tokens = [token for token in normalized_account.split() if len(token) >= 4]
    if account_tokens and any(token in normalized_description for token in account_tokens):
        return True
    return "CREDIT CARD" in normalized_description or " CIBC MC" in f" {normalized_description}"


def mark_transaction_as_payment(conn, transaction_id):
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


def enrich_interac_transactions(conn, transfers, account_id, undo_state=None, ignored_count=0):
    """Enrich matching checking-account transactions from Interac history rows.

    Interac history rows duplicate bank movements that already exist in the
    checking statement. They update the matched ledger transaction with the real
    counterparty merchant and then re-run rule categorization for that row.
    Unmatched or ambiguous rows are not inserted as transactions.
    """
    enriched_count = 0
    skipped_count = 0

    for transfer in transfers:
        merchant = get_or_create_merchant_for_name(conn, transfer["interac_counterparty"])
        if merchant is None:
            ignored_count += 1
            continue

        match = find_interac_match(conn, account_id, transfer, merchant["id"])
        if match is None:
            ignored_count += 1
            continue
        if match == "ambiguous":
            skipped_count += 1
            continue

        record_interac_undo_state(conn, match, undo_state)
        if should_preserve_existing_category(conn, match):
            update_transaction_identity(
                conn,
                match["id"],
                merchant["id"],
                transfer["interac_counterparty"],
            )
            enriched_count += 1
            continue

        enriched = categorize_transactions(
            [
                {
                    "id": match["id"],
                    "account_id": match["account_id"],
                    "tx_date": match["tx_date"],
                    "description": transfer["interac_counterparty"],
                    "amount": match["amount"],
                    "merchant_id": merchant["id"],
                }
            ],
            conn=conn,
            use_llm=False,
        )[0]
        update_enriched_transaction(
            conn,
            match["id"],
            merchant["id"],
            transfer["interac_counterparty"],
            enriched,
            match["transaction_kind"],
        )
        set_transaction_tags(
            conn,
            match["id"],
            enriched.get("tags", []),
            source=enriched.get("category_source", CATEGORY_SOURCE_UNKNOWN),
            rule_id=enriched.get("category_rule_id"),
        )
        enriched_count += 1

    return enriched_count, skipped_count, ignored_count


def update_transaction_identity(conn, transaction_id, merchant_id, description):
    """Update only merchant identity fields for an enriched transaction."""
    conn.execute(
        update(transactions_table)
        .where(transactions_table.c.id == transaction_id)
        .values(merchant_id=merchant_id, description=description)
    )


def update_enriched_transaction(conn, transaction_id, merchant_id, description, enriched, transaction_kind):
    """Persist merchant and categorization fields for an enriched transaction."""
    values = {
        "merchant_id": merchant_id,
        "description": description,
        "category": enriched["category"],
        "category_id": (
            enriched.get("category_id")
            if enriched.get("category_id") is not None
            else resolve_category_id(conn, enriched["category"])
        ),
        "needs_review": int(enriched.get("needs_review", 0)),
        "category_source": enriched.get("category_source", CATEGORY_SOURCE_UNKNOWN),
        "category_confidence": enriched.get("category_confidence"),
        "category_rule_id": enriched.get("category_rule_id"),
        "category_metadata": category_metadata_json(enriched.get("category_metadata")),
        "categorized_at": enriched.get("categorized_at"),
        "reviewed_at": enriched.get("reviewed_at"),
        "transaction_kind": transaction_kind,
    }
    conn.execute(
        update(transactions_table)
        .where(transactions_table.c.id == transaction_id)
        .values(**values)
    )


def should_preserve_existing_category(conn, transaction):
    """Return whether Interac enrichment should leave category state unchanged."""
    category = transaction["category"]
    unknown_category = get_unknown_category(conn)
    if transaction["category_source"] == CATEGORY_SOURCE_MANUAL:
        return True
    return bool(transaction["reviewed_at"] and category and category != unknown_category)


def find_interac_match(conn, account_id, transfer, merchant_id):
    """Return the unique checking transaction matched by an Interac history row."""
    match = find_interac_match_for_account(conn, account_id, transfer, merchant_id)
    if match is not None:
        return match

    if account_id is None or account_has_transactions(conn, account_id):
        return None

    return find_interac_match_for_other_ledger_accounts(conn, account_id, transfer, merchant_id)


def account_has_transactions(conn, account_id):
    """Return whether the selected account already contains ledger transactions."""
    return conn.execute(
        select(func.count().label("count"))
        .select_from(transactions_table)
        .where(transactions_table.c.account_id == account_id)
    ).scalar_one() > 0


def find_interac_match_for_account(conn, account_id, transfer, merchant_id):
    """Return an Interac match constrained to the selected account."""
    return find_interac_match_core(
        conn,
        transfer,
        merchant_id,
        account_id=account_id,
        account_is_null=account_id is None,
    )


def find_interac_match_for_other_ledger_accounts(conn, excluded_account_id, transfer, merchant_id):
    """Return an Interac match in another checking or savings ledger account.

    This fallback handles users who accidentally select a separate Interac
    history account even though enrichment must update the original ledger
    account that contains the bank movement.
    """
    return find_interac_match_core(
        conn,
        transfer,
        merchant_id,
        excluded_account_id=excluded_account_id,
        require_ledger_account=True,
    )


def find_interac_match_core(
    conn,
    transfer,
    merchant_id,
    account_id=None,
    account_is_null=False,
    excluded_account_id=None,
    require_ledger_account=False,
):
    """Return a Core Interac match for one account scope."""
    statement = transaction_snapshot_select()
    conditions = [
        transactions_table.c.ignored == 0,
        transactions_table.c.amount > transfer["amount"] - 0.005,
        transactions_table.c.amount < transfer["amount"] + 0.005,
        transactions_table.c.tx_date >= date_window_start(transfer["tx_date"], INTERAC_MATCH_DATE_TOLERANCE_DAYS),
        transactions_table.c.tx_date <= date_window_end(transfer["tx_date"], INTERAC_MATCH_DATE_TOLERANCE_DAYS),
        interac_counterparty_condition(transfer, merchant_id),
    ]

    if account_is_null:
        conditions.append(transactions_table.c.account_id.is_(None))
    elif account_id is not None:
        conditions.append(transactions_table.c.account_id == account_id)

    if excluded_account_id is not None:
        conditions.append(transactions_table.c.account_id != excluded_account_id)

    if require_ledger_account:
        statement = statement.select_from(
            transactions_table.join(
                accounts_table,
                accounts_table.c.id == transactions_table.c.account_id,
            )
        )
        conditions.append(accounts_table.c.account_type.in_((ACCOUNT_TYPE_CHECKING, ACCOUNT_TYPE_SAVINGS)))

    rows = conn.execute(
        statement.where(*conditions).order_by(transactions_table.c.tx_date, transactions_table.c.id)
    ).mappings().fetchall()
    return nearest_unique_match(rows, transfer["tx_date"])


def interac_counterparty_condition(transfer, merchant_id):
    """Return the Core condition that matches an Interac counterparty."""
    markers = INTERAC_DESCRIPTION_MARKERS.get(transfer.get("interac_direction"), ())
    conditions = [transactions_table.c.merchant_id == merchant_id]
    conditions.extend(
        func.upper(transactions_table.c.description).like(f"%{marker}%")
        for marker in markers
    )
    return or_(*conditions)


def abs_date_delta(left, right):
    """Return absolute date distance in days."""
    return abs((coerce_date(left) - coerce_date(right)).days)


def record_interac_undo_state(conn, transaction, undo_state):
    """Capture original transaction state before Interac enrichment."""
    record_transaction_undo_state(conn, transaction, undo_state)


def record_transaction_undo_state(conn, transaction, undo_state):
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


def import_statement_transactions_job(
    statement_id,
    account_id,
    statement_type,
    extension,
    raw_text,
    undo_state=None,
    import_mode=None,
    interac_direction=INTERAC_DIRECTION_AUTO,
):
    """Import statement transactions job."""
    import_mode = import_mode or default_import_mode(statement_type)

    llm_candidate_count = 0

    try:
        with db_core_transaction() as conn:
            update_statement_import_state(
                conn,
                statement_id,
                STATEMENT_IMPORT_STATUS_RUNNING,
                import_error=None,
                import_started_at=utc_timestamp(),
                import_finished_at=None,
            )

        with db_core_transaction() as conn:
            inserted_count, skipped_count, ignored_count = import_transactions(
                conn,
                statement_id,
                account_id,
                statement_type,
                extension,
                raw_text,
                undo_state=undo_state,
                import_mode=import_mode,
                interac_direction=interac_direction,
            )
            if (
                extension == "csv"
                and inserted_count
                and statement_type != STATEMENT_TYPE_PARSER_INTERAC_ETRANSFER
            ):
                llm_candidate_count = count_statement_unknown_transactions(conn, statement_id)
            update_statement_import_state(
                conn,
                statement_id,
                STATEMENT_IMPORT_STATUS_COMPLETED,
                import_error=None,
                import_finished_at=utc_timestamp(),
                imported_count=inserted_count,
                skipped_count=skipped_count,
                ignored_count=ignored_count,
                llm_candidate_count=llm_candidate_count,
            )
    except Exception as exc:
        with db_core_transaction() as conn:
            update_statement_import_state(
                conn,
                statement_id,
                STATEMENT_IMPORT_STATUS_FAILED,
                import_error=f"{type(exc).__name__}: {exc}",
                import_finished_at=utc_timestamp(),
            )
        raise

    if llm_candidate_count:
        submit_background_job(
            f"LLM categorize statement {statement_id}",
            categorize_statement_unknown_transactions_job,
        statement_id,
    )

    return upload_result_message(
        statement_type,
        extension,
        inserted_count,
        skipped_count,
        ignored_count,
        llm_candidate_count=llm_candidate_count,
    )


def reset_statement_import_state(conn, statement_id, status=STATEMENT_IMPORT_STATUS_QUEUED):
    """Reset persisted import metadata before queueing a statement import."""
    update_statement_import_state(
        conn,
        statement_id,
        status,
        import_error=None,
        import_started_at=None,
        import_finished_at=None,
        imported_count=0,
        skipped_count=0,
        ignored_count=0,
        llm_candidate_count=0,
    )


def update_statement_import_state(conn, statement_id, status, **fields):
    """Persist import status, timestamps, counters, and errors for a statement."""
    allowed_fields = {
        "import_error",
        "import_started_at",
        "import_finished_at",
        "imported_count",
        "skipped_count",
        "ignored_count",
        "llm_candidate_count",
    }
    for field, value in fields.items():
        if field not in allowed_fields:
            raise ValueError(f"Unsupported statement import field: {field}")

    conn.execute(
        update(statements_table)
        .where(statements_table.c.id == statement_id)
        .values(import_status=status, **fields)
    )


def count_statement_unknown_transactions(conn, statement_id):
    """Count statement unknown transactions."""
    unknown_category = get_unknown_category(conn)
    return conn.execute(
        select(func.count().label("count"))
        .select_from(transactions_table)
        .where(
            transactions_table.c.statement_id == statement_id,
            (
                transactions_table.c.category.is_(None)
                | (transactions_table.c.category == unknown_category)
            ),
        )
    ).scalar_one()


def categorize_statement_unknown_transactions_job(statement_id):
    """Categorize statement unknown transactions job."""
    with db_core_transaction() as conn:
        unknown_category = get_unknown_category(conn)
        rows = statement_unknown_transaction_rows(conn, statement_id, unknown_category)
        transactions = [
            {
                "id": row["id"],
                "account_id": row["account_id"],
                "tx_date": row["tx_date"],
                "merchant_id": row["merchant_id"],
                "description": row["description"],
                "amount": row["amount"],
                "category": row["category"] or unknown_category,
                "transaction_kind": row["transaction_kind"],
            }
            for row in rows
        ]
        if not transactions:
            return "No unknown transactions needed LLM categorization."

        categorized = categorize_transactions(transactions, conn=conn, use_llm=True)
        updated_count = 0
        for tx in categorized:
            is_unknown_result = tx.get("category") in (None, unknown_category)
            if is_unknown_result and not tx.get("category_metadata"):
                continue

            cursor = update_unknown_transaction_category(conn, tx, unknown_category)
            if cursor.rowcount and not is_unknown_result:
                set_transaction_tags(
                    conn,
                    tx["id"],
                    tx.get("tags", []),
                    source=tx.get("category_source", CATEGORY_SOURCE_UNKNOWN),
                    rule_id=tx.get("category_rule_id"),
                )
                updated_count += 1

    return f"LLM categorized {updated_count} transaction{'' if updated_count == 1 else 's'}."


def statement_unknown_transaction_rows(conn, statement_id, unknown_category):
    """Return statement transactions that still need unknown-category LLM categorization."""
    return conn.execute(
        select(
            transactions_table.c.id,
            transactions_table.c.account_id,
            transactions_table.c.tx_date,
            transactions_table.c.merchant_id,
            transactions_table.c.description,
            transactions_table.c.amount,
            transactions_table.c.category,
            transactions_table.c.transaction_kind,
        )
        .where(
            transactions_table.c.statement_id == statement_id,
            transactions_table.c.ignored == 0,
            (
                transactions_table.c.category.is_(None)
                | (transactions_table.c.category == unknown_category)
            ),
        )
        .order_by(transactions_table.c.id)
    ).mappings().fetchall()


def update_unknown_transaction_category(conn, tx, unknown_category):
    """Persist an LLM category for a transaction if it is still unknown."""
    values = {
        "category": tx["category"],
        "category_id": (
            tx.get("category_id")
            if tx.get("category_id") is not None
            else resolve_category_id(conn, tx["category"])
        ),
        "needs_review": int(tx.get("needs_review", 0)),
        "category_source": tx.get("category_source", CATEGORY_SOURCE_UNKNOWN),
        "category_confidence": tx.get("category_confidence"),
        "category_rule_id": tx.get("category_rule_id"),
        "category_metadata": category_metadata_json(tx.get("category_metadata")),
        "categorized_at": tx.get("categorized_at"),
        "reviewed_at": tx.get("reviewed_at"),
    }
    return conn.execute(
        update(transactions_table)
        .where(
            transactions_table.c.id == tx["id"],
            (
                transactions_table.c.category.is_(None)
                | (transactions_table.c.category == unknown_category)
            ),
        )
        .values(**values)
    )


def undo_statement_upload_job(statement_id, undo_state=None):
    """Undo statement upload job."""
    with db_core_transaction() as conn:
        statement = statement_filename_row(conn, statement_id)

        if statement is None:
            return "Statement was already removed."

        transaction_count = statement_transaction_count(conn, statement_id)

        delete_statement_transactions(conn, statement_id)
        delete_statement(conn, statement_id)
        restored_count = restore_interac_undo_state(conn, undo_state)

    message = (
        f"Removed statement {statement['filename']} "
        f"and {transaction_count} transaction{'s' if transaction_count != 1 else ''}."
    )
    if restored_count:
        message += f" Restored {restored_count} enriched transaction{'s' if restored_count != 1 else ''}."
    return message


def statement_filename_row(conn, statement_id):
    """Return the filename for one statement ID."""
    return conn.execute(
        select(statements_table.c.filename).where(statements_table.c.id == statement_id)
    ).mappings().fetchone()


def statement_transaction_count(conn, statement_id):
    """Return the number of transactions imported by one statement."""
    return conn.execute(
        select(func.count().label("count"))
        .select_from(transactions_table)
        .where(transactions_table.c.statement_id == statement_id)
    ).scalar_one()


def delete_statement_transactions(conn, statement_id):
    """Delete all transaction rows imported by one statement."""
    conn.execute(
        delete(transactions_table).where(transactions_table.c.statement_id == statement_id)
    )


def delete_statement(conn, statement_id):
    """Delete one persisted statement row."""
    conn.execute(delete(statements_table).where(statements_table.c.id == statement_id))


def restore_interac_undo_state(conn, undo_state):
    """Restore transactions changed by Interac enrichment."""
    changes = (undo_state or {}).get("updated_transactions") or []
    restored_count = 0
    for change in changes:
        cursor = restore_enriched_transaction(conn, change)
        if cursor.rowcount:
            set_transaction_tags(
                conn,
                change["id"],
                change.get("tags", []),
                source=change["category_source"],
                rule_id=change["category_rule_id"],
            )
            restored_count += 1
    return restored_count


def restore_enriched_transaction(conn, change):
    """Restore a transaction changed by upload enrichment."""
    values = {
        "merchant_id": change["merchant_id"],
        "description": change["description"],
        "category": change["category"],
        "category_id": (
            change.get("category_id")
            if change.get("category_id") is not None
            else resolve_category_id(conn, change["category"])
        ),
        "needs_review": change["needs_review"],
        "category_source": change["category_source"],
        "category_confidence": change["category_confidence"],
        "category_rule_id": change["category_rule_id"],
        "category_metadata": change.get("category_metadata"),
        "categorized_at": change["categorized_at"],
        "reviewed_at": change["reviewed_at"],
        "transaction_kind": change.get("transaction_kind", TRANSACTION_KIND_EXPENSE),
    }
    return conn.execute(
        update(transactions_table)
        .where(transactions_table.c.id == change["id"])
        .values(**values)
    )


def upload_result_message(statement_type, extension, inserted_count, skipped_count, ignored_count, llm_candidate_count=0):
    """Render result message."""
    if statement_type == STATEMENT_TYPE_PARSER_INTERAC_ETRANSFER:
        message = f"Interac history processed. Enriched {inserted_count} existing transactions. "
        if skipped_count:
            message += (
                f"Skipped {skipped_count} ambiguous match"
                f"{'' if skipped_count == 1 else 'es'} because each matched more than one possible checking transaction. "
            )
        if ignored_count:
            ignored_label = "row" if ignored_count == 1 else "rows"
            ignored_verb = "was" if ignored_count == 1 else "were"
            message += (
                f"Ignored {ignored_count} {ignored_label} that {ignored_verb} cancelled, "
                "non-deposited, or had no matching checking ledger transaction yet. "
                "Import matching checking statements first, then reprocess this Interac history. "
            )
        message += "No duplicate Interac ledger rows were added."
        return message

    message = (
        f"Statement uploaded. Added {inserted_count} transactions. "
        f"Skipped {skipped_count} duplicate transactions. "
    )

    if ignored_count:
        message += f"Ignored {ignored_count} non-transaction rows. "

    if extension == "pdf":
        message += "PDF text was captured for review; automatic PDF transaction parsing is not enabled yet. "

    if llm_candidate_count:
        message += (
            f"Queued LLM categorization for {llm_candidate_count} unknown transaction"
            f"{'' if llm_candidate_count == 1 else 's'}. "
        )

    message += "The original file was not stored."
    return message
