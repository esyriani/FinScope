"""Service-level tests for calendar page context behavior."""

from datetime import date as real_date

from sqlalchemy import text
from tests.support.database import insert_account, insert_merchant, insert_transaction
from werkzeug.datastructures import MultiDict

from finance_app.modules.calendar import parsing as calendar_parsing
from finance_app.modules.calendar import presenter as calendar_presenter
from finance_app.modules.calendar import recurrence as calendar_recurrence
from finance_app.modules.calendar import service as calendar_service
from finance_app.modules.categories.repository import resolve_category_id
from finance_app.modules.categories.tag_filters import UNTAGGED_TAG_FILTER
from finance_app.modules.categories.taxonomy import set_transaction_tags
from finance_app.modules.recurring import service as recurring_service
from finance_app.modules.recurring.patterns import recurring_pattern_key, upsert_recurring_pattern


class FixedDate(real_date):
    """Fixed replacement for date.today in calendar context tests."""

    @classmethod
    def today(cls):
        """Return a deterministic current date."""
        return cls(2026, 5, 9)


def seed_calendar_transactions(conn):
    """Seed calendar transactions, including one recurring monthly merchant."""
    account_id = conn.execute(text("""
        INSERT INTO accounts (name)
        VALUES ('Visa')
        """)).lastrowid
    rows = [
        ("2026-02-05", "NETFLIX", 18.99, "Entertainment", 0, "expense", "calendar-netflix-feb"),
        ("2026-03-05", "NETFLIX", 18.99, "Entertainment", 0, "expense", "calendar-netflix-mar"),
        ("2026-04-05", "NETFLIX", 18.99, "Entertainment", 0, "expense", "calendar-netflix-apr"),
        ("2026-05-02", "Metro Grocery", 50.00, "Food", 0, "expense", "calendar-metro"),
        ("2026-05-03", "Payroll", -1000.00, "Income", 0, "income", "calendar-payroll"),
        ("2026-05-04", "Unknown Shop", 30.00, "UNKNOWN", 0, "expense", "calendar-unknown"),
        ("2026-05-05", "NETFLIX", 18.99, "Entertainment", 0, "expense", "calendar-netflix-may"),
        ("2026-05-06", "Ignored Store", 999.00, "Food", 1, "expense", "calendar-ignored"),
    ]
    conn.execute(
        text("""
        INSERT INTO transactions (
            account_id,
            tx_date,
            description,
            amount,
            category,
            category_id,
            ignored,
            transaction_kind,
            fingerprint
        )
        VALUES (:p0, :p1, :p2, :p3, :p4, :category_id, :p5, :p6, :p7)
        """),
        [
            {
                **dict(zip(("p0", "p1", "p2", "p3", "p4", "p5", "p6", "p7"), row)),
                "category_id": resolve_category_id(conn, row[4]),
            }
            for row in [
                (account_id, tx_date, description, amount, category, ignored, transaction_kind, fingerprint)
                for tx_date, description, amount, category, ignored, transaction_kind, fingerprint in rows
            ]
        ],
    )
    for fingerprint, tags in (
        ("calendar-metro", ["Tax"]),
        ("calendar-netflix-may", ["Subscription"]),
    ):
        transaction_id = (
            conn.execute(
                text("""
            SELECT id
            FROM transactions
            WHERE fingerprint = :p0
            """),
                {"p0": fingerprint},
            )
            .fetchone()
            ._mapping["id"]
        )
        set_transaction_tags(conn, transaction_id, tags, source="rule")
    conn.execute(
        text("""
        INSERT INTO transactions (
            account_id,
            tx_date,
            description,
            amount,
            category,
            category_id,
            transaction_kind,
            fingerprint
        )
        VALUES (
            :p0,
            '2026-05-07',
            'Savings transfer',
            500.00,
            'Transfers',
            :category_id,
            'transfer',
            'calendar-transfer'
        )
        """),
        {"p0": account_id, "category_id": resolve_category_id(conn, "Transfers")},
    )
    conn.commit()
    return account_id


def patch_calendar_today(monkeypatch):
    """Patch calendar modules that read the current date directly."""
    monkeypatch.setattr(calendar_service, "date", FixedDate)
    monkeypatch.setattr(calendar_presenter, "date", FixedDate)
    monkeypatch.setattr(calendar_parsing, "date", FixedDate)
    monkeypatch.setattr(calendar_recurrence, "date", FixedDate)
    monkeypatch.setattr(recurring_service, "date", FixedDate)


