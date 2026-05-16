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


def test_yaml_categories_and_tags_are_persisted(db_conn):
    """Verify yaml categories and tags are persisted."""
    categories = {
        row["name"]: row
        for row in db_conn.execute(
            select(
                categories_table.c.name,
                categories_table.c.description,
                categories_table.c.instruction,
            )
        ).mappings()
    }
    tags = get_tag_options(db_conn)
    tag_rows = {row["name"]: row for row in get_tag_option_rows(db_conn)}

    assert "Income" in categories
    assert "UNKNOWN" in categories
    assert "salary" in categories["Income"]["instruction"].casefold()
    assert "Reimbursable" in tags
    assert "Government" in tags
    assert tag_rows["Reimbursable"]["color"].startswith("#")
    assert tag_rows["Government"]["color"].startswith("#")


def test_core_constants_do_not_define_taxonomy():
    """Verify core constants do not define taxonomy."""
    assert not hasattr(constants, "CATEGORY_DEFINITIONS")
    assert not hasattr(constants, "ALLOWED_CATEGORIES")


def test_category_options_seed_empty_db_from_yaml(db_conn):
    """Verify category options seed an empty taxonomy through Core writes."""
    db_conn.execute(delete(tags_table))
    db_conn.execute(delete(categories_table))

    category_options = get_category_options(db_conn)

    assert "Income" in category_options
    assert "UNKNOWN" in category_options
    assert db_conn.execute(select(func.count()).select_from(categories_table)).scalar_one() > 1


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
