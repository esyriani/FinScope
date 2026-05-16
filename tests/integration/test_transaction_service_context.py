"""Tests for transaction list service context behavior."""

from werkzeug.datastructures import MultiDict
from sqlalchemy import insert, update

from finance_app.database.tables import (
    accounts as accounts_table,
    settings as settings_table,
    transactions as transactions_table,
)
from finance_app.modules.categories.repository import resolve_category_id
from finance_app.modules.categories.taxonomy import set_transaction_tags
from finance_app.modules.transactions.service import build_transactions_context


def seed_transactions(conn):
    """Seed transactions with categories, sources, tags, and ignored state."""
    account_id = conn.execute(
        insert(accounts_table).values(name="Checking")
    ).inserted_primary_key[0]
    rows = [
        ("2026-01-01", "Metro Grocery", 20.00, "Food", "rule", 0, None, 0, "tx-list-metro"),
        ("2026-01-02", "Cafe Bistro", 12.50, "Food", "manual", 0, "2026-01-05T00:00:00Z", 0, "tx-list-cafe"),
        ("2026-01-03", "Hydro Quebec", 120.00, "Utilities", "ai", 0, None, 0, "tx-list-hydro"),
        ("2026-01-04", "Unknown Shop", 30.00, "UNKNOWN", "unknown", 1, None, 0, "tx-list-unknown"),
        ("2026-01-05", "Payroll", -1000.00, "Income", "rule", 0, None, 0, "tx-list-payroll"),
        ("2026-01-06", "Ignored Store", 999.00, "Food", "rule", 0, None, 1, "tx-list-ignored"),
    ]
    ids = {}
    for tx_date, description, amount, category, source, review, reviewed_at, ignored, fingerprint in rows:
        tx_id = conn.execute(
            insert(transactions_table).values(
                account_id=account_id,
                tx_date=tx_date,
                description=description,
                amount=amount,
                category=category,
                category_id=resolve_category_id(conn, category),
                category_source=source,
                needs_review=review,
                reviewed_at=reviewed_at,
                ignored=ignored,
                fingerprint=fingerprint,
            )
        ).inserted_primary_key[0]
        ids[description] = tx_id

    set_transaction_tags(conn, ids["Metro Grocery"], ["Tax"], source="rule")
    set_transaction_tags(conn, ids["Cafe Bistro"], ["Shared", "Tax"], source="manual")
    conn.execute(
        update(settings_table)
        .where(settings_table.c["key"] == "default_table_page_size")
        .values(value="2")
    )
    conn.commit()
    return ids


def descriptions(context):
    """Return descriptions from a transaction context."""
    return [row["description"] for row in context["transactions"]]


def test_transactions_context_paginates_and_sorts(db_conn):
    """Verify transaction context pagination and stable sorting."""
    seed_transactions(db_conn)

    first_page = build_transactions_context(
        MultiDict(
            [
                ("period", "all"),
                ("sort", "amount"),
                ("direction", "desc"),
                ("page", "1"),
            ]
        )
    )
    second_page = build_transactions_context(
        MultiDict(
            [
                ("period", "all"),
                ("sort", "amount"),
                ("direction", "desc"),
                ("page", "2"),
            ]
        )
    )

    assert first_page["total_count"] == 5
    assert first_page["total_pages"] == 3
    assert first_page["page_start"] == 1
    assert first_page["page_end"] == 2
    assert descriptions(first_page) == ["Hydro Quebec", "Unknown Shop"]
    assert descriptions(second_page) == ["Metro Grocery", "Cafe Bistro"]
    assert second_page["page_start"] == 3
    assert second_page["page_end"] == 4


def test_transactions_context_ignored_filters(db_conn):
    """Verify active, ignored, and all ignored-state filters."""
    seed_transactions(db_conn)

    active = build_transactions_context(MultiDict([("period", "all")]))
    ignored = build_transactions_context(MultiDict([("period", "all"), ("ignored", "ignored")]))
    all_rows = build_transactions_context(MultiDict([("period", "all"), ("ignored", "all")]))

    assert active["selected_ignored"] == "active"
    assert active["total_count"] == 5
    assert ignored["selected_ignored"] == "ignored"
    assert descriptions(ignored) == ["Ignored Store"]
    assert all_rows["selected_ignored"] == "all"
    assert all_rows["total_count"] == 6


def test_transactions_context_category_source_and_review_filters(db_conn):
    """Verify category source, review, and unknown/categorized filters."""
    seed_transactions(db_conn)

    ai_context = build_transactions_context(
        MultiDict([("period", "all"), ("category_source", "ai")])
    )
    manual_context = build_transactions_context(
        MultiDict([("period", "all"), ("category_source", "manual_reviewed")])
    )
    unknown_context = build_transactions_context(
        MultiDict([("period", "all"), ("category_status", "unknown")])
    )
    needs_review_context = build_transactions_context(
        MultiDict([("period", "all"), ("review", "needs_review")])
    )

    assert descriptions(ai_context) == ["Hydro Quebec"]
    assert ai_context["selected_category_source"] == "ai"
    assert ai_context["category_source_filter_options"] == (
        ("", "All sources"),
        ("manual_reviewed", "Manual reviewed"),
        ("rule", "Rule"),
        ("history", "History"),
        ("ai", "AI"),
    )
    assert ai_context["transactions"][0]["category_source_label"] == "AI"
    assert ai_context["transactions"][0]["category_source_badge_class"] == "text-bg-info"
    assert descriptions(manual_context) == ["Cafe Bistro"]
    assert descriptions(unknown_context) == ["Unknown Shop"]
    assert descriptions(needs_review_context) == ["Unknown Shop"]


