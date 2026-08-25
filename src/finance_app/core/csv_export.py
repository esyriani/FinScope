"""Shared backend CSV export helpers.

Centralizes spreadsheet-safety handling for server-generated CSV downloads.
Feature modules should sanitize every data cell before handing rows to
``csv.writer`` or ``csv.DictWriter`` so spreadsheet applications cannot treat
user-controlled text as formulas.
"""

CSV_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def csv_export_value(value: object) -> object:
    """Return a CSV-safe cell value that neutralizes spreadsheet formulas."""
    text = "" if value is None else str(value)
    return f"'{text}" if text.startswith(CSV_FORMULA_PREFIXES) else value
