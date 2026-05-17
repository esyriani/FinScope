"""Service-level context tests for dashboard, comparison, and home pages."""

from datetime import date as real_date, timedelta

from werkzeug.datastructures import MultiDict

from finance_app.modules.categories.taxonomy import set_transaction_tags
from finance_app.modules.comparison import service as comparison_service
from finance_app.modules.dashboard.service import build_dashboard_context
from finance_app.modules.home.service import build_home_context


class FixedDate(real_date):
    """Fixed replacement for date.today in comparison service tests."""

    @classmethod
    def today(cls):
        """Return a deterministic current date."""
        return cls(2026, 5, 9)


def seed_reporting_data(conn):
    """Seed realistic accounts, statements, and transactions for context tests."""
    account_id = conn.execute(
        """
        INSERT INTO accounts (name)
        VALUES ('Personal Checking')
        """
    ).lastrowid
    statement_type_id = conn.execute(
        """
        SELECT id
        FROM statement_types
        WHERE parser_type = 'bank_account'
        LIMIT 1
        """
    ).fetchone()["id"]
    conn.execute(
        """
        INSERT INTO statements (account_id, statement_type_id, filename, checksum, raw_text, uploaded_at)
        VALUES (?, ?, 'latest.csv', 'latest-checksum', 'raw', '2026-05-01T12:00:00Z')
        """,
        (account_id, statement_type_id),
    )
    rows = [
        ("2025-01-05", "Metro Grocery", 80.00, "Food", 0, "rule", 0, "expense", "seed-2025-food"),
        ("2025-02-10", "Hydro Quebec", 90.00, "Utilities", 0, "rule", 0, "expense", "seed-2025-utilities"),
        ("2025-05-02", "Metro Grocery", 60.00, "Food", 0, "rule", 0, "expense", "seed-2025-may-food"),
        ("2025-05-03", "Unknown Shop", 30.00, "UNKNOWN", 1, "unknown", 0, "expense", "seed-2025-unknown"),
        ("2025-05-04", "Payroll", -900.00, "Income", 0, "rule", 0, "income", "seed-2025-income"),
        ("2026-01-05", "Metro Grocery", 100.00, "Food", 0, "rule", 0, "expense", "seed-2026-food-jan"),
        ("2026-01-06", "Cafe Bistro", 40.00, "Food", 0, "manual", 0, "expense", "seed-2026-food-cafe"),
        ("2026-01-07", "Payroll", -1000.00, "Income", 0, "rule", 0, "income", "seed-2026-income-jan"),
        ("2026-02-10", "Hydro Quebec", 120.00, "Utilities", 0, "rule", 0, "expense", "seed-2026-utilities"),
        ("2026-02-11", "Unknown Shop", 30.00, "UNKNOWN", 1, "unknown", 0, "expense", "seed-2026-unknown"),
        ("2026-05-02", "Metro Grocery", 200.00, "Food", 0, "rule", 0, "expense", "seed-2026-may-food"),
        ("2026-05-03", "Unknown Shop", 50.00, "UNKNOWN", 1, "unknown", 0, "expense", "seed-2026-may-unknown"),
        ("2026-05-04", "Payroll", -1200.00, "Income", 0, "rule", 0, "income", "seed-2026-may-income"),
        ("2026-02-12", "Ignored Store", 999.00, "Food", 0, "rule", 1, "expense", "seed-ignored"),
    ]
    conn.executemany(
        """
        INSERT INTO transactions (
            tx_date,
            description,
            amount,
            category,
            needs_review,
            category_source,
            ignored,
            transaction_kind,
            fingerprint
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    tag_assignments = (
        ("seed-2025-may-food", ["Tax"]),
        ("seed-2026-food-jan", ["Tax"]),
        ("seed-2026-food-cafe", ["Shared"]),
        ("seed-2026-utilities", ["Government"]),
        ("seed-2026-may-food", ["Tax"]),
    )
    for fingerprint, tags in tag_assignments:
        transaction_id = conn.execute(
            """
            SELECT id
            FROM transactions
            WHERE fingerprint = ?
            """,
            (fingerprint,),
        ).fetchone()["id"]
        set_transaction_tags(conn, transaction_id, tags, source="rule")
    conn.executemany(
        """
        INSERT INTO transactions (
            tx_date,
            description,
            amount,
            category,
            needs_review,
            category_source,
            ignored,
            transaction_kind,
            fingerprint
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                "2026-01-08",
                "Savings transfer",
                500.00,
                "Transfers",
                0,
                "rule",
                0,
                "transfer",
                "seed-2026-transfer-jan",
            ),
            (
                "2026-05-05",
                "Card payment",
                700.00,
                "Transfers",
                0,
                "rule",
                0,
                "payment",
                "seed-2026-transfer-may",
            ),
        ],
    )
    conn.execute(
        """
        UPDATE settings
        SET value = '2'
        WHERE key = 'home_top_category_limit'
        """
    )
    conn.execute(
        """
        UPDATE settings
        SET value = '2'
        WHERE key = 'merchant_table_limit'
        """
    )
    conn.commit()