def calendar_day(context, day_key):
    """Return a single calendar day from the context."""
    return next(day for day in context["days"] if day["date"] == day_key)


def test_calendar_parsing_handles_months_and_heatmap_defaults():
    """Verify calendar parser accepts valid months and rejects invalid options."""
    assert calendar_parsing.parse_month("2026-05") == real_date(2026, 5, 1)
    assert calendar_parsing.parse_month("not-a-month") is None
    assert calendar_parsing.parse_heatmap_metric("net") == "net"
    assert calendar_parsing.parse_heatmap_metric("bogus") == "spending"


def test_calendar_context_builds_totals_day_json_and_heatmap(app, core_conn, monkeypatch):
    """Verify calendar context totals, day payloads, and net heatmap classes."""
    seed_calendar_transactions(core_conn)
    patch_calendar_today(monkeypatch)
    args = MultiDict([("month", "2026-05"), ("heatmap", "net")])

    with app.test_request_context("/calendar"):
        context = calendar_service.build_calendar_context(args)

    may_2 = calendar_day(context, "2026-05-02")
    may_3 = calendar_day(context, "2026-05-03")
    may_6 = calendar_day(context, "2026-05-06")
    may_2_json = context["calendar_day_json"]["2026-05-02"]

    assert context["selected_month"] == "2026-05"
    assert context["month_label"] == "May 2026"
    assert context["heatmap_metric"] == "net"
    assert "month=2026-04" in context["previous_month_url"]
    assert "heatmap=net" in context["previous_month_url"]
    assert context["summary"]["spending"] == 98.99
    assert context["summary"]["income"] == 1000.00
    assert context["summary"]["net"] == 901.01
    assert context["summary"]["transaction_count"] == 4
    assert may_2["spending"] == 50.00
    assert may_2["net"] == -50.00
    assert may_2["heatmap_class"] == "calendar-heat-spending"
    assert may_2["heatmap_alpha"] > 0
    assert may_3["income"] == 1000.00
    assert may_3["net"] == 1000.00
    assert may_3["heatmap_class"] == "calendar-heat-income"
    assert may_6["transactions"] == []
    assert may_2_json["transactions"][0]["description"] == "Metro Grocery"
    assert may_2_json["transactions"][0]["amount"] == 50.00


def test_calendar_context_applies_category_filters(app, core_conn, monkeypatch):
    """Verify category filters constrain calendar totals and day payloads."""
    seed_calendar_transactions(core_conn)
    patch_calendar_today(monkeypatch)
    args = MultiDict([("month", "2026-05"), ("categories", "Food")])

    with app.test_request_context("/calendar"):
        context = calendar_service.build_calendar_context(args)

    assert context["selected_categories"] == ["Food"]
    assert context["summary"]["spending"] == 50.00
    assert context["summary"]["income"] == 0
    assert context["summary"]["transaction_count"] == 1
    assert calendar_day(context, "2026-05-02")["transactions"][0]["category"] == "Food"
    assert calendar_day(context, "2026-05-03")["transactions"] == []


def test_calendar_context_applies_tag_filters(app, core_conn, monkeypatch):
    """Verify tag filters constrain calendar totals and navigation URLs."""
    seed_calendar_transactions(core_conn)
    patch_calendar_today(monkeypatch)
    args = MultiDict([("month", "2026-05"), ("tags", "Tax")])

    with app.test_request_context("/calendar"):
        context = calendar_service.build_calendar_context(args)

    assert context["selected_tags"] == ["Tax"]
    assert context["summary"]["spending"] == 50.00
    assert context["summary"]["income"] == 0
    assert context["summary"]["transaction_count"] == 1
    assert "tags=Tax" in context["previous_month_url"]
    assert calendar_day(context, "2026-05-02")["transactions"][0]["category"] == "Food"
    assert calendar_day(context, "2026-05-05")["transactions"] == []


