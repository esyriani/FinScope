"""SQLAlchemy Core tests for rules listing context queries."""

from sqlalchemy import text
from flask import request

from finance_app.modules.categories.taxonomy import set_rule_tags
from finance_app.modules.merchants.repository import get_or_create_merchant_for_name
from finance_app.modules.rules.listing import build_rules_context


def insert_listing_rule(
    conn,
    keyword,
    category,
    source="manual",
    ai_approved=0,
    amount_min=None,
    amount_max=None,
    merchant_id=None,
    tags=None,
):
    """Insert a category rule and optional tags for listing tests."""
    rule_id = conn.execute(text("""
        INSERT INTO category_rules (
            merchant_id,
            keyword,
            category,
            source,
            ai_approved,
            amount_min,
            amount_max
        )
        VALUES (:p0, :p1, :p2, :p3, :p4, :p5, :p6)
        """), {"p0": merchant_id, "p1": keyword, "p2": category, "p3": source, "p4": ai_approved, "p5": amount_min, "p6": amount_max}).lastrowid
    set_rule_tags(conn, rule_id, tags or [])
    conn.commit()
    return rule_id


def test_rules_context_filters_with_core_queries(app, core_conn):
    """Verify the rules context applies tag, source, approval, and category filters."""
    matching_id = insert_listing_rule(
        core_conn,
        "AUTO TAX FOOD",
        "Food",
        source="automatic",
        ai_approved=0,
        tags=["Tax"],
    )
    insert_listing_rule(
        core_conn,
        "AUTO APPROVED FOOD",
        "Food",
        source="automatic",
        ai_approved=1,
        tags=["Tax"],
    )
    insert_listing_rule(
        core_conn,
        "MANUAL TAX FOOD",
        "Food",
        source="manual",
        tags=["Tax"],
    )

    with app.test_request_context("/rules?category=Food&source=automatic&approval=suggested&tags=Tax"):
        context = build_rules_context(request.args)

    assert [rule["id"] for rule in context["rules"]] == [matching_id]
    assert context["rules"][0]["requires_approval"] is True
    assert context["rules"][0]["tag_label"] == "Tax"
    assert context["selected_category"] == "Food"
    assert context["selected_categories"] == ["Food"]
    assert context["selected_source"] == "automatic"
    assert context["selected_approval"] == "suggested"
    assert context["selected_tags"] == ["Tax"]


def test_rules_context_searches_merchant_and_amount_fields_with_core(app, core_conn):
    """Verify Core listing search includes merchant labels and amount fields."""
    merchant = get_or_create_merchant_for_name(core_conn, "Core Market")
    insert_listing_rule(
        core_conn,
        "HIDDEN KEYWORD",
        "Utilities",
        amount_min=123.45,
        merchant_id=merchant["id"],
    )
    insert_listing_rule(core_conn, "OTHER STORE", "Food")

    with app.test_request_context("/rules?search=core market"):
        merchant_context = build_rules_context(request.args)
    with app.test_request_context("/rules?search=123.45"):
        amount_context = build_rules_context(request.args)

    assert [rule["keyword"] for rule in merchant_context["rules"]] == ["HIDDEN KEYWORD"]
    assert [rule["keyword"] for rule in amount_context["rules"]] == ["HIDDEN KEYWORD"]
