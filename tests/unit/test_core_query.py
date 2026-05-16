"""Tests for shared SQLAlchemy Core query helpers."""

import pytest
from sqlalchemy import column

from finance_app.core.query import (
    CoreFilters,
    parse_page,
    parse_sort_direction,
    resolve_sort,
)


def test_core_filters_collect_conditions_in_order():
    """Verify Core filters collect SQLAlchemy conditions predictably."""
    amount = column("amount")
    category = column("category")
    filters = CoreFilters()

    filters.add(amount > 10)
    filters.add(None)
    filters.add_in(category, ["Food", "", None, "Travel"])

    criteria = filters.criteria()

    assert len(criteria) == 2
    assert str(criteria[0]) == "amount > :amount_1"
    assert "category IN" in str(criteria[1])


def test_core_filters_can_clone_without_sharing_condition_lists():
    """Verify cloned filters keep existing criteria and can diverge safely."""
    amount = column("amount")
    filters = CoreFilters()
    filters.add(amount > 10)

    clone = filters.clone()
    clone.add(amount < 20)

    assert len(filters.criteria()) == 1
    assert len(clone.criteria()) == 2


def test_core_filters_support_excluding_non_empty_values():
    """Verify negative membership filters stay as SQLAlchemy expressions."""
    category = column("category")
    filters = CoreFilters()

    filters.add_in(category, ["Food", ""], include=False)

    assert "category NOT IN" in str(filters.criteria()[0])


@pytest.mark.parametrize(
    ("raw_value", "expected"),
    [
        (None, 1),
        ("", 1),
        ("abc", 1),
        ("-3", 1),
        ("2", 2),
    ],
)
def test_parse_page_normalizes_invalid_values(raw_value, expected):
    """Verify that page parsing always returns a positive integer."""
    assert parse_page(raw_value) == expected


def test_sort_helpers_default_to_allowed_values():
    """Verify that sort helper output stays within explicit allow-lists."""
    allowed = {
        "date": column("tx_date"),
        "amount": column("amount"),
    }

    assert parse_sort_direction("DESC") == "desc"
    assert parse_sort_direction("sideways", default="desc") == "desc"
    assert resolve_sort("merchant", allowed, "date") == ("date", allowed["date"])
    assert resolve_sort("amount", allowed, "date") == ("amount", allowed["amount"])
