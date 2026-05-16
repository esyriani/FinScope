"""Tests for review query and presenter behavior."""

from sqlalchemy import insert

from finance_app.database.tables import transactions as transactions_table
from finance_app.modules.categories.repository import resolve_category_id
from finance_app.modules.review.presenter import (
    active_ungroup_keys,
    attach_review_row_urls,
    review_display_rows,
    review_group_rows,
    review_groups,
    review_summary,
    selected_ungroup_keys,
    sort_review_groups,
)
from finance_app.modules.review.queries import review_candidate_rows


def seed_review_candidates(conn):
    """Seed review candidates that exercise grouping and filtering."""
    rows = [
        ("2026-01-01", "Metro Grocery #111", 12.00, "UNKNOWN", "unknown", None, 1, 0, "review-presenter-1"),
        ("2026-01-05", "Metro Grocery #222", 20.00, "UNKNOWN", "unknown", None, 1, 0, "review-presenter-2"),
        ("2026-01-03", "Hydro Quebec", 90.00, "Utilities", "ai", 0.72, 1, 0, "review-presenter-3"),
        ("2026-01-04", "Ignored Unknown", 50.00, "UNKNOWN", "unknown", None, 1, 1, "review-presenter-ignored"),
        ("2026-01-06", "Categorized Done", 15.00, "Food", "rule", 1.0, 0, 0, "review-presenter-done"),
    ]
    for tx_date, description, amount, category, source, confidence, needs_review, ignored, fingerprint in rows:
        conn.execute(
            insert(transactions_table).values(
                tx_date=tx_date,
                description=description,
                amount=amount,
                category=category,
                category_id=resolve_category_id(conn, category),
                category_source=source,
                category_confidence=confidence,
                needs_review=needs_review,
                ignored=ignored,
                fingerprint=fingerprint,
            )
        )
    conn.commit()


def test_review_candidate_rows_exclude_ignored_and_done_transactions(db_conn):
    """Verify review query returns only active rows needing review or unknown."""
    seed_review_candidates(db_conn)

    rows = review_candidate_rows(db_conn, "UNKNOWN")

    assert [row["description"] for row in rows] == [
        "Metro Grocery #222",
        "Hydro Quebec",
        "Metro Grocery #111",
    ]


def test_review_groups_aggregate_by_normalized_merchant(db_conn):
    """Verify review groups aggregate normalized merchant variants."""
    seed_review_candidates(db_conn)

    groups = review_groups(db_conn, "UNKNOWN")
    by_key = {group["merchant_key"]: group for group in groups}

    assert set(by_key) == {"METRO GROCERY", "HYDRO QUEBEC"}
    metro = by_key["METRO GROCERY"]
    assert metro["count"] == 2
    assert metro["unknown_count"] == 2
    assert metro["review_count"] == 2
    assert metro["total_amount"] == 32.00
    assert metro["absolute_amount"] == 32.00
    assert metro["first_date"] == "2026-01-01"
    assert metro["last_date"] == "2026-01-05"
    assert metro["categories"] == ["UNKNOWN"]
    assert [row["description"] for row in metro["examples"]] == [
        "Metro Grocery #222",
        "Metro Grocery #111",
    ]
    assert [row["description"] for row in metro["transactions"]] == [
        "Metro Grocery #222",
        "Metro Grocery #111",
    ]


def test_review_display_rows_ungroup_and_attach_urls(app, db_conn):
    """Verify grouped rows can be split into transaction-level display rows."""
    seed_review_candidates(db_conn)
    groups = review_groups(db_conn, "UNKNOWN")
    ungrouped = selected_ungroup_keys(["Metro Grocery #999", "METRO GROCERY"])
    active = active_ungroup_keys(ungrouped, groups)
    rows = review_display_rows(groups, active, "UNKNOWN")

    with app.test_request_context("/review"):
        attach_review_row_urls(rows, page=1, ungrouped_keys=active, sort="count", direction="desc")

    metro_rows = [row for row in rows if row["merchant_key"] == "METRO GROCERY"]
    hydro_row = next(row for row in rows if row["merchant_key"] == "HYDRO QUEBEC")
    assert active == ["METRO GROCERY"]
    assert len(metro_rows) == 2
    assert all(row["is_ungrouped"] for row in metro_rows)
    assert [row["transaction_id"] for row in metro_rows]
    assert all(len(row["transactions"]) == 1 for row in metro_rows)
    assert all(row["regroup_url"] for row in metro_rows)
    assert hydro_row["is_ungrouped"] is False
    assert hydro_row["display_label"] == "HYDRO QUEBEC"
    assert hydro_row["category_sources"] == ["AI"]
    assert hydro_row["category_source_badges"] == [
        {"label": "AI", "class": "text-bg-info"}
    ]
    assert hydro_row["transactions"][0]["description"] == "Hydro Quebec"
    assert hydro_row["transactions"][0]["category_source_label"] == "AI"
    assert hydro_row["transactions"][0]["category_confidence_label"] == "72%"
    assert hydro_row["ungroup_url"] == ""


def test_review_sort_behavior_and_summary_counts(db_conn):
    """Verify review sort behavior and summary totals."""
    seed_review_candidates(db_conn)
    groups = review_groups(db_conn, "UNKNOWN")

    sort_review_groups(groups, "merchant", "asc")
    assert [group["merchant_key"] for group in groups] == ["HYDRO QUEBEC", "METRO GROCERY"]

    sort_review_groups(groups, "count", "desc")
    assert [group["merchant_key"] for group in groups] == ["METRO GROCERY", "HYDRO QUEBEC"]

    summary = review_summary(groups)
    assert summary == {
        "group_count": 2,
        "transaction_count": 3,
        "largest_group_count": 2,
        "largest_group_key": "METRO GROCERY",
        "review_amount": 122.00,
    }


def test_review_group_rows_returns_rows_for_one_normalized_merchant(db_conn):
    """Verify review group row lookup uses normalized merchant keys."""
    seed_review_candidates(db_conn)

    rows = review_group_rows(db_conn, "METRO GROCERY", "UNKNOWN")

    assert [row["description"] for row in rows] == [
        "Metro Grocery #222",
        "Metro Grocery #111",
    ]
