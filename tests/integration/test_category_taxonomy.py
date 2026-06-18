"""Tests for category taxonomy behavior."""

from unittest.mock import patch

from sqlalchemy import delete, func, select, text

from finance_app.core import constants
from finance_app.database.tables import (
    categories as categories_table,
)
from finance_app.database.tables import (
    tags as tags_table,
)
from finance_app.modules.categories import llm as llm_module
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


def test_taxonomy_categories_tags_and_builtins_are_persisted(core_conn):
    """Verify taxonomy seed rows and built-in categories are persisted."""
    categories = {
        row["name"]: row
        for row in core_conn.execute(
            select(
                categories_table.c.name,
                categories_table.c.builtin_key,
                categories_table.c.description,
                categories_table.c.instruction,
            )
        ).mappings()
    }
    tags = get_tag_options(core_conn)
    tag_rows = {row["name"]: row for row in get_tag_option_rows(core_conn)}
    persisted_tag_rows = {
        row["name"]: row for row in core_conn.execute(select(tags_table.c.name, tags_table.c.builtin_key)).mappings()
    }

    assert "Income" in categories
    assert "Rental" in categories
    assert "UNKNOWN" in categories
    assert "Reimbursement" in categories
    assert "Transfers" in categories
    assert categories["Income"]["builtin_key"] == "income"
    assert categories["Rental"]["builtin_key"] == "rental"
    assert categories["UNKNOWN"]["builtin_key"] == "unknown"
    assert categories["Reimbursement"]["builtin_key"] == "reimbursement"
    assert categories["Transfers"]["builtin_key"] == "transfers"
    assert "salary" in categories["Income"]["instruction"].casefold()
    assert "Reimbursable" in tags
    assert "Tax" in tags
    assert "Government" in tags
    assert persisted_tag_rows["Reimbursable"]["builtin_key"] == "reimbursable"
    assert persisted_tag_rows["Tax"]["builtin_key"] == "tax"
    assert tag_rows["Reimbursable"]["color"].startswith("#")
    assert tag_rows["Government"]["color"].startswith("#")


def test_core_constants_do_not_define_taxonomy():
    """Verify core constants do not define taxonomy."""
    assert not hasattr(constants, "CATEGORY_DEFINITIONS")
    assert not hasattr(constants, "ALLOWED_CATEGORIES")


def test_category_options_seed_empty_db_from_taxonomy_file(core_conn):
    """Verify category options seed an empty taxonomy through Core writes."""
    core_conn.execute(delete(tags_table))
    core_conn.execute(delete(categories_table))

    category_options = get_category_options(core_conn)

    assert "Income" in category_options
    assert "UNKNOWN" in category_options
    assert "Reimbursement" in category_options
    assert "Transfers" in category_options
    assert core_conn.execute(select(func.count()).select_from(categories_table)).scalar_one() > 1


def test_taxonomy_options_sort_user_values_before_builtins(core_conn):
    """Verify user taxonomy values sort alphabetically before bundled values."""
    core_conn.execute(text("""
        INSERT INTO categories (name)
        VALUES ('Aardvark'), ('Zulu custom')
        """))
    core_conn.execute(text("""
        INSERT INTO tags (name, color)
        VALUES ('Audit', '#123abc'), ('Zulu tag', '#456def')
        """))

    category_options = get_category_options(core_conn)
    tag_options = get_tag_options(core_conn)
    tag_option_rows = get_tag_option_rows(core_conn)
    taxonomy_categories = fetch_category_rows(core_conn)
    taxonomy_tags = fetch_tag_rows(core_conn)

    assert category_options[-5:] == ["Income", "Reimbursement", "Rental", "Transfers", "UNKNOWN"]
    assert [row["name"] for row in taxonomy_categories][-5:] == [
        "Income",
        "Reimbursement",
        "Rental",
        "Transfers",
        "UNKNOWN",
    ]
    assert tag_options[0] == "Audit"
    assert tag_options[-2:] == ["Reimbursable", "Tax"]
    assert [row["name"] for row in tag_option_rows[-2:]] == ["Reimbursable", "Tax"]
    assert [row["name"] for row in taxonomy_tags[-2:]] == ["Reimbursable", "Tax"]
    assert not any(row["is_builtin"] for row in taxonomy_tags[:-2])
    assert all(row["is_builtin"] for row in taxonomy_tags[-2:])


def test_normalize_category_requires_supplied_options():
    """Verify normalize category requires supplied options."""
    assert normalize_category("Income") == "UNKNOWN"
    assert normalize_category("income", ["Income"]) == "Income"


def test_rules_persist_tags(core_conn):
    """Verify rules persist tags."""
    rule_id = save_category_rule(
        core_conn,
        "HYDRO-QUEBEC",
        "Utilities",
        tags=["Tax", "Government"],
    )

    rule = next(rule for rule in get_category_rules(core_conn) if rule["id"] == rule_id)
    assert rule["category"] == "Utilities"
    assert rule["tags"] == ["Government", "Tax"]


def test_llm_fallback_path_initializes_taxonomy_options(core_conn):
    """Verify llm fallback path initializes taxonomy options."""
    core_conn.execute(delete(tags_table))
    core_conn.execute(delete(categories_table))
    seed_category_taxonomy(core_conn)
    transactions = [
        {
            "description": "UNSEEN MERCHANT",
            "merchant_key": "UNSEEN MERCHANT",
            "amount": 12.34,
            "category": "UNKNOWN",
        }
    ]

    with patch("finance_app.modules.categories.service.request_llm_categories", return_value=[]) as request_llm:
        classify_unknowns_with_llm(core_conn, transactions, [], "UNKNOWN")

    assert transactions[0]["category"] == "UNKNOWN"
    assert request_llm.called
    _, _, category_options, tag_options, *_ = request_llm.call_args.args
    assert "Income" in category_options
    assert "Tax" in tag_options


def test_service_llm_request_injection_does_not_replace_global_requester(core_conn):
    """Verify LLM request injection does not mutate module-level requester state."""
    transactions = [
        {
            "description": "UNSEEN MERCHANT",
            "merchant_key": "UNSEEN MERCHANT",
            "amount": 12.34,
            "category": "UNKNOWN",
        }
    ]
    original_requester = llm_module.request_llm_categories
    calls = []

    def request_for_test(*args):
        """Capture the injected request call and return no LLM results."""
        calls.append(args)
        return []

    classify_unknowns_with_llm(
        core_conn,
        transactions,
        [],
        "UNKNOWN",
        request_categories=request_for_test,
    )

    assert calls
    assert llm_module.request_llm_categories is original_requester
    assert transactions[0]["category"] == "UNKNOWN"
