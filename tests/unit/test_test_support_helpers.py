"""Tests for shared test-support helpers.

Verifies that common test factories work with production-style SQLAlchemy Core
connections.
"""

from types import SimpleNamespace

from sqlalchemy import select
from tests.support.database import insert_rule, insert_transaction, set_owner_setting
from tests.support.jobs import capture_background_jobs

from finance_app.core.csrf import CSRF_FIELD_NAME, CSRF_HEADER_NAME
from finance_app.database.tables import (
    accounts as accounts_table,
)
from finance_app.database.tables import (
    category_rules as category_rules_table,
)
from finance_app.database.tables import (
    statements as statements_table,
)
from finance_app.database.tables import (
    tags as tags_table,
)
from finance_app.database.tables import (
    transactions as transactions_table,
)
from finance_app.database.tables import (
    user_settings as user_settings_table,
)
from finance_app.database.tables import (
    users as users_table,
)
from finance_app.modules.categories.taxonomy import get_rule_tags_by_rule_id, get_transaction_tag_names


def test_shared_database_helpers_accept_core_connection(core_conn):
    """Verify shared setup helpers work with a raw SQLAlchemy Core connection."""
    rule_id = insert_rule(core_conn, "SUPPORT MARKET", "Food", tags=["Tax"])
    transaction_id = insert_transaction(
        core_conn,
        description="Support market",
        amount=12.34,
        category="Food",
        needs_review=0,
        fingerprint="support-helper-core",
        tags=["Tax"],
    )
    set_owner_setting(core_conn, "default_table_page_size", 33)

    rule = (
        core_conn.execute(
            select(category_rules_table.c.keyword, category_rules_table.c.category).where(
                category_rules_table.c.id == rule_id
            )
        )
        .mappings()
        .one()
    )
    transaction = (
        core_conn.execute(
            select(transactions_table.c.description, transactions_table.c.category).where(
                transactions_table.c.id == transaction_id
            )
        )
        .mappings()
        .one()
    )
    owner_id = core_conn.execute(select(users_table.c.id).where(users_table.c.username == "owner")).scalar_one()
    setting = core_conn.execute(
        select(user_settings_table.c.value).where(
            user_settings_table.c.user_id == owner_id,
            user_settings_table.c.key == "default_table_page_size",
        )
    ).scalar_one()

    assert dict(rule) == {"keyword": "SUPPORT MARKET", "category": "Food"}
    assert dict(transaction) == {"description": "Support market", "category": "Food"}
    assert get_rule_tags_by_rule_id(core_conn, [rule_id])[rule_id] == ["Tax"]
    assert get_transaction_tag_names(core_conn, transaction_id) == ["Tax"]
    assert setting == "33"


def test_shared_data_factory_builds_domain_rows(data_factory, core_conn):
    """Verify the shared factory builds users, accounts, statements, tags, rules, and transactions."""
    user_id = data_factory.users.create(username="support-user", display_name="Support user")
    account_id = data_factory.accounts.create(name="Support checking")
    tag_id = data_factory.tags.create(name="Support tag", color="#123abc")
    statement_id = data_factory.statements.create(
        account_id=account_id,
        filename="support-statement.csv",
        checksum="support-statement-checksum",
    )
    rule_id = data_factory.rules.create(
        keyword="SUPPORT FACTORY",
        category="Food",
        tags=["Support tag"],
    )
    transaction_id = data_factory.transactions.create(
        account_id=account_id,
        statement_id=statement_id,
        description="Support factory transaction",
        category="Food",
        tags=["Support tag"],
    )

    user = (
        core_conn.execute(select(users_table.c.username, users_table.c.display_name).where(users_table.c.id == user_id))
        .mappings()
        .one()
    )
    account = core_conn.execute(select(accounts_table.c.name).where(accounts_table.c.id == account_id)).scalar_one()
    statement = (
        core_conn.execute(
            select(statements_table.c.filename, statements_table.c.account_id).where(
                statements_table.c.id == statement_id
            )
        )
        .mappings()
        .one()
    )
    tag = (
        core_conn.execute(select(tags_table.c.name, tags_table.c.color).where(tags_table.c.id == tag_id))
        .mappings()
        .one()
    )

    assert dict(user) == {"username": "support-user", "display_name": "Support user"}
    assert account == "Support checking"
    assert dict(statement) == {
        "filename": "support-statement.csv",
        "account_id": account_id,
    }
    assert dict(tag) == {"name": "Support tag", "color": "#123abc"}
    assert get_rule_tags_by_rule_id(core_conn, [rule_id])[rule_id] == ["Support tag"]
    assert get_transaction_tag_names(core_conn, transaction_id) == ["Support tag"]


def test_csrf_enabled_client_builds_form_and_json_requests(csrf_client):
    """Verify CSRF-enabled clients inject form fields and JSON headers."""
    assert csrf_client.form_data({"name": "value"}) == {
        "name": "value",
        CSRF_FIELD_NAME: csrf_client.token,
    }
    assert csrf_client.json_headers({"X-Test": "1"}) == {
        "X-Test": "1",
        CSRF_HEADER_NAME: csrf_client.token,
    }


def test_background_job_recorder_captures_submission_metadata(monkeypatch):
    """Verify shared job recorder captures submit metadata without running work."""
    target = SimpleNamespace(submit_background_job=lambda *args, **kwargs: "real")
    recorder = capture_background_jobs(monkeypatch, target, job_id="captured-123")

    def job_func():
        """Placeholder job function that should not run during capture."""
        raise AssertionError("Captured jobs must not run.")

    def undo_func():
        """Placeholder undo function stored as metadata."""
        return None

    job_id = target.submit_background_job(
        "Example job",
        job_func,
        "arg",
        undo_handler=undo_func,
        undo_args=("undo",),
        undo_kwargs={"force": True},
        queue="ai",
    )

    captured = recorder.single()
    assert job_id == "captured-123"
    assert captured.label == "Example job"
    assert captured.func is job_func
    assert captured.args == ("arg",)
    assert captured.undo_handler is undo_func
    assert captured.undo_args == ("undo",)
    assert captured.undo_kwargs == {"force": True}
    assert captured.kwargs == {"queue": "ai"}
