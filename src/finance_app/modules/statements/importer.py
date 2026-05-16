"""Statement and transaction import helpers."""

import csv
import hashlib
import io
import re
from datetime import datetime

from pypdf import PdfReader

from finance_app.core.config import settings
from finance_app.core.constants import (
    AMOUNT_COLUMNS,
    CREDIT_COLUMNS,
    DATE_COLUMNS,
    DATE_FORMATS,
    DEBIT_COLUMNS,
    DESCRIPTION_COLUMNS,
    FRENCH_MONTHS,
    INTERAC_DIRECTION_AUTO,
    INTERAC_DIRECTION_RECEIVED,
    INTERAC_DIRECTION_SENT,
    INTERAC_DIRECTIONS,
    STATEMENT_TYPE_PARSER_BANK_ACCOUNT,
    STATEMENT_TYPE_PARSER_CREDIT_CARD,
    STATEMENT_TYPE_PARSER_INTERAC_ETRANSFER,
    UNKNOWN_CATEGORY,
)
from finance_app.core.text import normalize_header, strip_accents


def file_checksum(file_storage):
    """Calculate checksum."""
    hasher = hashlib.sha256()

    file_storage.stream.seek(0)
    for chunk in iter(lambda: file_storage.stream.read(8192), b""):
        hasher.update(chunk)

    file_storage.stream.seek(0)
    return hasher.hexdigest()


def allowed_statement_file(filename):
    """Return whether statement file."""
    if "." not in filename:
        return False

    extension = filename.rsplit(".", 1)[1].lower()
    return extension in settings.allowed_statement_extensions


def get_file_extension(filename):
    """Return file extension."""
    return filename.rsplit(".", 1)[1].lower()


def extract_pdf_text(file_storage):
    """Extract pdf text."""
    file_storage.stream.seek(0)

    reader = PdfReader(file_storage.stream)
    pages_text = []

    for page in reader.pages:
        text = page.extract_text()
        if text:
            pages_text.append(text)

    file_storage.stream.seek(0)
    return "\n".join(pages_text)


def normalize_date_text(value):
    """Normalize date text."""
    text = strip_accents(value).replace(",", " ")
    tokens = []

    for token in text.split():
        normalized = token.strip(".").lower()
        tokens.append(FRENCH_MONTHS.get(normalized, token))

    return " ".join(tokens)


def parse_date(value):
    """Parse date."""
    raw_value = str(value).strip()

    if not raw_value:
        return None

    candidates = [
        raw_value,
        normalize_date_text(raw_value),
    ]

    for candidate in candidates:
        for fmt in DATE_FORMATS:
            try:
                return datetime.strptime(candidate, fmt).date().isoformat()
            except ValueError:
                continue

    return None


def parse_money(value):
    """Parse money."""
    if value is None:
        return None

    text = str(value).strip()

    if text in {"", "-", "--", "N/A"}:
        return None

    negative = False
    text = text.replace("\xa0", " ")
    text = re.sub(r"(?i)\bCAD\b", "", text)
    text = re.sub(r"(?i)CA\$", "", text)
    text = text.replace("$", "")
    text = text.strip()

    if text.startswith("(") and text.endswith(")"):
        negative = True
        text = text[1:-1]

    if text.endswith("-"):
        negative = True
        text = text[:-1]

    if text.startswith("-"):
        negative = True
        text = text[1:]

    text = re.sub(r"[^0-9,.]", "", text)

    if not text:
        return None

    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif "," in text:
        parts = text.split(",")
        if len(parts[-1]) in {1, 2}:
            text = "".join(parts[:-1]) + "." + parts[-1]
        else:
            text = text.replace(",", "")

    amount = float(text)
    return -amount if negative else amount


def csv_rows(raw_text):
    """Parse non-empty CSV rows using a detected dialect when possible."""
    sample = raw_text[:4096]
    delimiter = detect_csv_delimiter_from_header(raw_text)

    if delimiter is None:
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
            delimiter = dialect.delimiter
        except csv.Error:
            delimiter = csv.excel.delimiter

    reader = csv.reader(io.StringIO(raw_text), delimiter=delimiter)

    return [
        row
        for row in reader
        if any(cell.strip() for cell in row)
    ]


def detect_csv_delimiter_from_header(raw_text):
    """Detect a delimiter by finding a plausible transaction header row."""
    for delimiter in (",", ";", "\t", "|"):
        rows = csv.reader(io.StringIO(raw_text), delimiter=delimiter)
        for index, row in enumerate(rows):
            if index >= 10:
                break
            if len([cell for cell in row if cell.strip()]) < 3:
                continue
            if is_transaction_header_row(row):
                return delimiter
    return None