def test_calendar_context_filters_activity_by_account(app, core_conn, monkeypatch):
    """Verify calendar and recurring activity can be scoped to one account."""
    visa_id = seed_calendar_transactions(core_conn)
    checking_id = insert_account(core_conn, "Daily Checking")
    insert_transaction(
        core_conn,
        "Checking Store",
        500.00,
        "Food",
        account_id=checking_id,
        tx_date="2026-05-02",
        fingerprint="calendar-account-checking-food",
        category_source="rule",
        needs_review=0,
    )
    patch_calendar_today(monkeypatch)
    args = MultiDict([("month", "2026-05"), ("account_id", str(visa_id))])

    with app.test_request_context("/calendar"):
        context = calendar_service.build_calendar_context(args)

    may_2 = calendar_day(context, "2026-05-02")

    assert context["selected_account_id"] == visa_id
    assert {account["name"] for account in context["account_options"]} == {"Daily Checking", "Visa"}
    assert context["summary"]["spending"] == 98.99
    assert context["summary"]["transaction_count"] == 4
    assert context["summary"]["recurring_count"] == 1
    assert len(may_2["transactions"]) == 1
    assert may_2["transactions"][0]["description"] == "Metro Grocery"
    assert f"account_id={visa_id}" in context["previous_month_url"]
    assert f"account_id={visa_id}" in context["month_transactions_url"]
    assert f"account_id={visa_id}" in may_2["url"]
    assert f"account_id={visa_id}" in may_2["transactions"][0]["url"]


def test_calendar_context_combines_account_merchant_category_and_tag_filters(app, core_conn, monkeypatch):
    """Verify calendar totals, days, and URLs preserve combined analytics filters."""
    visa_id = insert_account(core_conn, "Visa")
    checking_id = insert_account(core_conn, "Daily Checking")
    netflix_id = insert_merchant(core_conn, "NETFLIX")
    metro_id = insert_merchant(core_conn, "METRO")
    netflix_rows = [
        ("2026-02-05", "NETFLIX SUBSCRIPTION", "calendar-filter-netflix-feb"),
        ("2026-03-05", "NETFLIX SUBSCRIPTION", "calendar-filter-netflix-mar"),
        ("2026-04-05", "NETFLIX SUBSCRIPTION", "calendar-filter-netflix-apr"),
        ("2026-05-05", "NETFLIX SUBSCRIPTION", "calendar-filter-netflix-may"),
    ]
    for tx_date, description, fingerprint in netflix_rows:
        insert_transaction(
            core_conn,
            description,
            18.99,
            "Entertainment",
            account_id=visa_id,
            merchant_id=netflix_id,
            tx_date=tx_date,
            fingerprint=fingerprint,
            category_source="rule",
            needs_review=0,
            tags=["Subscription"],
        )
    insert_transaction(
        core_conn,
        "NETFLIX CHECKING NOISE",
        18.99,
        "Entertainment",
        account_id=checking_id,
        merchant_id=netflix_id,
        tx_date="2026-05-05",
        fingerprint="calendar-filter-netflix-checking-noise",
        category_source="rule",
        needs_review=0,
        tags=["Subscription"],
    )
    insert_transaction(
        core_conn,
        "METRO GROCERY",
        42.00,
        "Food",
        account_id=visa_id,
        merchant_id=metro_id,
        tx_date="2026-05-06",
        fingerprint="calendar-filter-metro-noise",
        category_source="rule",
        needs_review=0,
        tags=["Subscription"],
    )
    patch_calendar_today(monkeypatch)
    args = MultiDict(
        [
            ("month", "2026-05"),
            ("account_id", str(visa_id)),
            ("merchant_id", str(netflix_id)),
            ("merchant_query", "NETFLIX"),
            ("categories", "Entertainment"),
            ("tags", "Subscription"),
        ]
    )

    with app.test_request_context("/calendar"):
        context = calendar_service.build_calendar_context(args)

    may_5 = calendar_day(context, "2026-05-05")
    may_6 = calendar_day(context, "2026-05-06")

    assert context["selected_account_id"] == visa_id
    assert context["selected_merchant_id"] == netflix_id
    assert context["merchant_query"] == "NETFLIX"
    assert context["selected_merchant_label"] == "NETFLIX"
    assert context["summary"]["spending"] == 18.99
    assert context["summary"]["transaction_count"] == 1
    assert context["summary"]["recurring_count"] == 1
    assert [transaction["description"] for transaction in may_5["transactions"]] == ["NETFLIX SUBSCRIPTION"]
    assert may_6["transactions"] == []
    assert f"account_id={visa_id}" in context["previous_month_url"]
    assert f"merchant_id={netflix_id}" in context["previous_month_url"]
    assert "merchant_query=NETFLIX" in context["previous_month_url"]
    assert "categories=Entertainment" in context["previous_month_url"]
    assert "tags=Subscription" in context["previous_month_url"]
    assert f"account_id={visa_id}" in context["month_transactions_url"]
    assert "search=NETFLIX" in context["month_transactions_url"]
    assert f"merchant_id={netflix_id}" in context["recurring_calendar_url"]
    assert "merchant_query=NETFLIX" in context["recurring_list_url"]


