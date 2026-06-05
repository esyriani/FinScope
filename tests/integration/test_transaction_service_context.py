"""Tests for transaction list service context behavior."""

from sqlalchemy import text
import json

from werkzeug.datastructures import MultiDict
from sqlalchemy import insert, select, update

from finance_app.database.tables import (
    accounts as accounts_table,
    transactions as transactions_table,
    user_settings as user_settings_table,
    users as users_table,
)
from finance_app.modules.categories.repository import resolve_category_id
from finance_app.modules.categories.tag_filters import UNTAGGED_TAG_FILTER
from finance_app.modules.categories.taxonomy import get_rule_tags_by_rule_id, get_transaction_tag_names, set_transaction_tags
from finance_app.modules.merchants.repository import get_or_create_merchant_for_description
from finance_app.modules.transactions import service as transactions_service
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
        update(user_settings_table)
        .where(
            user_settings_table.c["key"] == "default_table_page_size",
            user_settings_table.c.user_id
            == select(users_table.c.id)
            .where(users_table.c.username == "owner")
            .scalar_subquery(),
        )
        .values(value="2")
    )
    conn.commit()
    return ids


def descriptions(context):
    """Return descriptions from a transaction context."""
    return [row["description"] for row in context["transactions"]]


def test_transactions_context_paginates_and_sorts(core_conn):
    """Verify transaction context pagination and stable sorting."""
    ids = seed_transactions(core_conn)

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
    assert first_page["all_transaction_ids"] == [
        ids["Hydro Quebec"],
        ids["Unknown Shop"],
        ids["Metro Grocery"],
        ids["Cafe Bistro"],
        ids["Payroll"],
    ]
    assert second_page["page_start"] == 3
    assert second_page["page_end"] == 4
    assert first_page["run_transaction_ai_enabled"] is True


def test_transactions_context_ignored_filters(core_conn):
    """Verify active, ignored, and all ignored-state filters."""
    seed_transactions(core_conn)

    active = build_transactions_context(MultiDict([("period", "all")]))
    ignored = build_transactions_context(MultiDict([("period", "all"), ("ignored", "ignored")]))
    all_rows = build_transactions_context(MultiDict([("period", "all"), ("ignored", "all")]))

    assert active["selected_ignored"] == "active"
    assert active["total_count"] == 5
    assert ignored["selected_ignored"] == "ignored"
    assert descriptions(ignored) == ["Ignored Store"]
    assert all_rows["selected_ignored"] == "all"
    assert all_rows["total_count"] == 6


def test_transactions_context_category_source_and_review_filters(core_conn):
    """Verify category source, review, and unknown/categorized filters."""
    seed_transactions(core_conn)

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
    pending_approval_context = build_transactions_context(
        MultiDict([("period", "all"), ("review", "pending_approval")])
    )
    verified_context = build_transactions_context(
        MultiDict([("period", "all"), ("review", "verified")])
    )

    assert descriptions(ai_context) == ["Hydro Quebec"]
    assert ai_context["selected_category_source"] == "ai"
    assert ai_context["review_filter_options"] == (
        ("", "All"),
        ("needs_review", "Needs review"),
        ("pending_approval", "Pending approval"),
        ("verified", "Approved"),
    )
    assert ai_context["category_source_filter_options"] == (
        ("", "All methods"),
        ("manual_reviewed", "Manual"),
        ("rule", "Rule"),
        ("history", "Similarity"),
        ("ai", "AI"),
    )
    assert ai_context["transactions"][0]["category_source_label"] == "AI"
    assert ai_context["transactions"][0]["category_source_badge_class"] == "text-bg-info"
    assert descriptions(manual_context) == ["Cafe Bistro"]
    assert descriptions(unknown_context) == ["Unknown Shop"]
    assert descriptions(needs_review_context) == ["Unknown Shop"]
    assert pending_approval_context["selected_review"] == "pending_approval"
    assert pending_approval_context["total_count"] == 3
    assert verified_context["selected_review"] == "verified"
    assert descriptions(verified_context) == ["Cafe Bistro"]