def test_transactions_context_merchant_filters_and_tag_rendering(db_conn):
    """Verify merchant filters and tag view model fields."""
    seed_transactions(db_conn)

    merchant_context = build_transactions_context(
        MultiDict([("period", "all"), ("merchant_key", "Metro Grocery")])
    )
    category_context = build_transactions_context(
        MultiDict(
            [
                ("period", "all"),
                ("categories", "Food"),
                ("filter_mode", "include"),
                ("sort", "date"),
                ("direction", "asc"),
            ]
        )
    )
    tag_context = build_transactions_context(
        MultiDict([("period", "all"), ("tags", "Shared")])
    )

    metro = merchant_context["transactions"][0]
    assert descriptions(merchant_context) == ["Metro Grocery"]
    assert metro["cleaned_merchant"] == "METRO GROCERY"
    assert metro["canonical_merchant"] == "METRO GROCERY"
    assert metro["tags"] == ["Tax"]
    assert metro["tag_label"] == "Tax"
    assert metro["tag_pills"][0]["name"] == "Tax"
    assert metro["tag_pills"][0]["color"].startswith("#")

    assert category_context["total_count"] == 2
    assert descriptions(category_context) == ["Metro Grocery", "Cafe Bistro"]
    cafe = category_context["transactions"][1]
    assert cafe["tag_label"] == "Shared, Tax"
    assert [tag["name"] for tag in cafe["tag_pills"]] == ["Shared", "Tax"]

    assert tag_context["selected_tags"] == ["Shared"]
    assert descriptions(tag_context) == ["Cafe Bistro"]


def test_transactions_context_merchant_filter_matches_default_aliases(db_conn):
    """Verify merchant filtering preserves default alias matching semantics."""
    account_id = db_conn.execute(
        insert(accounts_table).values(name="Alias checking")
    ).inserted_primary_key[0]
    food_id = resolve_category_id(db_conn, "Food")
    db_conn.execute(
        insert(transactions_table),
        [
            {
                "account_id": account_id,
                "tx_date": "2026-01-01",
                "description": "AMZN MKTP CA*1234",
                "amount": 20.00,
                "category": "Food",
                "category_id": food_id,
                "category_source": "rule",
                "needs_review": 0,
                "ignored": 0,
                "fingerprint": "tx-list-amzn",
            },
            {
                "account_id": account_id,
                "tx_date": "2026-01-02",
                "description": "Amazon Mktplace CA*ABCD",
                "amount": 30.00,
                "category": "Food",
                "category_id": food_id,
                "category_source": "rule",
                "needs_review": 0,
                "ignored": 0,
                "fingerprint": "tx-list-amazon",
            },
            {
                "account_id": account_id,
                "tx_date": "2026-01-03",
                "description": "Local Market",
                "amount": 40.00,
                "category": "Food",
                "category_id": food_id,
                "category_source": "rule",
                "needs_review": 0,
                "ignored": 0,
                "fingerprint": "tx-list-local",
            },
            {
                "account_id": account_id,
                "tx_date": "2026-01-04",
                "description": "AMZN MKTP CA*9999",
                "amount": 50.00,
                "category": "Food",
                "category_id": food_id,
                "category_source": "rule",
                "needs_review": 0,
                "ignored": 1,
                "fingerprint": "tx-list-amzn-ignored",
            },
        ],
    )
    db_conn.commit()

    context = build_transactions_context(
        MultiDict([("period", "all"), ("merchant_key", "Amazon")])
    )

    assert context["total_count"] == 2
    assert descriptions(context) == ["Amazon Mktplace CA*ABCD", "AMZN MKTP CA*1234"]


def test_transactions_context_custom_dates_are_inclusive(db_conn):
    """Verify custom date filters include exact start and end boundaries."""
    account_id = db_conn.execute(
        insert(accounts_table).values(name="Boundary checking")
    ).inserted_primary_key[0]
    food_id = resolve_category_id(db_conn, "Food")
    for tx_date, description, fingerprint in [
        ("2026-03-31", "Before boundary", "tx-list-before-boundary"),
        ("2026-04-01", "Start boundary", "tx-list-start-boundary"),
        ("2026-04-30", "End boundary", "tx-list-end-boundary"),
        ("2026-05-01", "After boundary", "tx-list-after-boundary"),
    ]:
        db_conn.execute(
            insert(transactions_table).values(
                account_id=account_id,
                tx_date=tx_date,
                description=description,
                amount=10.00,
                category="Food",
                category_id=food_id,
                category_source="rule",
                needs_review=0,
                ignored=0,
                fingerprint=fingerprint,
            )
        )
    db_conn.commit()

    context = build_transactions_context(
        MultiDict(
            [
                ("period", "custom"),
                ("date_from", "2026-04-01"),
                ("date_to", "2026-04-30"),
                ("sort", "date"),
                ("direction", "asc"),
            ]
        )
    )

    assert context["total_count"] == 2
    assert descriptions(context) == ["Start boundary", "End boundary"]