def category_totals(context):
    """Return dashboard category totals by category label."""
    return {
        row["category"]: row["total"]
        for row in context["category_rows"]
    }


def merchant_totals(context):
    """Return dashboard merchant totals by merchant label."""
    return {
        row["merchant"]: row["total"]
        for row in context["merchant_rows"]
    }


def quick_view_count(context, value):
    """Return a dashboard quick-view count by option value."""
    option = next(
        row for row in context["quick_view_options"]
        if row["value"] == value
    )
    return option.get("count")


def seed_dashboard_spending_only(conn):
    """Seed a dashboard range with spending but no income."""
    conn.execute(
        """
        INSERT INTO transactions (tx_date, description, amount, category, category_source, fingerprint)
        VALUES ('2026-06-01', 'Coffee Stand', 25.00, 'Food', 'rule', 'dashboard-spending-only')
        """
    )
    conn.commit()


def seed_dashboard_unknown_only(conn):
    """Seed a dashboard range where all transactions are UNKNOWN."""
    rows = [
        ("2026-07-01", "Unknown Shop", 20.00, "dashboard-unknown-1"),
        ("2026-07-02", "Unknown Cafe", 40.00, "dashboard-unknown-2"),
    ]
    conn.executemany(
        """
        INSERT INTO transactions (
            tx_date,
            description,
            amount,
            category,
            needs_review,
            category_source,
            fingerprint
        )
        VALUES (?, ?, ?, 'UNKNOWN', 1, 'unknown', ?)
        """,
        rows,
    )
    conn.commit()


def seed_reimbursable_dashboard_data(conn):
    """Seed tagged expenses and reimbursement credits for dashboard cash flow."""
    rows = [
        ("2026-03-05", "Work hotel", 300.00, "Travel", "expense", "dashboard-reimbursable-expense"),
        (
            "2026-03-20",
            "Employer reimbursement",
            -250.00,
            "Transfers",
            "transfer",
            "dashboard-reimbursable-credit",
        ),
    ]
    conn.executemany(
        """
        INSERT INTO transactions (
            tx_date,
            description,
            amount,
            category,
            transaction_kind,
            needs_review,
            category_source,
            ignored,
            fingerprint
        )
        VALUES (?, ?, ?, ?, ?, 0, 'manual', 0, ?)
        """,
        rows,
    )
    for fingerprint in ("dashboard-reimbursable-expense", "dashboard-reimbursable-credit"):
        transaction_id = conn.execute(
            """
            SELECT id
            FROM transactions
            WHERE fingerprint = ?
            """,
            (fingerprint,),
        ).fetchone()["id"]
        set_transaction_tags(conn, transaction_id, ["Reimbursable"], source="manual")
    conn.commit()