def test_calendar_context_applies_untagged_filter(app, core_conn, monkeypatch):
    """Verify the virtual untagged tag filter finds transactions without tags."""
    seed_calendar_transactions(core_conn)
    patch_calendar_today(monkeypatch)
    args = MultiDict([("month", "2026-05"), ("tags", UNTAGGED_TAG_FILTER)])

    with app.test_request_context("/calendar"):
        context = calendar_service.build_calendar_context(args)

    assert context["selected_tags"] == [UNTAGGED_TAG_FILTER]
    assert context["summary"]["spending"] == 48.99
    assert context["summary"]["income"] == 1000.00
    assert context["summary"]["transaction_count"] == 3
    assert calendar_day(context, "2026-05-02")["transactions"] == []
    assert calendar_day(context, "2026-05-04")["transactions"][0]["description"] == "Unknown Shop"


def test_recurring_activity_context_exposes_json_payload(app, core_conn, monkeypatch):
    """Verify recurring activity context includes serializable recurrence data."""
    seed_calendar_transactions(core_conn)
    patch_calendar_today(monkeypatch)

    with app.test_request_context("/calendar"):
        context = calendar_service.build_recurring_activity_context(
            real_date(2026, 5, 1),
            ["Entertainment"],
        )

    assert context["summary"]["recurring_count"] == 1
    assert context["summary"]["recurring_occurred_count"] == 1
    item = context["recurring_items"][0]
    payload = context["recurring_activity_json"][item["id"]]
    assert item["merchant"] == "NETFLIX"
    assert item["status"] == "occurred"
    assert item["date"] == "2026-05-05"
    assert payload["patternKey"] == "NETFLIX::spending"
    assert payload["merchant"] == "NETFLIX"
    assert payload["status"] == "occurred"
    assert payload["matchDetails"]["matched_date"] == "2026-05-05"
    assert payload["occurrences"][0]["date"] == "2026-04-05"


def test_recurring_page_context_filters_confirmed_patterns_by_merchant_text(app, core_conn, monkeypatch):
    """Verify recurring merchant filters match pattern merchants and examples."""
    visa_id = insert_account(core_conn, "Visa")
    netflix_id = insert_merchant(core_conn, "NETFLIX")
    hydro_id = insert_merchant(core_conn, "HYDRO")
    for tx_date, fingerprint in [
        ("2026-02-05", "recurring-filter-streaming-feb"),
        ("2026-03-05", "recurring-filter-streaming-mar"),
        ("2026-04-05", "recurring-filter-streaming-apr"),
        ("2026-05-05", "recurring-filter-streaming-may"),
    ]:
        insert_transaction(
            core_conn,
            "MONTHLY STREAMING CHARGE",
            18.99,
            "Entertainment",
            account_id=visa_id,
            merchant_id=netflix_id,
            tx_date=tx_date,
            fingerprint=fingerprint,
            category_source="rule",
            needs_review=0,
        )
    for tx_date, fingerprint in [
        ("2026-02-08", "recurring-filter-hydro-feb"),
        ("2026-03-08", "recurring-filter-hydro-mar"),
        ("2026-04-08", "recurring-filter-hydro-apr"),
        ("2026-05-08", "recurring-filter-hydro-may"),
    ]:
        insert_transaction(
            core_conn,
            "HYDRO BILL",
            64.00,
            "Utilities",
            account_id=visa_id,
            merchant_id=hydro_id,
            tx_date=tx_date,
            fingerprint=fingerprint,
            category_source="rule",
            needs_review=0,
        )
    upsert_recurring_pattern(
        core_conn,
        recurring_pattern_key("NETFLIX", "spending"),
        "NETFLIX",
        "spending",
        merchant_id=netflix_id,
        user_status="confirmed",
    )
    core_conn.commit()
    patch_calendar_today(monkeypatch)

    with app.test_request_context("/recurring"):
        pattern_context = recurring_service.build_recurring_page_context(
            MultiDict(
                [
                    ("month", "2026-05"),
                    ("confidence", "all"),
                    ("merchant_query", "NETFLIX"),
                ]
            )
        )
        example_context = recurring_service.build_recurring_page_context(
            MultiDict(
                [
                    ("month", "2026-05"),
                    ("confidence", "all"),
                    ("merchant_query", "STREAMING"),
                ]
            )
        )
        no_match_context = recurring_service.build_recurring_page_context(
            MultiDict(
                [
                    ("month", "2026-05"),
                    ("confidence", "all"),
                    ("merchant_query", "GROCERY"),
                ]
            )
        )

    assert [item["merchant"] for item in pattern_context["recurring_items"]] == ["NETFLIX"]
    assert pattern_context["recurring_items"][0]["user_status"] == "confirmed"
    assert [item["merchant"] for item in example_context["recurring_items"]] == ["NETFLIX"]
    assert no_match_context["recurring_items"] == []
    assert no_match_context["recurring_empty_state_message"] == "No recurring activity matches this merchant."
    assert "merchant_query=NETFLIX" in pattern_context["previous_month_url"]
    assert "merchant_query=STREAMING" in example_context["list_view_url"]