def test_transactions_context_merchant_filters_and_tag_rendering(core_conn):
    """Verify merchant filters and tag view model fields."""
    seed_transactions(core_conn)

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
    untagged_context = build_transactions_context(
        MultiDict([("period", "all"), ("tags", UNTAGGED_TAG_FILTER)])
    )

    metro = merchant_context["transactions"][0]
    assert descriptions(merchant_context) == ["Metro Grocery"]
    assert metro["merchant_key"] == "METRO GROCERY"
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
    assert untagged_context["selected_tags"] == [UNTAGGED_TAG_FILTER]
    assert untagged_context["total_count"] == 3
    assert all(row["tag_label"] == "" for row in untagged_context["transactions"])


def test_transactions_context_merchant_filter_uses_deterministic_keys(core_conn):
    """Verify merchant filtering does not expand through unmanaged aliases."""
    account_id = core_conn.execute(
        insert(accounts_table).values(name="Merchant checking")
    ).inserted_primary_key[0]
    food_id = resolve_category_id(core_conn, "Food")
    core_conn.execute(
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
    core_conn.commit()

    context = build_transactions_context(
        MultiDict([("period", "all"), ("merchant_key", "AMZN MKTP")])
    )

    assert context["total_count"] == 1
    assert descriptions(context) == ["AMZN MKTP CA*1234"]


def test_transactions_context_custom_dates_are_inclusive(core_conn):
    """Verify custom date filters include exact start and end boundaries."""
    account_id = core_conn.execute(
        insert(accounts_table).values(name="Boundary checking")
    ).inserted_primary_key[0]
    food_id = resolve_category_id(core_conn, "Food")
    for tx_date, description, fingerprint in [
        ("2026-03-31", "Before boundary", "tx-list-before-boundary"),
        ("2026-04-01", "Start boundary", "tx-list-start-boundary"),
        ("2026-04-30", "End boundary", "tx-list-end-boundary"),
        ("2026-05-01", "After boundary", "tx-list-after-boundary"),
    ]:
        core_conn.execute(
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
    core_conn.commit()

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


def test_transactions_context_reads_single_transaction_ai_setting(core_conn):
    """Verify transaction context exposes the single-transaction AI setting."""
    seed_transactions(core_conn)
    core_conn.execute(
        update(user_settings_table)
        .where(user_settings_table.c["key"] == "transaction_ai_rerun_enabled")
        .values(value="0")
    )
    core_conn.commit()

    context = build_transactions_context(MultiDict([("period", "all")]))

    assert context["run_transaction_ai_enabled"] is False


def test_recategorize_selected_transactions_job_updates_selected_rows(core_conn, monkeypatch):
    """Verify selected recategorization persists workflow results and tags."""
    account_id = core_conn.execute(
        insert(accounts_table).values(name="Batch AI")
    ).inserted_primary_key[0]
    target_id = core_conn.execute(
        insert(transactions_table).values(
            account_id=account_id,
            tx_date="2026-05-04",
            description="TVA SPORTS DIRECT",
            amount=20.68,
            category="UNKNOWN",
            category_id=resolve_category_id(core_conn, "UNKNOWN"),
            category_source="unknown",
            needs_review=1,
            fingerprint="batch-recat-target",
        )
    ).inserted_primary_key[0]
    other_id = core_conn.execute(
        insert(transactions_table).values(
            account_id=account_id,
            tx_date="2026-05-05",
            description="METRO GROCERY",
            amount=42.00,
            category="UNKNOWN",
            category_id=resolve_category_id(core_conn, "UNKNOWN"),
            category_source="unknown",
            needs_review=1,
            fingerprint="batch-recat-other",
        )
    ).inserted_primary_key[0]
    untouched_id = core_conn.execute(
        insert(transactions_table).values(
            account_id=account_id,
            tx_date="2026-05-06",
            description="UNTOUCHED STORE",
            amount=9.99,
            category="UNKNOWN",
            category_id=resolve_category_id(core_conn, "UNKNOWN"),
            category_source="unknown",
            needs_review=1,
            fingerprint="batch-recat-untouched",
        )
    ).inserted_primary_key[0]
    core_conn.commit()

    def categorize_for_test(transactions, conn, use_llm=True):
        """Return deterministic categorization for the selected batch."""
        del conn
        assert use_llm is True
        assert [tx["id"] for tx in transactions] == [target_id, other_id]
        transactions[0].update(
            {
                "category": "Entertainment",
                "tags": ["Service"],
                "needs_review": 0,
                "category_source": "ai",
                "category_confidence": 0.96,
                "category_rule_id": None,
                "category_metadata": {"decision_source": "llm"},
                "categorized_at": "2026-05-04T12:00:00Z",
                "reviewed_at": None,
            }
        )
        transactions[1].update(
            {
                "category": "Food",
                "tags": ["Tax"],
                "needs_review": 1,
                "category_source": "rule",
                "category_confidence": 0.75,
                "category_rule_id": None,
                "category_metadata": {"decision_source": "rule"},
                "categorized_at": "2026-05-04T12:01:00Z",
                "reviewed_at": None,
            }
        )
        return transactions

    monkeypatch.setattr(transactions_service, "categorize_transactions", categorize_for_test)

    message = transactions_service.recategorize_selected_transactions_job([target_id, other_id])

    target = core_conn.execute(text("SELECT category, category_source, category_confidence, needs_review FROM transactions WHERE id = :p0"), {"p0": target_id}).fetchone()
    other = core_conn.execute(text("SELECT category, category_source, category_confidence, needs_review FROM transactions WHERE id = :p0"), {"p0": other_id}).fetchone()
    untouched = core_conn.execute(text("SELECT category, category_source, needs_review FROM transactions WHERE id = :p0"), {"p0": untouched_id}).fetchone()
    assert message == "2 selected transactions recategorized."
    assert tuple(target) == ("Entertainment", "ai", 0.96, 0)
    assert tuple(other) == ("Food", "rule", 0.75, 1)
    assert tuple(untouched) == ("UNKNOWN", "unknown", 1)
    assert get_transaction_tag_names(core_conn, target_id) == ["Service"]
    assert get_transaction_tag_names(core_conn, other_id) == ["Tax"]


def test_suggest_transaction_ai_category_does_not_update_rows(core_conn, monkeypatch):
    """Verify a one-off AI suggestion does not mutate transactions or rules."""
    account_id = core_conn.execute(
        insert(accounts_table).values(name="AI checking")
    ).inserted_primary_key[0]
    merchant_id = get_or_create_merchant_for_description(core_conn, "TVA SPORTS DIRECT")["id"]
    ids = []
    for fingerprint in ("ai-single-target", "ai-single-other"):
        ids.append(
            core_conn.execute(
                insert(transactions_table).values(
                    account_id=account_id,
                    merchant_id=merchant_id,
                    tx_date="2026-05-04",
                    description="TVA SPORTS DIRECT",
                    amount=20.68,
                    category="UNKNOWN",
                    category_id=resolve_category_id(core_conn, "UNKNOWN"),
                    needs_review=1,
                    category_source="unknown",
                    fingerprint=fingerprint,
                )
            ).inserted_primary_key[0]
        )
    core_conn.commit()
    captured = {}

    def classify_for_test(conn, transactions, rules, unknown_category, save_automatic_rules=True):
        """Return a deterministic one-row LLM decision."""
        del rules, unknown_category
        captured["save_automatic_rules"] = save_automatic_rules
        transactions_service.llm_module.record_llm_request_status("ok", requested_count=1, result_count=1)
        transactions[0].update(
            {
                "category": "Entertainment",
                "category_id": resolve_category_id(conn, "Entertainment"),
                "tags": ["Service"],
                "needs_review": 0,
                "category_source": "ai",
                "category_confidence": 0.96,
                "category_rule_id": None,
                "category_metadata": {
                    "decision_source": "llm",
                    "final_category": "Entertainment",
                    "final_tags": ["Service"],
                    "final_confidence": 0.96,
                    "llm_confidence": 0.96,
                    "llm_reason": "TVA Sports is a streaming sports service.",
                    "review_required": False,
                },
                "categorized_at": "2026-05-04T12:00:00Z",
                "reviewed_at": None,
            }
        )

    monkeypatch.setattr(transactions_service, "classify_unknowns_with_llm", classify_for_test)

    result = transactions_service.suggest_transaction_ai_category(ids[0])

    target = core_conn.execute(
        select(
            transactions_table.c.category,
            transactions_table.c.category_source,
            transactions_table.c.category_confidence,
            transactions_table.c.category_rule_id,
            transactions_table.c.needs_review,
        ).where(transactions_table.c.id == ids[0])
    ).mappings().fetchone()
    other = core_conn.execute(
        select(transactions_table.c.category).where(transactions_table.c.id == ids[1])
    ).scalar_one()
    rule_count = core_conn.execute(text("SELECT COUNT(*) AS count FROM category_rules")).fetchone()._mapping["count"]

    assert captured["save_automatic_rules"] is False
    assert result["ok"] is True
    assert result["applied"] is False
    assert result["can_apply"] is True
    assert result["category"] == "Entertainment"
    assert result["tags"] == ["Service"]
    assert result["llm_reason"] == "TVA Sports is a streaming sports service."
    assert result["persistence"]["category"] == "Entertainment"
    assert target["category"] == "UNKNOWN"
    assert target["category_source"] == "unknown"
    assert target["category_confidence"] is None
    assert target["category_rule_id"] is None
    assert target["needs_review"] == 1
    assert get_transaction_tag_names(core_conn, ids[0]) == []
    assert other == "UNKNOWN"
    assert rule_count == 0


def test_apply_transaction_ai_suggestion_updates_selected_row(core_conn, monkeypatch):
    """Verify accepting an AI suggestion applies only the selected transaction."""
    account_id = core_conn.execute(
        insert(accounts_table).values(name="AI checking apply")
    ).inserted_primary_key[0]
    merchant_id = get_or_create_merchant_for_description(core_conn, "TVA SPORTS DIRECT")["id"]
    ids = []
    for fingerprint in ("ai-apply-target", "ai-apply-other"):
        ids.append(
            core_conn.execute(
                insert(transactions_table).values(
                    account_id=account_id,
                    merchant_id=merchant_id,
                    tx_date="2026-05-04",
                    description="TVA SPORTS DIRECT",
                    amount=20.68,
                    category="UNKNOWN",
                    category_id=resolve_category_id(core_conn, "UNKNOWN"),
                    needs_review=1,
                    category_source="unknown",
                    fingerprint=fingerprint,
                )
            ).inserted_primary_key[0]
        )
    core_conn.commit()

    def classify_for_test(conn, transactions, rules, unknown_category, save_automatic_rules=True):
        """Return a deterministic one-row LLM decision."""
        del rules, unknown_category, save_automatic_rules
        transactions_service.llm_module.record_llm_request_status("ok", requested_count=1, result_count=1)
        transactions[0].update(
            {
                "category": "Entertainment",
                "category_id": resolve_category_id(conn, "Entertainment"),
                "tags": ["Service"],
                "needs_review": 1,
                "category_source": "ai",
                "category_confidence": 0.84,
                "category_rule_id": None,
                "category_metadata": {
                    "decision_source": "llm",
                    "final_category": "Entertainment",
                    "final_tags": ["Service"],
                    "final_confidence": 0.84,
                    "llm_confidence": 0.84,
                    "llm_reason": "TVA Sports is a streaming sports service.",
                    "review_required": True,
                },
                "categorized_at": "2026-05-04T12:00:00Z",
                "reviewed_at": None,
            }
        )

    monkeypatch.setattr(transactions_service, "classify_unknowns_with_llm", classify_for_test)
    suggestion = transactions_service.suggest_transaction_ai_category(ids[0])

    result = transactions_service.apply_transaction_ai_suggestion(ids[0], suggestion)

    target = core_conn.execute(
        select(
            transactions_table.c.category,
            transactions_table.c.category_source,
            transactions_table.c.category_confidence,
            transactions_table.c.category_rule_id,
            transactions_table.c.category_metadata,
            transactions_table.c.needs_review,
            transactions_table.c.reviewed_at,
        ).where(transactions_table.c.id == ids[0])
    ).mappings().fetchone()
    other = core_conn.execute(
        select(transactions_table.c.category).where(transactions_table.c.id == ids[1])
    ).scalar_one()
    rule_count = core_conn.execute(text("SELECT COUNT(*) AS count FROM category_rules")).fetchone()._mapping["count"]
    metadata = json.loads(target["category_metadata"])

    assert result["updated"] is True
    assert result["saved_rule_id"] is None
    assert target["category"] == "Entertainment"
    assert target["category_source"] == "ai"
    assert target["category_confidence"] == 0.84
    assert target["category_rule_id"] is None
    assert target["needs_review"] == 0
    assert target["reviewed_at"] is not None
    assert metadata["accepted_by_user"] is True
    assert metadata["review_required_before_acceptance"] is True
    assert get_transaction_tag_names(core_conn, ids[0]) == ["Service"]
    assert other == "UNKNOWN"
    assert rule_count == 0


def test_apply_transaction_ai_suggestion_can_create_rule(core_conn):
    """Verify accepting an AI suggestion can save a user-approved rule."""
    account_id = core_conn.execute(
        insert(accounts_table).values(name="AI checking rule")
    ).inserted_primary_key[0]
    merchant_id = get_or_create_merchant_for_description(core_conn, "TVA SPORTS DIRECT")["id"]
    tx_id = core_conn.execute(
        insert(transactions_table).values(
            account_id=account_id,
            merchant_id=merchant_id,
            tx_date="2026-05-04",
            description="TVA SPORTS DIRECT",
            amount=20.68,
            category="UNKNOWN",
            category_id=resolve_category_id(core_conn, "UNKNOWN"),
            needs_review=1,
            category_source="unknown",
            fingerprint="ai-apply-rule-target",
        )
    ).inserted_primary_key[0]
    core_conn.commit()
    suggestion = {
        "transaction_id": tx_id,
        "can_apply": True,
        "persistence": {
            "category": "Entertainment",
            "tags": ["Service"],
            "needs_review": 0,
            "category_source": "ai",
            "category_confidence": 0.96,
            "category_rule_id": None,
            "category_metadata": {"decision_source": "llm"},
            "categorized_at": "2026-05-04T12:00:00Z",
            "reviewed_at": None,
            "amount": 20.68,
            "transaction_kind": "expense",
        },
    }

    result = transactions_service.apply_transaction_ai_suggestion(
        tx_id,
        suggestion,
        action=transactions_service.APPLY_AI_SUGGESTION_WITH_RULE_ACTION,
        rule_keyword="TVA SPORTS DIRECT",
    )

    rule = core_conn.execute(text("""
        SELECT id, merchant_id, keyword, category, source, amount_min, amount_max
        FROM category_rules
        WHERE keyword = 'TVA SPORTS DIRECT'
        """)).fetchone()
    tx = core_conn.execute(
        select(transactions_table.c.category, transactions_table.c.needs_review)
        .where(transactions_table.c.id == tx_id)
    ).mappings().fetchone()

    assert result["updated"] is True
    assert result["saved_rule_id"] == rule._mapping["id"]
    assert result["message"] == "AI suggestion applied. Rule saved."
    assert rule._mapping["merchant_id"] == merchant_id
    assert rule._mapping["category"] == "Entertainment"
    assert rule._mapping["source"] == "manual"
    assert rule._mapping["amount_min"] is None
    assert rule._mapping["amount_max"] is None
    assert get_rule_tags_by_rule_id(core_conn, [rule._mapping["id"]])[rule._mapping["id"]] == ["Service"]
    assert tx["category"] == "Entertainment"
    assert tx["needs_review"] == 0