def seed_reimbursable_comparison_data(conn):
    """Seed period-comparison data with reimbursed transfer credits."""
    rows = [
        ("2025-05-06", "Prior work hotel", 100.00, "Travel", "expense", "comparison-reimbursable-prior-expense"),
        (
            "2025-05-07",
            "Prior employer reimbursement",
            -80.00,
            "Transfers",
            "transfer",
            "comparison-reimbursable-prior-credit",
        ),
        ("2026-05-06", "Current work hotel", 200.00, "Travel", "expense", "comparison-reimbursable-current-expense"),
        (
            "2026-05-07",
            "Current employer reimbursement",
            -150.00,
            "Transfers",
            "transfer",
            "comparison-reimbursable-current-credit",
        ),
    ]
    conn.executemany(
        """
        INSERT INTO transactions (
            tx_date,
            description,
            amount,
            category,
            transaction_kind,
            needs_review,
            category_source,
            ignored,
            fingerprint
        )
        VALUES (?, ?, ?, ?, ?, 0, 'manual', 0, ?)
        """,
        rows,
    )
    for fingerprint in (
        "comparison-reimbursable-prior-expense",
        "comparison-reimbursable-prior-credit",
        "comparison-reimbursable-current-expense",
        "comparison-reimbursable-current-credit",
    ):
        transaction_id = conn.execute(
            """
            SELECT id
            FROM transactions
            WHERE fingerprint = ?
            """,
            (fingerprint,),
        ).fetchone()["id"]
        set_transaction_tags(conn, transaction_id, ["Reimbursable"], source="manual")
    conn.commit()


def seed_dashboard_period_delta_data(conn):
    """Seed current and previous rolling-month merchant spending."""
    current_date = (real_date.today() - timedelta(days=10)).isoformat()
    previous_date = (real_date.today() - timedelta(days=45)).isoformat()
    rows = [
        (current_date, "Metro Grocery", 150.00, "Food", "dashboard-delta-current"),
        (current_date, "New Bakery", 60.00, "Food", "dashboard-delta-new"),
        (previous_date, "Metro Grocery", 100.00, "Food", "dashboard-delta-previous"),
    ]
    conn.executemany(
        """
        INSERT INTO transactions (tx_date, description, amount, category, category_source, fingerprint)
        VALUES (?, ?, ?, ?, 'rule', ?)
        """,
        rows,
    )
    conn.commit()


def seed_comparison_unknown_warning_data(conn):
    """Seed comparison periods where UNKNOWN exceeds warning thresholds."""
    rows = [
        ("2026-04-02", "Unknown Prior", 40.00, "UNKNOWN", "comparison-unknown-prior"),
        ("2026-04-03", "Prior Grocery", 60.00, "Food", "comparison-food-prior"),
        ("2026-05-02", "Unknown Current", 70.00, "UNKNOWN", "comparison-unknown-current"),
        ("2026-05-03", "Current Grocery", 30.00, "Food", "comparison-food-current"),
    ]
    conn.executemany(
        """
        INSERT INTO transactions (tx_date, description, amount, category, category_source, fingerprint)
        VALUES (?, ?, ?, ?, 'rule', ?)
        """,
        rows,
    )
    conn.commit()


def test_home_context_summarizes_seeded_year_to_date_data(db_conn):
    """Verify home summary values against realistic seeded statement data."""
    seed_reporting_data(db_conn)

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