def is_transaction_header_row(row):
    """Return whether a parsed row contains transaction CSV header columns."""
    header_map = {
        normalize_header(cell): cell
        for cell in row
        if normalize_header(cell)
    }
    has_date = find_column(header_map, DATE_COLUMNS)
    has_description = find_column(header_map, DESCRIPTION_COLUMNS)
    has_amount = (
        find_column(header_map, DEBIT_COLUMNS)
        or find_column(header_map, CREDIT_COLUMNS)
        or find_column(header_map, AMOUNT_COLUMNS)
    )
    return bool(has_date and has_description and has_amount)


def find_column(header_map, candidates):
    """Find column."""
    for candidate in candidates:
        if candidate in header_map:
            return header_map[candidate]

    for normalized, original in header_map.items():
        if any(candidate in normalized for candidate in candidates):
            return original

    return None


def detect_csv_header(rows):
    """Detect the first plausible CSV header row in a statement export."""
    # Some financial exports include report titles or blank leading rows before
    # the real header, so inspect the first few rows for required columns.
    for index, row in enumerate(rows[:10]):
        if len([cell for cell in row if cell.strip()]) >= 3 and is_transaction_header_row(row):
            return index, row

    return None, None


def normalize_signed_amount(raw_amount, statement_type):
    """Normalize signed amount."""
    if raw_amount is None:
        return None

    if statement_type == STATEMENT_TYPE_PARSER_BANK_ACCOUNT:
        return -raw_amount

    return raw_amount


def build_transaction(raw_date, description, statement_type, raw_debit=None, raw_credit=None, raw_amount=None):
    """Build transaction."""
    tx_date = parse_date(raw_date)
    description = str(description or "").strip()

    if not tx_date or not description:
        return None

    debit = parse_money(raw_debit)
    credit = parse_money(raw_credit)
    signed_amount = parse_money(raw_amount)

    if debit is not None and abs(debit) > 0.004:
        amount = abs(debit)
    elif credit is not None and abs(credit) > 0.004:
        amount = -abs(credit)
    elif signed_amount is not None and abs(signed_amount) > 0.004:
        amount = normalize_signed_amount(signed_amount, statement_type)
    else:
        return None

    amount = round(amount, 2)

    return {
        "tx_date": tx_date,
        "description": description,
        "amount": amount,
        "category": UNKNOWN_CATEGORY,
        "needs_review": 1,
    }


def build_interac_transfer(
    raw_date,
    counterparty,
    raw_amount,
    direction,
    method=None,
    status=None,
    require_deposited_status=True,
):
    """Build an Interac e-Transfer history row.

    Interac history files are enrichment sources for checking-account ledger
    rows. Sent transfers are spending outflows; received transfers are credits.
    Cancelled or otherwise incomplete rows are ignored.
    """
    tx_date = parse_date(raw_date)
    description = str(counterparty or "").strip()
    amount = parse_money(raw_amount)
    normalized_status = str(status or "").strip().lower()

    if not tx_date or not description or amount is None or abs(amount) <= 0.004:
        return None
    if require_deposited_status and not (
        normalized_status.startswith("deposited")
        or normalized_status.startswith("autodeposited")
    ):
        return None

    signed_amount = abs(amount) if direction == INTERAC_DIRECTION_SENT else -abs(amount)
    return {
        "tx_date": tx_date,
        "description": description,
        "amount": round(signed_amount, 2),
        "category": UNKNOWN_CATEGORY,
        "needs_review": 1,
        "interac_direction": direction,
        "interac_counterparty": description,
        "interac_method": str(method or "").strip(),
        "interac_status": str(status or "").strip(),
    }


def normalize_interac_direction(direction):
    """Return a supported Interac direction override value."""
    normalized = str(direction or INTERAC_DIRECTION_AUTO).strip().lower()
    if normalized in INTERAC_DIRECTIONS:
        return normalized
    return INTERAC_DIRECTION_AUTO


