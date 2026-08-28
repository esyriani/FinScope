"""Presentation shaping for Reports category and tag detail pages.

The helpers build taxonomy-target view models while reusing the shared Reports
row, URL, and chart helpers from the base presenter.
"""

from collections.abc import Mapping, Sequence
from typing import Any

from flask import url_for

from finance_app.core.analytics import REPORT_MEASURE_INCOME, REPORT_MEASURE_SPENDING, build_data_quality
from finance_app.core.money import rounded_money_float
from finance_app.core.urls import build_app_url
from finance_app.modules.reports.export_presenter import build_taxonomy_export_rows
from finance_app.modules.reports.filters import ReportRequest
from finance_app.modules.reports.presenter import (
    base_transaction_params,
    build_breakdown_rows,
    build_monthly_rows,
    build_summary_view,
    build_taxonomy_evidence_rows,
    build_taxonomy_target_options,
    report_comparison_url,
    selected_total_for_share,
)
from finance_app.modules.reports.taxonomy import TAXONOMY_TARGET_CATEGORY, TAXONOMY_TARGET_TAG, TaxonomyReportTarget
from finance_app.modules.reports.urls import reports_url
from finance_app.modules.transactions.constants import AMOUNT_TYPE_CREDIT, AMOUNT_TYPE_SPENDING


