"""Tests for statement text extraction in the upload controller."""

import io

from flask import get_flashed_messages
from werkzeug.datastructures import FileStorage

from finance_app.modules.upload import controller as upload_controller


def file_storage(payload=b"content", filename="statement.csv"):
    """Build a FileStorage object backed by memory."""
    return FileStorage(stream=io.BytesIO(payload), filename=filename)


def test_read_statement_text_extracts_pdf_text(app, monkeypatch):
    """Verify PDF uploads return extracted text when available."""
    uploaded_file = file_storage(b"%PDF-1.4", "statement.pdf")
    monkeypatch.setattr(
        upload_controller,
        "extract_pdf_text",
        lambda file: "Extracted statement text",
    )

    with app.test_request_context("/upload"):
        assert upload_controller.read_statement_text(uploaded_file, "pdf") == "Extracted statement text"
        assert get_flashed_messages() == []


def test_read_statement_text_rejects_image_based_pdf(app, monkeypatch):
    """Verify PDFs with no extractable text return a user-facing message."""
    uploaded_file = file_storage(b"%PDF-1.4", "scanned.pdf")
    monkeypatch.setattr(upload_controller, "extract_pdf_text", lambda file: " \n\t")

    with app.test_request_context("/upload"):
        assert upload_controller.read_statement_text(uploaded_file, "pdf") is None
        assert get_flashed_messages() == [
            "Could not extract text from this PDF. It may be scanned or image-based."
        ]


def test_read_statement_text_rejects_unsupported_file_types(app):
    """Verify unsupported extensions return no text and flash a message."""
    uploaded_file = file_storage(b"plain text", "statement.txt")

    with app.test_request_context("/upload"):
        assert upload_controller.read_statement_text(uploaded_file, "txt") is None
        assert get_flashed_messages() == ["Unsupported file type."]


def test_read_statement_text_decodes_csv_with_utf8_sig(app):
    """Verify CSV uploads are decoded and stream position is restored."""
    uploaded_file = file_storage("\ufeffDate,Description,Amount\n".encode("utf-8"), "statement.csv")

    with app.test_request_context("/upload"):
        assert upload_controller.read_statement_text(uploaded_file, "csv") == "Date,Description,Amount\n"
        assert uploaded_file.stream.tell() == 0
