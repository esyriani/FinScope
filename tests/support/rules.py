"""Shared rule route test helpers.

Provides small lookup and rendering helpers used by route tests for the rules
and rule audit features. The helpers assume the standard test database schema
and seeded owner settings are available.
"""

from sqlalchemy import text

from tests.support.database import set_owner_setting


def rule_by_id(conn, rule_id):
    """Return a category rule row by id.

    Args:
        conn: Active test database connection.
        rule_id: Rule identifier to fetch.

    Returns:
        A SQLite row for the matching rule, or ``None`` when absent.
    """
    return conn.execute(
        text("""
        SELECT id, keyword, category, amount_min, amount_max, source, ai_approved
        FROM category_rules
        WHERE id = :p0
        """),
        {"p0": rule_id},
    ).fetchone()


def html_fragment_after(body, marker, length=500):
    """Return a short HTML fragment after a marker for scoped assertions.

    Args:
        body: Full HTML response body.
        marker: Text marker that must exist in the body.
        length: Maximum fragment length to return.

    Returns:
        A substring starting at the marker.

    Raises:
        ValueError: If the marker is not found.
    """
    start = body.index(marker)
    return body[start : start + length]


def set_default_table_page_size(conn, size):
    """Set the owner's default table page size for route rendering tests."""
    set_owner_setting(conn, "default_table_page_size", size)


def set_rule_audit_transaction_limit(conn, size):
    """Set the owner's Rule Audit transaction limit for route rendering tests."""
    set_owner_setting(conn, "rule_audit_transaction_limit", size)