def parse_interac_transactions(raw_text, interac_direction=INTERAC_DIRECTION_AUTO):
    """Parse Interac e-Transfer sent or received history CSV rows."""
    interac_direction = normalize_interac_direction(interac_direction)
    rows = csv_rows(raw_text)
    if not rows:
        return {
            "transactions": [],
            "ignored_rows": 0,
        }

    header = rows[0]
    header_map = {
        normalize_header(cell): cell
        for cell in header
        if normalize_header(cell)
    }
    sent_date_col = find_column(header_map, {"datesent"})
    deposited_date_col = find_column(header_map, {"datedeposited"})
    recipient_col = find_column(header_map, {"recipient"})
    received_from_col = find_column(header_map, {"receivedfrom"})
    amount_col = find_column(header_map, {"amount"})
    method_col = find_column(header_map, {"method"})
    status_col = find_column(header_map, {"status"})
    require_deposited_status = bool(status_col)

    if interac_direction in {INTERAC_DIRECTION_SENT, INTERAC_DIRECTION_RECEIVED}:
        direction = interac_direction
        date_col = sent_date_col or deposited_date_col or find_column(header_map, DATE_COLUMNS)
        counterparty_col = (
            recipient_col
            or received_from_col
            or find_column(header_map, DESCRIPTION_COLUMNS | {"counterparty"})
        )
    elif sent_date_col and recipient_col:
        direction = INTERAC_DIRECTION_SENT
        date_col = sent_date_col
        counterparty_col = recipient_col
    elif deposited_date_col and received_from_col:
        direction = INTERAC_DIRECTION_RECEIVED
        date_col = deposited_date_col
        counterparty_col = received_from_col
    else:
        return {
            "transactions": [],
            "ignored_rows": max(0, len(rows) - 1),
        }
    if not date_col or not counterparty_col:
        return {
            "transactions": [],
            "ignored_rows": max(0, len(rows) - 1),
        }

    transactions = []
    ignored_rows = 0
    for row in rows[1:]:
        padded_row = row + [""] * max(0, len(header) - len(row))
        record = dict(zip(header, padded_row))
        tx = build_interac_transfer(
            record.get(date_col),
            record.get(counterparty_col),
            record.get(amount_col) if amount_col else None,
            direction,
            method=record.get(method_col) if method_col else None,
            status=record.get(status_col) if status_col else None,
            require_deposited_status=require_deposited_status,
        )
        if tx:
            transactions.append(tx)
        else:
            ignored_rows += 1

    return {
        "transactions": transactions,
        "ignored_rows": ignored_rows,
    }


def parse_csv_transactions(
    raw_text,
    statement_type=STATEMENT_TYPE_PARSER_CREDIT_CARD,
    interac_direction=INTERAC_DIRECTION_AUTO,
):
    """Parse csv transactions."""
    if statement_type == STATEMENT_TYPE_PARSER_INTERAC_ETRANSFER:
        return parse_interac_transactions(raw_text, interac_direction=interac_direction)

    rows = csv_rows(raw_text)
    header_index, header = detect_csv_header(rows)
    transactions = []
    ignored_rows = 0

    if header is not None:
        # Header-based imports are preferred because bank and card exports use
        # different debit/credit conventions and column names.
        header_map = {
            normalize_header(cell): cell
            for cell in header
            if normalize_header(cell)
        }
        date_col = find_column(header_map, DATE_COLUMNS)
        description_col = find_column(header_map, DESCRIPTION_COLUMNS)
        debit_col = find_column(header_map, DEBIT_COLUMNS)
        credit_col = find_column(header_map, CREDIT_COLUMNS)
        amount_col = find_column(header_map, AMOUNT_COLUMNS)

        for row in rows[header_index + 1:]:
            padded_row = row + [""] * max(0, len(header) - len(row))
            record = dict(zip(header, padded_row))

            tx = build_transaction(
                record.get(date_col),
                record.get(description_col),
                statement_type,
                raw_debit=record.get(debit_col) if debit_col else None,
                raw_credit=record.get(credit_col) if credit_col else None,
                raw_amount=record.get(amount_col) if amount_col else None,
            )

            if tx:
                transactions.append(tx)
            else:
                ignored_rows += 1
    else:
        # Fall back to a compact date/description/amount shape for simple CSVs.
        for row in rows:
            if len(row) < 3:
                ignored_rows += 1
                continue

            tx = build_transaction(
                row[0],
                row[1],
                statement_type,
                raw_debit=row[2] if len(row) > 3 else None,
                raw_credit=row[3] if len(row) > 3 else None,
                raw_amount=row[2] if len(row) == 3 else None,
            )

            if tx:
                transactions.append(tx)
            else:
                ignored_rows += 1

    return {
        "transactions": transactions,
        "ignored_rows": ignored_rows,
    }


def transaction_fingerprint(tx, account_id=None):
    """Build fingerprint."""
    raw = f"{account_id}|{tx['tx_date']}|{tx['description']}|{tx['amount']}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
