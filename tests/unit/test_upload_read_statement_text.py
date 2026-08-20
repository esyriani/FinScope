"""Tests for statement text extraction in upload previews."""

import io

from werkzeug.datastructures import FileStorage

from finance_app.modules.upload.preview import read_statement_text


def file_storage(payload=b"content", filename="statement.csv"):
    """Build a FileStorage object backed by memory."""
    return FileStorage(stream=io.BytesIO(payload), filename=filename)


def test_read_statement_text_rejects_pdf_files():
    """Verify PDF uploads are rejected because statements are CSV-only."""
    uploaded_file = file_storage(b"%PDF-1.4", "scanned.pdf")

    assert read_statement_text(uploaded_file, "pdf") is None


def test_read_statement_text_rejects_unsupported_file_types():
    """Verify unsupported extensions return no text."""
    uploaded_file = file_storage(b"plain text", "statement.txt")

    assert read_statement_text(uploaded_file, "txt") is None


def test_read_statement_text_decodes_csv_with_utf8_sig():
    """Verify CSV uploads are decoded and stream position is restored."""
    uploaded_file = file_storage("\ufeffDate,Description,Amount\n".encode("utf-8"), "statement.csv")

    assert read_statement_text(uploaded_file, "csv") == "Date,Description,Amount\n"
    assert uploaded_file.stream.tell() == 0
