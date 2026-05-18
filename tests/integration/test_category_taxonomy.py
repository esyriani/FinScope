"""Tests for category taxonomy behavior."""

from unittest.mock import patch

from sqlalchemy import delete, select, func

from finance_app.core import constants
from finance_app.database.tables import (
    categories as categories_table,
    tags as tags_table,
)
from finance_app.modules.categories.service import (
    classify_unknowns_with_llm,
    get_category_options,
    get_category_rules,
    normalize_category,
    save_category_rule,
)
from finance_app.modules.categories.taxonomy import (
    get_tag_option_rows,
    get_tag_options,
    seed_category_taxonomy,
)
from finance_app.modules.taxonomy_admin.service import fetch_category_rows, fetch_tag_rows


def test_taxonomy_categories_tags_and_builtins_are_persisted(db_conn):
    """Verify taxonomy seed rows and built-in categories are persisted."""
    categories = {
        row["name"]: row
        for row in db_conn.execute(
            select(
                categories_table.c.name,
                categories_table.c.builtin_key,
                categories_table.c.description,
                categories_table.c.instruction,
            )
        ).mappings()
    }
    tags = get_tag_options(db_conn)
    tag_rows = {row["name"]: row for row in get_tag_option_rows(db_conn)}

    assert "Income" in categories
    assert "UNKNOWN" in categories
    assert "Transfers" in categories
    assert categories["UNKNOWN"]["builtin_key"] == "unknown"
    assert categories["Transfers"]["builtin_key"] == "transfers"
    assert categories["Income"]["builtin_key"] is None
    assert "salary" in categories["Income"]["instruction"].casefold()
    assert "Reimbursable" in tags
    assert "Government" in tags
    assert tag_rows["Reimbursable"]["color"].startswith("#")
    assert tag_rows["Government"]["color"].startswith("#")


def test_core_constants_do_not_define_taxonomy():
    """Verify core constants do not define taxonomy."""
    assert not hasattr(constants, "CATEGORY_DEFINITIONS")
    assert not hasattr(constants, "ALLOWED_CATEGORIES")


def test_category_options_seed_empty_db_from_taxonomy_file(db_conn):
    """Verify category options seed an empty taxonomy through Core writes."""
    db_conn.execute(delete(tags_table))
    db_conn.execute(delete(categories_table))

    category_options = get_category_options(db_conn)

    assert "Income" in category_options
    assert "UNKNOWN" in category_options
    assert "Transfers" in category_options
    assert db_conn.execute(select(func.count()).select_from(categories_table)).scalar_one() > 1


def test_taxonomy_options_sort_user_values_before_builtins(db_conn):
    """Verify user taxonomy values sort alphabetically before bundled values."""
    db_conn.execute(
        """
        INSERT INTO categories (name)
        VALUES ('Aardvark'), ('Zulu custom')
        """
    )
    db_conn.execute(
        """
        INSERT INTO tags (name, color)
        VALUES ('Audit', '#123abc'), ('Zulu tag', '#456def')
        """
    )

    category_options = get_category_options(db_conn)
    tag_options = get_tag_options(db_conn)
    tag_option_rows = get_tag_option_rows(db_conn)
    taxonomy_categories = fetch_category_rows(db_conn)
    taxonomy_tags = fetch_tag_rows(db_conn)

    assert category_options[-2:] == ["Transfers", "UNKNOWN"]
    assert [row["name"] for row in taxonomy_categories][-2:] == ["Transfers", "UNKNOWN"]
    assert tag_options[:2] == ["Audit", "Zulu tag"]
    assert [row["name"] for row in tag_option_rows[:2]] == ["Audit", "Zulu tag"]
    assert [row["name"] for row in taxonomy_tags[:2]] == ["Audit", "Zulu tag"]
    assert all(row["is_builtin"] for row in taxonomy_tags[2:])


def test_normalize_category_requires_supplied_options():
    """Verify normalize category requires supplied options."""
    assert normalize_category("Income") == "UNKNOWN"
    assert normalize_category("income", ["Income"]) == "Income"


def test_rules_persist_tags(db_conn):
    """Verify rules persist tags."""
    rule_id = save_category_rule(
        db_conn,
        "HYDRO-QUEBEC",
        "Utilities",
        tags=["Tax", "Government"],
    )

    rule = next(rule for rule in get_category_rules(db_conn) if rule["id"] == rule_id)
    assert rule["category"] == "Utilities"
    assert rule["tags"] == ["Government", "Tax"]


def test_llm_fallback_path_initializes_taxonomy_options(db_conn):
    """Verify llm fallback path initializes taxonomy options."""
    db_conn.execute(delete(tags_table))
    db_conn.execute(delete(categories_table))
    seed_category_taxonomy(db_conn)
    transactions = [
        {
            "description": "UNSEEN MERCHANT",
            "merchant_key": "UNSEEN MERCHANT",
            "amount": 12.34,
            "category": "UNKNOWN",
        }
    ]

    with patch("finance_app.modules.categories.service.request_llm_categories", return_value=[]) as request_llm:
        classify_unknowns_with_llm(db_conn, transactions, [], "UNKNOWN")

    assert transactions[0]["category"] == "UNKNOWN"
    assert request_llm.called
    _, _, category_options, tag_options, *_ = request_llm.call_args.args
    assert "Income" in category_options
    assert "Tax" in tag_options
