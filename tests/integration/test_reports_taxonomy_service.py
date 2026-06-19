"""Service-level tests for Reports taxonomy contexts."""

from decimal import Decimal

from sqlalchemy import select
from tests.support.context_services import seed_reporting_data
from werkzeug.datastructures import MultiDict

from finance_app.core.constants import REIMBURSEMENT_CATEGORY, TRANSACTION_KIND_EXPENSE, TRANSACTION_KIND_INCOME
from finance_app.database.tables import categories as categories_table
from finance_app.database.tables import tags as tags_table
from finance_app.modules.reimbursements.service import create_reimbursement_allocation
from finance_app.modules.reports.definitions import REPORT_TAXONOMY
from finance_app.modules.reports.service import build_reports_context, build_reports_taxonomy_detail_context
from finance_app.modules.reports.taxonomy import TAXONOMY_TARGET_CATEGORY, TAXONOMY_TARGET_TAG


def taxonomy_args() -> MultiDict:
    """Return a deterministic custom Reports period."""
    return MultiDict(
        [
            ("period", "custom"),
            ("date_from", "2026-01-01"),
            ("date_to", "2026-01-31"),
        ]
    )


def category_id(conn, name: str) -> int:
    """Return a persisted category id by name."""
    return conn.execute(select(categories_table.c.id).where(categories_table.c.name == name)).scalar_one()


def tag_id(conn, name: str) -> int:
    """Return a persisted tag id by name."""
    return conn.execute(select(tags_table.c.id).where(tags_table.c.name == name)).scalar_one()


def rows_by_label(rows):
    """Return report rows keyed by label."""
    return {row["label"]: row for row in rows}


def test_taxonomy_index_lists_category_and_tag_targets(app, core_conn):
    """Verify taxonomy index rows resolve active category and tag report targets."""
    seed_reporting_data(core_conn)

    with app.test_request_context("/reports/taxonomy"):
        context = build_reports_context(REPORT_TAXONOMY, taxonomy_args())

    category_rows = rows_by_label(context["taxonomy_category_rows"])
    tag_rows = rows_by_label(context["taxonomy_tag_rows"])
    assert context["active_report_section"].key == REPORT_TAXONOMY
    assert category_rows["Food"]["spending"] == 140.00
    assert category_rows["Food"]["url"].startswith("/reports/categories/")
    assert tag_rows["Tax"]["spending"] == 100.00
    assert tag_rows["Tax"]["url"].startswith("/reports/tags/")


def test_category_detail_scopes_to_category_and_uses_tag_composition(app, core_conn):
    """Verify a category detail report scopes summary, composition, and evidence rows."""
    seed_reporting_data(core_conn)
    food_id = category_id(core_conn, "Food")

    with app.test_request_context(f"/reports/categories/{food_id}"):
        context = build_reports_taxonomy_detail_context(TAXONOMY_TARGET_CATEGORY, food_id, taxonomy_args())

    composition = rows_by_label(context["taxonomy_composition_rows"])
    evidence_descriptions = {row["description"] for row in context["taxonomy_evidence_rows"]}
    assert context["taxonomy_target"].name == "Food"
    assert context["total_spending"] == 140.00
    assert context["transaction_count"] == 2
    assert composition["Tax"]["spending"] == 100.00
    assert composition["Shared"]["spending"] == 40.00
    assert evidence_descriptions == {"Metro Grocery", "Cafe Bistro"}
    assert "categories=Food" in context["transaction_url"]
    assert context["taxonomy_panel"] is None


def test_tag_detail_scopes_to_tag_and_marks_non_exclusive(app, core_conn):
    """Verify a tag detail report scopes rows and exposes tag semantics."""
    seed_reporting_data(core_conn)
    tax_id = tag_id(core_conn, "Tax")

    with app.test_request_context(f"/reports/tags/{tax_id}"):
        context = build_reports_taxonomy_detail_context(TAXONOMY_TARGET_TAG, tax_id, taxonomy_args())

    composition = rows_by_label(context["taxonomy_composition_rows"])
    note_messages = {note["message"] for note in context["taxonomy_notes"]}
    assert context["taxonomy_target"].name == "Tax"
    assert context["total_spending"] == 100.00
    assert context["transaction_count"] == 1
    assert composition["Food"]["spending"] == 100.00
    assert "tags=Tax" in context["transaction_url"]
    assert "Tag reports are non-exclusive, so one transaction can appear in more than one tag report." in note_messages
    assert "Tax-tag exports emphasize the filtered evidence rows for year-end review." in note_messages


def test_reimbursement_targets_add_read_only_tracking_panels(app, core_conn, data_factory):
    """Verify reimbursement built-ins expose read-only matched and pending summaries."""
    expense_id = data_factory.transactions.create(
        description="Conference hotel",
        amount=Decimal("1000.00"),
        tx_date="2026-01-09",
        category="Travel",
        transaction_kind=TRANSACTION_KIND_EXPENSE,
        needs_review=0,
        tags=["Reimbursable"],
    )
    reimbursement_id = data_factory.transactions.create(
        description="Employer reimbursement",
        amount=Decimal("-400.00"),
        tx_date="2026-01-20",
        category=REIMBURSEMENT_CATEGORY,
        transaction_kind=TRANSACTION_KIND_INCOME,
        needs_review=0,
    )
    create_reimbursement_allocation(reimbursement_id, expense_id, Decimal("400.00"), conn=core_conn)

    reimbursable_id = tag_id(core_conn, "Reimbursable")
    reimbursement_category_id = category_id(core_conn, REIMBURSEMENT_CATEGORY)

    with app.test_request_context(f"/reports/tags/{reimbursable_id}"):
        reimbursable_context = build_reports_taxonomy_detail_context(
            TAXONOMY_TARGET_TAG,
            reimbursable_id,
            taxonomy_args(),
        )
    with app.test_request_context(f"/reports/categories/{reimbursement_category_id}"):
        reimbursement_context = build_reports_taxonomy_detail_context(
            TAXONOMY_TARGET_CATEGORY,
            reimbursement_category_id,
            taxonomy_args(),
        )

    reimbursable_metrics = {row["label"]: row["value"] for row in reimbursable_context["taxonomy_panel"]["metrics"]}
    reimbursement_metrics = {row["label"]: row["value"] for row in reimbursement_context["taxonomy_panel"]["metrics"]}
    assert reimbursable_context["taxonomy_panel"]["title"] == "Reimbursable expense tracking"
    assert reimbursable_metrics["Gross reimbursable spending"] == 1000.00
    assert reimbursable_metrics["Matched reimbursements"] == 400.00
    assert reimbursable_metrics["Pending reimbursement"] == 600.00
    assert reimbursement_context["taxonomy_panel"]["title"] == "Reimbursement credit tracking"
    assert reimbursement_metrics["Received amount"] == 400.00
    assert reimbursement_metrics["Matched reimbursements"] == 400.00