def build_reports_taxonomy_detail_view(
    report_request: ReportRequest,
    target: TaxonomyReportTarget,
    summary: Mapping[str, Any],
    monthly_rows: Sequence[Mapping[str, Any]],
    composition_rows: Sequence[Mapping[str, Any]],
    account_rows: Sequence[Mapping[str, Any]],
    merchant_rows: Sequence[Mapping[str, Any]],
    evidence_rows: Sequence[Mapping[str, Any]],
    semantic_summary: Mapping[str, Any] | None,
    target_options: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Build the taxonomy target detail view model."""
    summary_view = build_summary_view(summary)
    total_for_share = selected_total_for_share(summary_view, report_request.measure)
    monthly = build_monthly_rows(monthly_rows, report_request.measure, total_for_share)
    composition = build_breakdown_rows(
        composition_rows,
        report_request,
        total_for_share,
        target.composition_row_kind,
    )
    accounts = build_breakdown_rows(account_rows, report_request, total_for_share, "account")
    merchants = build_breakdown_rows(merchant_rows, report_request, total_for_share, "merchant")
    evidence = build_taxonomy_evidence_rows(evidence_rows)
    visible_composition = meaningful_taxonomy_composition_rows(composition, target)
    transaction_url = taxonomy_transactions_url(target, report_request)
    comparison_url = taxonomy_comparison_url(target, report_request)
    view = {
        **summary_view,
        "data_quality": build_data_quality(summary),
        "taxonomy_target": target,
        "taxonomy_notes": build_taxonomy_target_notes(target),
        "taxonomy_panel": build_taxonomy_panel(target, semantic_summary),
        "monthly_rows": monthly,
        "taxonomy_composition_rows": composition,
        "taxonomy_visible_composition_rows": visible_composition,
        "taxonomy_composition_empty_message": taxonomy_composition_empty_message(composition, target),
        "taxonomy_has_composition_chart": bool(visible_composition),
        "account_rows": accounts,
        "merchant_rows": merchants,
        "taxonomy_evidence_rows": evidence,
        "chart_data": build_taxonomy_detail_chart_data(monthly, visible_composition, target, report_request.measure),
        "transaction_url": transaction_url,
        "comparison_url": comparison_url,
        "taxonomy_target_options": build_taxonomy_target_options(target_options, report_request),
        "taxonomy_breadcrumbs": build_taxonomy_breadcrumbs(target, report_request),
        "taxonomy_back_url": reports_url(report_request.args, endpoint="reports.taxonomy"),
        "taxonomy_detail_subnav": build_taxonomy_detail_subnav(),
        "taxonomy_related_links": build_taxonomy_related_links(
            transaction_url,
            comparison_url,
            accounts,
            merchants,
        ),
    }
    return {
        **view,
        "taxonomy_export_rows": build_taxonomy_export_rows(view),
    }


def meaningful_taxonomy_composition_rows(
    rows: Sequence[Mapping[str, Any]],
    target: TaxonomyReportTarget,
) -> list[Mapping[str, Any]]:
    """Return composition rows worth charting and presenting as detail."""
    if target.kind != TAXONOMY_TARGET_CATEGORY:
        return list(rows)
    return [
        row for row in rows if not (row.get("untagged") or str(row.get("label") or "").strip().casefold() == "untagged")
    ]


def taxonomy_composition_empty_message(
    rows: Sequence[Mapping[str, Any]],
    target: TaxonomyReportTarget,
) -> str:
    """Return a concise message when taxonomy composition has no meaningful rows."""
    if target.kind == TAXONOMY_TARGET_CATEGORY and rows:
        meaningful = meaningful_taxonomy_composition_rows(rows, target)
        if not meaningful:
            return "No tags are used in this category."
    return "No composition rows are available for this report."


def build_taxonomy_breadcrumbs(
    target: TaxonomyReportTarget,
    report_request: ReportRequest,
) -> list[dict[str, str]]:
    """Return breadcrumbs for taxonomy detail pages."""
    return [
        {"label": "Reports", "url": reports_url(report_request.args, endpoint="reports.overview")},
        {"label": "Categories and tags", "url": reports_url(report_request.args, endpoint="reports.taxonomy")},
        {"label": target.name, "url": ""},
    ]


def build_taxonomy_detail_subnav() -> list[dict[str, str]]:
    """Return in-page section links for taxonomy detail reports."""
    return [
        {"label": "Summary", "target": "taxonomy-summary"},
        {"label": "Monthly", "target": "taxonomy-monthly"},
        {"label": "Composition", "target": "taxonomy-composition"},
        {"label": "Merchants", "target": "taxonomy-merchants"},
        {"label": "Transactions", "target": "taxonomy-transactions"},
    ]


def build_taxonomy_related_links(
    transaction_url: str,
    comparison_url: str,
    account_rows: Sequence[Mapping[str, Any]],
    merchant_rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, str]]:
    """Return related report links for a taxonomy detail page."""
    links = [
        {"label": "View transactions", "detail": "", "url": transaction_url, "icon": "list-ul"},
        {"label": "Compare this category/tag", "detail": "", "url": comparison_url, "icon": "layout-split"},
    ]
    top_account = next((row for row in account_rows if row.get("url")), None)
    top_merchant = next((row for row in merchant_rows if row.get("url")), None)
    if top_account:
        links.append(
            {
                "label": "Open related account report",
                "detail": str(top_account["label"]),
                "url": str(top_account["url"]),
                "icon": "bank",
            }
        )
    if top_merchant:
        links.append(
            {
                "label": "Open related merchant report",
                "detail": str(top_merchant["label"]),
                "url": str(top_merchant["url"]),
                "icon": "shop",
            }
        )
    return links


def taxonomy_transactions_url(target: TaxonomyReportTarget, report_request: ReportRequest) -> str:
    """Return a transaction-list URL matching a taxonomy target report."""
    params = base_transaction_params(report_request)
    params["filter_mode"] = "include"
    if report_request.measure == REPORT_MEASURE_SPENDING:
        params["amount_type"] = AMOUNT_TYPE_SPENDING
    elif report_request.measure == REPORT_MEASURE_INCOME:
        params["amount_type"] = AMOUNT_TYPE_CREDIT

    if target.kind == TAXONOMY_TARGET_CATEGORY:
        params["categories"] = [target.name]
    elif target.kind == TAXONOMY_TARGET_TAG:
        params["tags"] = [target.name]
    return build_app_url("transactions.transactions", **params)


def taxonomy_comparison_url(target: TaxonomyReportTarget, report_request: ReportRequest) -> str:
    """Return a Comparison URL scoped to a category or tag report target."""
    if target.kind == TAXONOMY_TARGET_CATEGORY:
        return report_comparison_url(report_request, categories=[target.name])
    return report_comparison_url(report_request, tags=[target.name])


def build_taxonomy_target_notes(target: TaxonomyReportTarget) -> list[dict[str, str]]:
    """Return semantic notes for special taxonomy report targets."""
    notes = []
    if target.is_tag:
        notes.append(
            {
                "level": "warning",
                "message": "Tag reports are non-exclusive, so one transaction can appear in more than one tag report.",
            }
        )
    if target.is_reimbursable_tag:
        notes.append(
            {
                "level": "info",
                "message": "Reimbursable expenses are summarized from reimbursement matches without showing edit controls.",
            }
        )
    if target.is_reimbursement_category:
        notes.append(
            {
                "level": "info",
                "message": "Reimbursement credits are summarized separately because reportable cash flow offsets them against expenses.",
            }
        )
    if target.is_tax_tag:
        notes.append(
            {
                "level": "info",
                "message": "Tax-tag exports emphasize the filtered evidence rows for year-end review.",
            }
        )
    if target.is_rental_category:
        notes.append(
            {
                "level": "info",
                "message": "Rental reports keep rental-property income and spending together for review.",
            }
        )
    return notes


def build_taxonomy_panel(
    target: TaxonomyReportTarget,
    semantic_summary: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    """Return a read-only side panel for special taxonomy semantics."""
    if semantic_summary is None:
        return None

    if target.is_reimbursable_tag:
        return {
            "title": "Reimbursable expense tracking",
            "description": "Matched and pending amounts are read from reimbursement records.",
            "action_url": url_for("reimbursements.reimbursements"),
            "action_label": "Open reimbursements",
            "metrics": [
                panel_metric("Tagged expenses", int(semantic_summary["transaction_count"] or 0)),
                panel_metric(
                    "Gross reimbursable spending", rounded_money_float(semantic_summary["gross_amount"]), True
                ),
                panel_metric("Matched reimbursements", rounded_money_float(semantic_summary["matched_amount"]), True),
                panel_metric("Pending reimbursement", rounded_money_float(semantic_summary["pending_amount"]), True),
                panel_metric("Open expenses", int(semantic_summary["pending_count"] or 0)),
                panel_metric("Completed expenses", int(semantic_summary["completed_count"] or 0)),
            ],
        }

    if target.is_reimbursement_category:
        return {
            "title": "Reimbursement credit tracking",
            "description": "Received credits and matched amounts are read from reimbursement records.",
            "action_url": url_for("reimbursements.reimbursements"),
            "action_label": "Open reimbursements",
            "metrics": [
                panel_metric("Received credits", int(semantic_summary["transaction_count"] or 0)),
                panel_metric("Received amount", rounded_money_float(semantic_summary["received_amount"]), True),
                panel_metric("Matched reimbursements", rounded_money_float(semantic_summary["matched_amount"]), True),
                panel_metric("Unmatched credits", rounded_money_float(semantic_summary["pending_amount"]), True),
                panel_metric("Open credits", int(semantic_summary["pending_count"] or 0)),
            ],
        }
    return None


def panel_metric(label: str, value: object, is_money: bool = False) -> dict[str, object]:
    """Return one side-panel metric."""
    return {"label": label, "value": value, "is_money": is_money}


def build_taxonomy_detail_chart_data(
    monthly_rows: Sequence[Mapping[str, Any]],
    composition_rows: Sequence[Mapping[str, Any]],
    target: TaxonomyReportTarget,
    measure: str,
) -> dict[str, Any]:
    """Return JSON-safe chart data for taxonomy detail reports."""
    return {
        "monthlyLabels": [row["label"] for row in monthly_rows],
        "monthlySpending": [row["spending"] for row in monthly_rows],
        "monthlyIncome": [row["income"] for row in monthly_rows],
        "monthlyNet": [row["net"] for row in monthly_rows],
        "compositionLabels": [row["label"] for row in composition_rows[:12]],
        "compositionValues": [row["selected_value"] for row in composition_rows[:12]],
        "compositionName": target.composition_label_heading,
        "measure": measure,
    }