def test_home_context_surfaces_command_center_activity(db_conn):
    """Verify Home exposes failed imports, suggested rules, and recent actions."""
    seed_reporting_data(db_conn)
    statement_type_id = db_conn.execute(
        """
        SELECT id
        FROM statement_types
        WHERE active = 1
        ORDER BY id
        LIMIT 1
        """
    ).fetchone()["id"]
    db_conn.execute(
        """
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
            ?, 'failed-home.csv', 'failed-home-checksum', 'raw',
            'failed', 'Parser failed', '2026-05-07T10:00:00Z'
        )
        """,
        (statement_type_id,),
    )
    db_conn.execute(
        """
        INSERT INTO category_rules (keyword, category, source, ai_approved, created_at)
        VALUES ('Suggested Home Merchant', 'Food', 'automatic', 0, '2026-05-06T10:00:00Z')
        """
    )
    db_conn.executemany(
        """
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
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                "2026-05-06",
                "Reviewed Home Cafe",
                22.00,
                "Food",
                "manual",
                0,
                "2026-05-06T12:00:00Z",
                "2026-05-06T12:00:00Z",
                "home-reviewed-activity",
            ),
            (
                "2026-05-07",
                "Categorized Home Market",
                44.00,
                "Food",
                "rule",
                0,
                None,
                "2026-05-07T12:00:00Z",
                "home-categorized-activity",
            ),
        ],
    )
    db_conn.commit()

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


def test_dashboard_context_totals_filters_custom_dates_and_sorting(app, db_conn):
    """Verify dashboard context totals, filters, custom ranges, and sorting."""
    seed_reporting_data(db_conn)
    args = MultiDict(
        [
            ("period", "custom"),
            ("date_from", "2026-01-01"),
            ("date_to", "2026-02-28"),
            ("category_sort", "category"),
            ("category_direction", "asc"),
            ("merchant_sort", "spending"),
            ("merchant_direction", "desc"),
        ]
    )

    with app.test_request_context("/dashboard"):
        context = build_dashboard_context(args)

    assert context["selected_period"] == "custom"
    assert context["period_label"] == "01-Jan-2026 to 28-Feb-2026"
    assert context["quick_view"] == "categorized"
    assert [(option["value"], option["active"]) for option in context["quick_view_options"]] == [
        ("categorized", True),
        ("needs_review", False),
        ("unknown", False),
        ("custom", False),
        ("all", False),
    ]
    assert context["total_spending"] == 260.00
    assert context["total_income"] == 1000.00
    assert context["net_cashflow"] == 740.00
    assert context["transaction_count"] == 4
    assert context["uncategorized_count"] == 0
    assert context["data_quality"]["transaction_count"] == 5
    assert context["data_quality"]["quality_score"] == 80
    assert context["data_quality"]["review_label"] == "Review 1 unknown transaction"
    assert context["data_quality"]["level"] == "warning"
    assert "quick_view=categorized" not in context["data_quality"]["categorized_url"]
    insights = context["dashboard_insights"]
    assert insights["average_transaction_amount"] == 315.00
    assert insights["untagged_spending_count"] == 0
    assert insights["untagged_spending_rate"] == 0.0
    assert insights["verified_count"] == 0
    assert insights["verified_rate"] == 0.0
    assert insights["top_source"]["label"] == "Rule"
    assert insights["top_source"]["count"] == 3
    assert insights["top_source"]["rate"] == 75.0
    assert category_totals(context) == {
        "Food": 140.00,
        "Utilities": 120.00,
    }
    assert list(merchant_totals(context).items()) == [
        ("HYDRO QUEBEC", 120.00),
        ("METRO GROCERY", 100.00),
    ]


def test_dashboard_context_quick_views_and_category_include_filter(app, db_conn):
    """Verify dashboard quick views and custom category filters."""
    seed_reporting_data(db_conn)

    with app.test_request_context("/dashboard"):
        unknown_context = build_dashboard_context(
            MultiDict(
                [
                    ("period", "custom"),
                    ("date_from", "2026-01-01"),
                    ("date_to", "2026-02-28"),
                    ("quick_view", "unknown"),
                ]
            )
        )
        food_context = build_dashboard_context(
            MultiDict(
                [
                    ("period", "custom"),
                    ("date_from", "2026-01-01"),
                    ("date_to", "2026-02-28"),
                    ("categories", "Food"),
                    ("filter_mode", "include"),
                ]
            )
        )
        tax_context = build_dashboard_context(
            MultiDict(
                [
                    ("period", "custom"),
                    ("date_from", "2026-01-01"),
                    ("date_to", "2026-02-28"),
                    ("tags", "Tax"),
                ]
            )
        )

    assert unknown_context["quick_view"] == "unknown"
    assert unknown_context["total_spending"] == 30.00
    assert unknown_context["transaction_count"] == 1
    assert unknown_context["uncategorized_count"] == 1
    assert quick_view_count(unknown_context, "unknown") == 1

    assert food_context["quick_view"] == "custom"
    assert food_context["selected_categories"] == ["Food"]
    assert food_context["total_spending"] == 140.00
    assert food_context["transaction_count"] == 2
    assert category_totals(food_context) == {"Food": 140.00}
    assert list(merchant_totals(food_context).items()) == [
        ("METRO GROCERY", 100.00),
        ("CAFE BISTRO", 40.00),
    ]

    assert tax_context["quick_view"] == "custom"
    assert tax_context["selected_tags"] == ["Tax"]
    assert tax_context["total_spending"] == 100.00
    assert tax_context["transaction_count"] == 1
    assert category_totals(tax_context) == {"Food": 100.00}
    assert list(merchant_totals(tax_context).items()) == [("METRO GROCERY", 100.00)]


def test_dashboard_context_tag_breakdown_counts_each_matching_tag(app, db_conn):
    """Verify tag breakdown uses tag-associated spending, including overlaps."""
    seed_reporting_data(db_conn)
    cafe_id = db_conn.execute(
        """
        SELECT id
        FROM transactions
        WHERE fingerprint = 'seed-2026-food-cafe'
        """
    ).fetchone()["id"]
    set_transaction_tags(db_conn, cafe_id, ["Shared", "Tax"], source="manual")
    db_conn.commit()
    args = MultiDict(
        [
            ("period", "custom"),
            ("date_from", "2026-01-01"),
            ("date_to", "2026-02-28"),
            ("quick_view", "all"),
            ("breakdown", "tag"),
        ]
    )

    with app.test_request_context("/dashboard"):
        context = build_dashboard_context(args)
        untagged_context = build_dashboard_context(
            MultiDict(
                [
                    ("period", "custom"),
                    ("date_from", "2026-01-01"),
                    ("date_to", "2026-02-28"),
                    ("quick_view", "all"),
                    ("breakdown", "tag"),
                    ("show_untagged", "1"),
                ]
            )
        )

    assert context["breakdown_mode"] == "tag"
    assert context["quick_view"] == "all"
    assert context["breakdown_is_tag"] is True
    assert context["show_untagged"] is False
    assert "show_untagged=1" in context["show_untagged_url"]
    assert context["breakdown_chart_title"] == "Spending by tag"
    assert context["breakdown_table_title"] == "Tag detail"
    assert context["breakdown_label"] == "Tag"
    assert context["total_spending"] == 290.00
    assert category_totals(context) == {
        "Government": 120.00,
        "Shared": 40.00,
        "Tax": 140.00,
    }
    assert sum(category_totals(context).values()) > context["total_spending"]
    assert context["category_labels"] == ["Tax", "Government", "Shared"]
    tax_row = next(row for row in context["category_rows"] if row["category"] == "Tax")
    assert "tags=Tax" in tax_row["url"]
    assert "amount_type=spending" in tax_row["url"]
    assert all(row["category"] != "Untagged" for row in context["category_rows"])

    assert untagged_context["show_untagged"] is True
    assert "show_untagged" not in untagged_context["show_untagged_url"]
    assert category_totals(untagged_context) == {
        "Government": 120.00,
        "Shared": 40.00,
        "Tax": 140.00,
        "Untagged": 30.00,
    }
    assert untagged_context["category_labels"] == [
        "Tax",
        "Government",
        "Shared",
        "Untagged",
    ]
    untagged_row = next(
        row for row in untagged_context["category_rows"]
        if row["category"] == "Untagged"
    )
    assert untagged_row["url"] == ""


def test_dashboard_tag_cashflow_includes_tagged_transfer_credits(app, db_conn):
    """Verify tagged dashboard cash flow nets reimbursed transfer credits."""
    seed_reimbursable_dashboard_data(db_conn)
    date_args = [
        ("period", "custom"),
        ("date_from", "2026-03-01"),
        ("date_to", "2026-03-31"),
    ]

    with app.test_request_context("/dashboard"):
        untagged_context = build_dashboard_context(MultiDict(date_args))
        reimbursable_context = build_dashboard_context(
            MultiDict([*date_args, ("tags", "Reimbursable")])
        )

    assert untagged_context["total_spending"] == 300.00
    assert untagged_context["total_income"] == 0
    assert untagged_context["net_cashflow"] == -300.00
    assert untagged_context["transaction_count"] == 1

    assert reimbursable_context["quick_view"] == "custom"
    assert reimbursable_context["selected_tags"] == ["Reimbursable"]
    assert reimbursable_context["total_spending"] == 300.00
    assert reimbursable_context["total_income"] == 250.00
    assert reimbursable_context["net_cashflow"] == -50.00
    assert reimbursable_context["transaction_count"] == 2
    assert reimbursable_context["income_month_totals"] == [250.00]
    assert reimbursable_context["net_month_totals"] == [-50.00]
    assert category_totals(reimbursable_context) == {"Travel": 300.00}
    assert "amount_type=credit" in reimbursable_context["dashboard_links"]["income"]


def test_comparison_context_year_and_period_metrics(app, db_conn, monkeypatch):
    """Verify comparison context year totals, category filters, and period metrics."""
    seed_reporting_data(db_conn)
    monkeypatch.setattr(comparison_service, "date", FixedDate)
    args = MultiDict(
        [
            ("years", "2025"),
            ("years", "2026"),
            ("baseline_year", "2025"),
            ("period_comparison", "month_last_year"),
            ("period_categories", "Food"),
            ("year_categories", "Food"),
        ]
    )

    with app.test_request_context("/comparison"):
        context = comparison_service.build_comparison_context(args)

    food_comparison = next(
        row for row in context["category_comparison"]
        if row["category"] == "Food"
    )
    period_totals = {
        metric["label"]: metric
        for metric in context["period_comparison"]["totals"]
    }

    assert context["comparison_has_data"] is True
    assert context["available_years"] == [2026, 2025]
    assert context["selected_years"] == [2025, 2026]
    assert context["selected_baseline_year"] == 2025
    assert context["selected_year_categories"] == ["Food"]
    assert context["selected_period_categories"] == ["Food"]
    assert context["monthly_spending"][2025][0] == 80.00
    assert context["monthly_spending"][2026][0] == 140.00
    assert food_comparison["totals"] == {2025: 140.00, 2026: 340.00}
    assert food_comparison["changes"][2026]["change"] == 200.00
    assert period_totals["Spending"]["current"] == 200.00
    assert period_totals["Spending"]["previous"] == 60.00
    assert period_totals["Transactions"]["current"] == 1
    assert context["period_comparison"]["category_rows"][0]["category"] == "Food"
    assert context["period_comparison"]["merchant_rows"][0]["merchant"] == "METRO GROCERY"
    insight_groups = context["period_comparison"]["insight_groups"]
    category_insight = context["period_comparison"]["insights"][0]
    activity_insight = next(
        insight for insight in context["period_comparison"]["insights"]
        if insight["label"] == "Transaction activity"
    )
    assert [group["key"] for group in insight_groups] == ["categories", "merchants", "spending"]
    assert insight_groups[0]["insights"][0]["group"] == "categories"
    assert insight_groups[1]["insights"][0]["group"] == "merchants"
    assert insight_groups[2]["insights"][0]["group"] == "spending"
    assert category_insight["visual"] == "comparison"
    assert category_insight["group"] == "categories"
    assert category_insight["tone"] == "danger"
    assert category_insight["icon"] == "bi-graph-up-arrow"
    assert category_insight["title"] == "Food"
    assert category_insight["summary"] == "+140.00 $"
    assert category_insight["previous_width"] == 30.0
    assert category_insight["current_width"] == 100.0
    assert activity_insight["visual"] == "activity"
    assert activity_insight["tone"] == "accent"
    assert activity_insight["stat_items"][2]["label"] == "Average"


def test_comparison_context_filters_year_and_period_by_tags(app, db_conn, monkeypatch):
    """Verify comparison contexts can be filtered by transaction tags."""
    seed_reporting_data(db_conn)
    monkeypatch.setattr(comparison_service, "date", FixedDate)
    args = MultiDict(
        [
            ("years", "2025"),
            ("years", "2026"),
            ("period_comparison", "month_last_year"),
            ("period_tags", "Tax"),
            ("year_tags", "Tax"),
        ]
    )

    with app.test_request_context("/comparison"):
        context = comparison_service.build_comparison_context(args)

    food_comparison = next(
        row for row in context["category_comparison"]
        if row["category"] == "Food"
    )
    period_totals = {
        metric["label"]: metric
        for metric in context["period_comparison"]["totals"]
    }

    assert context["selected_year_tags"] == ["Tax"]
    assert context["selected_period_tags"] == ["Tax"]
    assert context["monthly_spending"][2026][0] == 100.00
    assert food_comparison["totals"] == {2025: 60.00, 2026: 300.00}
    assert period_totals["Spending"]["current"] == 200.00
    assert period_totals["Spending"]["previous"] == 60.00
    assert context["period_comparison"]["merchant_rows"][0]["merchant"] == "METRO GROCERY"


def test_comparison_tag_cashflow_includes_tagged_transfer_credits(app, db_conn, monkeypatch):
    """Verify tagged comparison cash flow nets reimbursed transfer credits."""
    seed_reimbursable_comparison_data(db_conn)
    monkeypatch.setattr(comparison_service, "date", FixedDate)
    args = MultiDict(
        [
            ("period_comparison", "month_last_year"),
            ("period_tags", "Reimbursable"),
        ]
    )

    with app.test_request_context("/comparison"):
        context = comparison_service.build_comparison_context(args)

    period_totals = {
        metric["label"]: metric
        for metric in context["period_comparison"]["totals"]
    }

    assert context["selected_period_tags"] == ["Reimbursable"]
    assert period_totals["Spending"]["current"] == 200.00
    assert period_totals["Spending"]["previous"] == 100.00
    assert period_totals["Income and Credits"]["current"] == 150.00
    assert period_totals["Income and Credits"]["previous"] == 80.00
    assert period_totals["Net cash flow"]["current"] == -50.00
    assert period_totals["Net cash flow"]["previous"] == -20.00
    assert period_totals["Transactions"]["current"] == 2
    assert period_totals["Transactions"]["previous"] == 2


def test_comparison_period_transaction_count_excludes_transfers(app, db_conn, monkeypatch):
    """Verify comparison period activity excludes payment and transfer rows."""
    seed_reporting_data(db_conn)
    monkeypatch.setattr(comparison_service, "date", FixedDate)

    with app.test_request_context("/comparison"):
        context = comparison_service.build_comparison_context(
            MultiDict([("period_comparison", "month_last_year")])
        )

    assert context["period_comparison"]["current_transaction_count"] == 3
    assert context["period_comparison"]["previous_transaction_count"] == 3


def test_dashboard_context_handles_empty_database(app):
    """Verify dashboard context is coherent when no transactions exist."""
    with app.test_request_context("/dashboard"):
        context = build_dashboard_context(MultiDict())

    assert context["total_spending"] == 0
    assert context["total_income"] == 0
    assert context["net_cashflow"] == 0
    assert context["transaction_count"] == 0
    assert context["uncategorized_count"] == 0
    assert context["cash_flow_summary"]["savings_rate"] is None
    assert context["cash_flow_summary"]["savings_rate_label"] == "n/a"
    assert context["data_quality"]["level"] == "empty"
    assert context["data_quality"]["message"] == "No transactions in this view."
    assert context["category_rows"] == []
    assert context["merchant_rows"] == []


def test_dashboard_context_handles_zero_income_savings_rate(app, db_conn):
    """Verify spending-only views do not divide by zero for savings rate."""
    seed_dashboard_spending_only(db_conn)
    args = MultiDict(
        [
            ("period", "custom"),
            ("date_from", "2026-06-01"),
            ("date_to", "2026-06-30"),
        ]
    )

    with app.test_request_context("/dashboard"):
        context = build_dashboard_context(args)

    assert context["total_spending"] == 25.00
    assert context["total_income"] == 0
    assert context["net_cashflow"] == -25.00
    assert context["cash_flow_summary"]["status"] == "deficit"
    assert context["cash_flow_summary"]["savings_rate"] is None
    assert context["cash_flow_summary"]["savings_detail"] == "No income in this view."


def test_dashboard_context_handles_all_unknown_quick_view(app, db_conn):
    """Verify all-UNKNOWN views expose quality risk without category rows."""
    seed_dashboard_unknown_only(db_conn)
    args = MultiDict(
        [
            ("period", "custom"),
            ("date_from", "2026-07-01"),
            ("date_to", "2026-07-31"),
            ("quick_view", "unknown"),
        ]
    )

    with app.test_request_context("/dashboard"):
        context = build_dashboard_context(args)

    assert context["quick_view"] == "unknown"
    assert context["total_spending"] == 60.00
    assert context["transaction_count"] == 2
    assert context["uncategorized_count"] == 2
    assert context["category_rows"] == []
    assert context["category_labels"] == []
    assert context["data_quality"]["level"] == "danger"
    assert context["data_quality"]["review_label"] == "Review 2 unknown transactions"
    assert quick_view_count(context, "unknown") == 2


def test_dashboard_context_calculates_previous_period_merchant_deltas(app, db_conn):
    """Verify merchant rows include current versus prior rolling-period deltas."""
    seed_dashboard_period_delta_data(db_conn)
    args = MultiDict(
        [
            ("period", "month"),
            ("merchant_sort", "merchant"),
            ("merchant_direction", "asc"),
        ]
    )

    with app.test_request_context("/dashboard"):
        context = build_dashboard_context(args)

    merchants = {
        row["merchant"]: row
        for row in context["merchant_rows"]
    }
    assert merchants["METRO GROCERY"]["total"] == 150.00
    assert merchants["METRO GROCERY"]["period_change"]["label"] == "+50%"
    assert merchants["METRO GROCERY"]["period_change"]["direction"] == "up"
    assert merchants["METRO GROCERY"]["period_change"]["sort_value"] == 50
    assert merchants["NEW BAKERY"]["period_change"]["label"] == "n/a"
    assert merchants["NEW BAKERY"]["period_change"]["detail"] == "No comparison"


def test_comparison_context_handles_empty_database(app, monkeypatch):
    """Verify comparison context falls back cleanly when no data exists."""
    monkeypatch.setattr(comparison_service, "date", FixedDate)

    with app.test_request_context("/comparison"):
        context = comparison_service.build_comparison_context(MultiDict())

    assert context["comparison_has_data"] is False
    assert context["available_years"] == []
    assert context["selected_years"] == [2026]
    assert context["selected_baseline_year"] is None
    assert context["category_comparison"] == []
    assert context["monthly_spending"][2026] == [0] * 12
    assert context["monthly_spending_json"] == [{"year": 2026, "totals": [0] * 12}]
    assert context["year_unknown_warning"] is None
    assert context["period_comparison"]["current_transaction_count"] == 0
    assert context["period_comparison"]["previous_transaction_count"] == 0


def test_comparison_context_filters_invalid_years_and_falls_back_baseline(app, db_conn, monkeypatch):
    """Verify invalid years are ignored and invalid baselines use previous-year comparisons."""
    seed_reporting_data(db_conn)
    monkeypatch.setattr(comparison_service, "date", FixedDate)

    with app.test_request_context("/comparison"):
        invalid_year_context = comparison_service.build_comparison_context(
            MultiDict([("years", "1999"), ("baseline_year", "1999")])
        )
        fallback_context = comparison_service.build_comparison_context(
            MultiDict(
                [
                    ("years", "2025"),
                    ("years", "2026"),
                    ("baseline_year", "1999"),
                    ("year_categories", "Food"),
                ]
            )
        )

    food_comparison = next(
        row for row in fallback_context["category_comparison"]
        if row["category"] == "Food"
    )
    assert invalid_year_context["selected_years"] == [2026]
    assert invalid_year_context["selected_baseline_year"] is None
    assert fallback_context["selected_years"] == [2025, 2026]
    assert fallback_context["selected_baseline_year"] is None
    assert food_comparison["changes"][2026]["baseline_year"] == 2025
    assert food_comparison["changes"][2026]["change"] == 200.00


def test_comparison_context_warns_when_unknown_exceeds_threshold(app, db_conn, monkeypatch):
    """Verify UNKNOWN warnings fire for year and period comparison contexts."""
    seed_comparison_unknown_warning_data(db_conn)
    monkeypatch.setattr(comparison_service, "date", FixedDate)
    args = MultiDict(
        [
            ("years", "2026"),
            ("period_comparison", "month_previous"),
        ]
    )

    with app.test_request_context("/comparison"):
        context = comparison_service.build_comparison_context(args)

    assert "Category comparison may be unreliable" in context["year_unknown_warning"]["source"]
    assert context["year_unknown_warning"]["values"] == {
        "category": "UNKNOWN",
        "share": "55.0",
    }
    assert "UNKNOWN accounts for 55.0%" in context["year_unknown_warning"]["text"]
    assert "Category insights may be incomplete" in context["period_comparison"]["unknown_warning"]["source"]
    assert context["period_comparison"]["unknown_warning"]["values"] == {
        "category": "UNKNOWN",
        "share": "70.0",
    }
    assert "UNKNOWN accounts for 70.0%" in context["period_comparison"]["unknown_warning"]["text"]
