"""Service-level context tests for the home page."""

from sqlalchemy import text
from finance_app.modules.home.service import build_home_context
from tests.support.context_services import seed_reporting_data


def test_home_context_summarizes_seeded_year_to_date_data(core_conn):
    """Verify home summary values against realistic seeded statement data."""
    seed_reporting_data(core_conn)

    context = build_home_context()

    assert context["statement_count"] == 1
    assert context["latest_statement"]["filename"] == "latest.csv"
    assert context["latest_statement"]["account_name"] == "Personal Checking"
    assert context["overview"]["transaction_count"] == 8
    assert context["overview"]["uncategorized_count"] == 2
    assert context["overview"]["latest_tx_date"] == "2026-05-04"
    assert context["ytd_spending"] == 540.00
    assert context["ytd_income"] == 2200.00
    assert context["ytd_cashflow"] == 1660.00
    assert context["financial_pulse"]["title"] == "Positive cash flow"
    assert context["financial_pulse"]["state"] == "surplus"
    assert [row["label"] for row in context["pulse_kpis"]] == [
        "YTD cash flow",
        "YTD spending",
        "Open attention",
    ]
    assert context["attention_counts"]["unknown_transactions"] == 3
    assert context["attention_counts"]["review_groups"] == 1
    assert "Unknown transactions" in [item["title"] for item in context["attention_items"]]
    assert context["primary_action"]["label"] == "Review unknown transactions"
    assert len(context["suggested_actions"]) <= 4
    assert "Review unknown transactions" in [item["label"] for item in context["suggested_actions"]]
    assert "Open dashboard" not in [item["label"] for item in context["suggested_actions"]]
    assert [(row["category"], row["total"]) for row in context["top_categories"]] == [
        ("Food", 340.00),
        ("Utilities", 120.00),
    ]
    assert "Top YTD category" in [item["label"] for item in context["quick_insights"]]


def test_home_context_surfaces_command_center_activity(core_conn):
    """Verify Home exposes failed imports, suggested rules, and recent actions."""
    seed_reporting_data(core_conn)
    statement_type_id = core_conn.execute(text("""
        SELECT id
        FROM statement_types
        WHERE active = 1
        ORDER BY id
        LIMIT 1
        """)).fetchone()._mapping["id"]
    core_conn.execute(text("""
        INSERT INTO statements (
            statement_type_id,
            filename,
            checksum,
            raw_text,
            import_status,
            import_error,
            uploaded_at
        )
        VALUES (
            :p0, 'failed-home.csv', 'failed-home-checksum', 'raw',
            'failed', 'Parser failed', '2026-05-07T10:00:00Z'
        )
        """), {"p0": statement_type_id})
    core_conn.execute(text("""
        INSERT INTO category_rules (keyword, category, source, ai_approved, created_at)
        VALUES ('Suggested Home Merchant', 'Food', 'automatic', 0, '2026-05-06T10:00:00Z')
        """))
    core_conn.execute(text("""
        INSERT INTO transactions (
            tx_date,
            description,
            amount,
            category,
            category_source,
            needs_review,
            reviewed_at,
            categorized_at,
            fingerprint
        )
        VALUES (:p0, :p1, :p2, :p3, :p4, :p5, :p6, :p7, :p8)
        """), [{"p0": "2026-05-06", "p1": "Reviewed Home Cafe", "p2": 22.00, "p3": "Food", "p4": "manual", "p5": 0, "p6": "2026-05-06T12:00:00Z", "p7": "2026-05-06T12:00:00Z", "p8": "home-reviewed-activity"}, {"p0": "2026-05-07", "p1": "Categorized Home Market", "p2": 44.00, "p3": "Food", "p4": "rule", "p5": 0, "p6": None, "p7": "2026-05-07T12:00:00Z", "p8": "home-categorized-activity"}])
    core_conn.commit()

    context = build_home_context()
    attention_titles = [item["title"] for item in context["attention_items"]]
    activity_labels = [item["label"] for item in context["recent_activity"]]

    assert context["attention_counts"]["failed_imports"] == 1
    assert context["attention_counts"]["rule_suggestions"] == 1
    assert "Failed imports" in attention_titles
    assert "Rule suggestions" in attention_titles
    assert attention_titles.index("Unknown transactions") < attention_titles.index("Rule suggestions")
    assert attention_titles.index("Rule suggestions") < attention_titles.index("Failed imports")
    assert len(context["recent_activity"]) <= 5
    assert "Imported statement" in activity_labels
    assert "Reviewed transaction" in activity_labels
    assert "Categorized transaction" in activity_labels
    assert "Created rule" in activity_labels