def test_recurring_page_context_combines_account_and_exact_merchant_filters(app, core_conn, monkeypatch):
    """Verify recurring account and exact merchant filters combine predictably."""
    visa_id = insert_account(core_conn, "Visa")
    checking_id = insert_account(core_conn, "Daily Checking")
    netflix_id = insert_merchant(core_conn, "NETFLIX")
    for account_id, suffix in [(visa_id, "visa"), (checking_id, "checking")]:
        for tx_date in ["2026-02-05", "2026-03-05", "2026-04-05", "2026-05-05"]:
            insert_transaction(
                core_conn,
                f"NETFLIX {suffix}",
                18.99,
                "Entertainment",
                account_id=account_id,
                merchant_id=netflix_id,
                tx_date=tx_date,
                fingerprint=f"recurring-filter-{suffix}-{tx_date}",
                category_source="rule",
                needs_review=0,
            )
    patch_calendar_today(monkeypatch)

    with app.test_request_context("/recurring"):
        context = recurring_service.build_recurring_page_context(
            MultiDict(
                [
                    ("month", "2026-05"),
                    ("confidence", "all"),
                    ("account_id", str(visa_id)),
                    ("merchant_id", str(netflix_id)),
                    ("merchant_query", "NETFLIX"),
                ]
            )
        )

    assert context["selected_account_id"] == visa_id
    assert context["selected_merchant_id"] == netflix_id
    assert [item["merchant"] for item in context["recurring_items"]] == ["NETFLIX"]
    assert context["recurring_summary"]["occurred_count"] == 1
    assert f"account_id={visa_id}" in context["calendar_view_url"]
    assert f"merchant_id={netflix_id}" in context["calendar_view_url"]
    assert "merchant_query=NETFLIX" in context["calendar_view_url"]


def test_recurring_activity_context_trains_on_prior_months_only(app, core_conn, monkeypatch):
    """Verify selected-month merchant activity does not create recurrence history."""
    account_id = core_conn.execute(text("""
        INSERT INTO accounts (name)
        VALUES ('Visa')
        """)).lastrowid
    core_conn.execute(
        text("""
        INSERT INTO transactions (
            account_id,
            tx_date,
            description,
            amount,
            category,
            transaction_kind,
            fingerprint
        )
        VALUES (:account_id, :tx_date, :description, :amount, 'Food', 'expense', :fingerprint)
        """),
        [
            {
                "account_id": account_id,
                "tx_date": "2026-01-05",
                "description": "Noisy Store",
                "amount": 42.00,
                "fingerprint": "calendar-noisy-store-jan",
            },
            {
                "account_id": account_id,
                "tx_date": "2026-03-05",
                "description": "Noisy Store",
                "amount": 43.00,
                "fingerprint": "calendar-noisy-store-mar",
            },
            {
                "account_id": account_id,
                "tx_date": "2026-05-05",
                "description": "Noisy Store",
                "amount": 44.00,
                "fingerprint": "calendar-noisy-store-may",
            },
        ],
    )
    core_conn.commit()
    patch_calendar_today(monkeypatch)

    with app.test_request_context("/calendar"):
        context = calendar_service.build_recurring_activity_context(real_date(2026, 5, 1), ["Food"])

    assert [item["merchant"] for item in context["recurring_items"]] == []
