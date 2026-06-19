"""Presentation shaping for Reports overview data.

The presenter converts SQL row mappings into template-ready view models and a
flat export model. It keeps money as database values until the final display
and serialization boundary.
"""

from collections.abc import Mapping, Sequence
from typing import Any

from flask import url_for

from finance_app.core.money import rounded_money_float
from finance_app.modules.dashboard.presenter import build_cash_flow_summary, build_data_quality
from finance_app.modules.reports.constants import (
    REPORT_MEASURE_INCOME,
    REPORT_MEASURE_NET,
    REPORT_MEASURE_SPENDING,
)
from finance_app.modules.reports.entities import (
    REPORT_ENTITY_ACCOUNT,
    REPORT_ENTITY_MERCHANT,
    ReportEntityTarget,
)
from finance_app.modules.reports.filters import ReportRequest
from finance_app.modules.reports.taxonomy import (
    TAXONOMY_TARGET_CATEGORY,
    TAXONOMY_TARGET_TAG,
    TaxonomyReportTarget,
)
from finance_app.modules.reports.urls import build_app_url, reports_url
from finance_app.modules.transactions.constants import (
    AMOUNT_TYPE_CREDIT,
    AMOUNT_TYPE_SPENDING,
    IGNORED_FILTER_ACTIVE,
)


def metric_value(row: Mapping[str, Any], measure: str) -> float:
    """Return the selected measure value from a report row."""
    if measure == REPORT_MEASURE_INCOME:
        return rounded_money_float(row.get("income"))
    if measure == REPORT_MEASURE_NET:
        return rounded_money_float(row.get("net"))
    return rounded_money_float(row.get("spending"))


def build_summary_view(summary: Mapping[str, Any]) -> dict[str, Any]:
    """Return headline KPI values for a Reports overview."""
    total_spending = rounded_money_float(summary["total_spending"])
    total_income = rounded_money_float(summary["total_income"])
    net_cashflow = rounded_money_float(summary["net_cashflow"])
    cash_flow_summary = build_cash_flow_summary(total_income, total_spending)
    return {
        "total_spending": total_spending,
        "total_income": total_income,
        "net_cashflow": net_cashflow,
        "cash_flow_summary": {
            **cash_flow_summary,
            "net_cashflow": net_cashflow,
        },
        "transaction_count": summary["transaction_count"] or 0,
        "first_tx_date": summary["first_tx_date"],
        "last_tx_date": summary["last_tx_date"],
    }


def build_breakdown_rows(
    rows: Sequence[Mapping[str, Any]],
    report_request: ReportRequest,
    total_for_share: float,
    row_kind: str,
) -> list[dict[str, Any]]:
    """Return table-ready breakdown rows sorted by selected measure."""
    prepared: list[dict[str, Any]] = []
    for row in rows:
        label = str(row["label"] or "")
        selected_value = metric_value(row, report_request.measure)
        prepared.append(
            {
                "label": label,
                "spending": rounded_money_float(row.get("spending")),
                "income": rounded_money_float(row.get("income")),
                "net": rounded_money_float(row.get("net")),
                "transaction_count": int(row.get("transaction_count") or 0),
                "selected_value": selected_value,
                "share": round((selected_value / total_for_share) * 100, 1) if total_for_share else 0,
                "bar_width": 0,
                "url": breakdown_transactions_url(row_kind, row, report_request),
            }
        )

    prepared.sort(key=lambda row: (-float(row["selected_value"]), str(row["label"])))
    max_value = max((abs(float(row["selected_value"])) for row in prepared), default=0)
    for row in prepared:
        row["bar_width"] = round((abs(float(row["selected_value"])) / max_value) * 100, 1) if max_value else 0
    return prepared


def breakdown_transactions_url(row_kind: str, row: Mapping[str, Any], report_request: ReportRequest) -> str:
    """Return a transactions handoff URL for a Reports breakdown row."""
    params = base_transaction_params(report_request)
    if report_request.measure == REPORT_MEASURE_SPENDING:
        params["amount_type"] = AMOUNT_TYPE_SPENDING
    elif report_request.measure == REPORT_MEASURE_INCOME:
        params["amount_type"] = AMOUNT_TYPE_CREDIT

    if row_kind == "category":
        params["filter_mode"] = "include"
        params["categories"] = [row["label"]]
    elif row_kind == "tag" and not row.get("untagged"):
        params["filter_mode"] = "include"
        params["tags"] = [row["label"]]
    elif row_kind == "tag":
        return ""
    elif row_kind == "account":
        params["account_id"] = row.get("account_id")
    elif row_kind == "merchant":
        params["merchant_key"] = row.get("merchant_key") or row.get("label")

    return build_app_url("transactions.transactions", **params)


def base_transaction_params(report_request: ReportRequest) -> dict[str, object]:
    """Return transaction-list parameters matching Reports overview filters."""
    params: dict[str, object] = {
        "period": report_request.period,
        "ignored": IGNORED_FILTER_ACTIVE,
    }
    if report_request.period == "custom":
        params["date_from"] = report_request.date_from
        params["date_to"] = report_request.date_to
    if report_request.selected_account_id:
        params["account_id"] = report_request.selected_account_id
    if report_request.merchant_query:
        params["merchant_key"] = report_request.merchant_query
    return params


def build_monthly_rows(rows: Sequence[Mapping[str, Any]], measure: str, total_for_share: float) -> list[dict[str, Any]]:
    """Return monthly rows with presentation-safe numeric values."""
    prepared = [
        {
            "label": row["label"],
            "spending": rounded_money_float(row["spending"]),
            "income": rounded_money_float(row["income"]),
            "net": rounded_money_float(row["net"]),
            "transaction_count": int(row["transaction_count"] or 0),
            "selected_value": metric_value(row, measure),
            "share": 0,
            "bar_width": 0,
            "url": "",
        }
        for row in rows
    ]
    max_value = max((abs(float(row["selected_value"])) for row in prepared), default=0)
    for row in prepared:
        selected_value = abs(float(row["selected_value"]))
        row["share"] = round((selected_value / total_for_share) * 100, 1) if total_for_share else 0
        row["bar_width"] = round((selected_value / max_value) * 100, 1) if max_value else 0
    return prepared


def build_reports_overview_view(
    report_request: ReportRequest,
    summary: Mapping[str, Any],
    monthly_rows: Sequence[Mapping[str, Any]],
    category_rows: Sequence[Mapping[str, Any]],
    tag_rows: Sequence[Mapping[str, Any]],
    account_rows: Sequence[Mapping[str, Any]],
    merchant_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Build the Reports overview view model."""
    summary_view = build_summary_view(summary)
    total_for_share = abs(
        {
            REPORT_MEASURE_SPENDING: summary_view["total_spending"],
            REPORT_MEASURE_INCOME: summary_view["total_income"],
            REPORT_MEASURE_NET: summary_view["net_cashflow"],
        }[report_request.measure]
    )
    monthly = build_monthly_rows(monthly_rows, report_request.measure, total_for_share)
    category_breakdown = build_breakdown_rows(category_rows, report_request, total_for_share, "category")
    tag_breakdown = build_breakdown_rows(tag_rows, report_request, total_for_share, "tag")
    account_breakdown = build_breakdown_rows(account_rows, report_request, total_for_share, "account")
    merchant_breakdown = build_breakdown_rows(merchant_rows, report_request, total_for_share, "merchant")

    return {
        **summary_view,
        "data_quality": build_data_quality(summary),
        "monthly_rows": monthly,
        "category_rows": category_breakdown,
        "tag_rows": tag_breakdown,
        "account_rows": account_breakdown,
        "merchant_rows": merchant_breakdown,
        "chart_data": build_chart_data(monthly, category_breakdown, tag_breakdown, report_request.measure),
        "transaction_url": build_app_url("transactions.transactions", **base_transaction_params(report_request)),
        "comparison_url": url_for("comparison.comparison"),
    }


def selected_total_for_share(summary_view: Mapping[str, Any], measure: str) -> float:
    """Return the absolute total used for share calculations."""
    return abs(
        {
            REPORT_MEASURE_SPENDING: summary_view["total_spending"],
            REPORT_MEASURE_INCOME: summary_view["total_income"],
            REPORT_MEASURE_NET: summary_view["net_cashflow"],
        }[measure]
    )


def build_taxonomy_index_rows(
    rows: Sequence[Mapping[str, Any]],
    report_request: ReportRequest,
    total_for_share: float,
    kind: str,
) -> list[dict[str, Any]]:
    """Return table-ready taxonomy index rows."""
    prepared: list[dict[str, Any]] = []
    endpoint = "reports.category_report" if kind == TAXONOMY_TARGET_CATEGORY else "reports.tag_report"
    route_key = "category_id" if kind == TAXONOMY_TARGET_CATEGORY else "tag_id"
    for row in rows:
        target_id = row.get("id")
        selected_value = metric_value(row, report_request.measure)
        prepared.append(
            {
                "id": target_id,
                "kind": kind,
                "type_label": "Category" if kind == TAXONOMY_TARGET_CATEGORY else "Tag",
                "label": str(row["label"] or ""),
                "description": str(row.get("description") or ""),
                "builtin_key": str(row.get("builtin_key") or ""),
                "color": str(row.get("color") or ""),
                "spending": rounded_money_float(row.get("spending")),
                "income": rounded_money_float(row.get("income")),
                "net": rounded_money_float(row.get("net")),
                "transaction_count": int(row.get("transaction_count") or 0),
                "selected_value": selected_value,
                "share": round((selected_value / total_for_share) * 100, 1) if total_for_share else 0,
                "bar_width": 0,
                "url": (
                    reports_url(report_request.args, endpoint=endpoint, route_values={route_key: target_id})
                    if target_id
                    else ""
                ),
            }
        )

    prepared.sort(key=lambda row: (-float(row["selected_value"]), str(row["label"])))
    max_value = max((abs(float(row["selected_value"])) for row in prepared), default=0)
    for row in prepared:
        row["bar_width"] = round((abs(float(row["selected_value"])) / max_value) * 100, 1) if max_value else 0
    return prepared


def build_reports_taxonomy_index_view(
    report_request: ReportRequest,
    summary: Mapping[str, Any],
    category_rows: Sequence[Mapping[str, Any]],
    tag_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Build the taxonomy index view model."""
    summary_view = build_summary_view(summary)
    total_for_share = selected_total_for_share(summary_view, report_request.measure)
    return {
        **summary_view,
        "data_quality": build_data_quality(summary),
        "taxonomy_category_rows": build_taxonomy_index_rows(
            category_rows,
            report_request,
            total_for_share,
            TAXONOMY_TARGET_CATEGORY,
        ),
        "taxonomy_tag_rows": build_taxonomy_index_rows(
            tag_rows,
            report_request,
            total_for_share,
            TAXONOMY_TARGET_TAG,
        ),
        "transaction_url": build_app_url("transactions.transactions", **base_transaction_params(report_request)),
    }


def build_entity_index_rows(
    rows: Sequence[Mapping[str, Any]],
    report_request: ReportRequest,
    total_for_share: float,
    kind: str,
) -> list[dict[str, Any]]:
    """Return table-ready account or merchant report index rows."""
    prepared: list[dict[str, Any]] = []
    endpoint = "reports.account_report" if kind == REPORT_ENTITY_ACCOUNT else "reports.merchant_report"
    route_key = "account_id" if kind == REPORT_ENTITY_ACCOUNT else "merchant_id"
    id_key = "account_id" if kind == REPORT_ENTITY_ACCOUNT else "merchant_id"
    for row in rows:
        target_id = row.get(id_key)
        selected_value = metric_value(row, report_request.measure)
        account_type = str(row.get("account_type") or "")
        prepared.append(
            {
                "id": target_id,
                "kind": kind,
                "type_label": entity_row_type_label(kind, account_type),
                "label": str(row["label"] or ""),
                "description": "",
                "color": "",
                "spending": rounded_money_float(row.get("spending")),
                "income": rounded_money_float(row.get("income")),
                "net": rounded_money_float(row.get("net")),
                "transaction_count": int(row.get("transaction_count") or 0),
                "selected_value": selected_value,
                "share": round((selected_value / total_for_share) * 100, 1) if total_for_share else 0,
                "bar_width": 0,
                "url": (
                    reports_url(report_request.args, endpoint=endpoint, route_values={route_key: target_id})
                    if target_id
                    else ""
                ),
            }
        )

    prepared.sort(key=lambda row: (-float(row["selected_value"]), str(row["label"])))
    max_value = max((abs(float(row["selected_value"])) for row in prepared), default=0)
    for row in prepared:
        row["bar_width"] = round((abs(float(row["selected_value"])) / max_value) * 100, 1) if max_value else 0
    return prepared


def entity_row_type_label(kind: str, account_type: str) -> str:
    """Return an index row type label for an account or merchant."""
    if kind == REPORT_ENTITY_ACCOUNT:
        return {
            "checking": "Checking account",
            "savings": "Savings account",
            "credit_card": "Credit card",
        }.get(account_type, "Account")
    return "Merchant"


def build_reports_entity_index_view(
    report_request: ReportRequest,
    summary: Mapping[str, Any],
    rows: Sequence[Mapping[str, Any]],
    kind: str,
) -> dict[str, Any]:
    """Build the account or merchant report index view model."""
    summary_view = build_summary_view(summary)
    total_for_share = selected_total_for_share(summary_view, report_request.measure)
    return {
        **summary_view,
        "data_quality": build_data_quality(summary),
        "entity_rows": build_entity_index_rows(rows, report_request, total_for_share, kind),
        "entity_index_title": "Account reports" if kind == REPORT_ENTITY_ACCOUNT else "Merchant reports",
        "entity_index_label_heading": "Account" if kind == REPORT_ENTITY_ACCOUNT else "Merchant",
        "transaction_url": build_app_url("transactions.transactions", **base_transaction_params(report_request)),
    }


def build_reports_entity_detail_view(
    report_request: ReportRequest,
    target: ReportEntityTarget,
    summary: Mapping[str, Any],
    monthly_rows: Sequence[Mapping[str, Any]],
    category_rows: Sequence[Mapping[str, Any]],
    tag_rows: Sequence[Mapping[str, Any]],
    account_rows: Sequence[Mapping[str, Any]],
    merchant_rows: Sequence[Mapping[str, Any]],
    evidence_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Build an account or merchant report detail view model."""
    summary_view = build_summary_view(summary)
    total_for_share = selected_total_for_share(summary_view, report_request.measure)
    monthly = build_monthly_rows(monthly_rows, report_request.measure, total_for_share)
    categories = build_breakdown_rows(category_rows, report_request, total_for_share, "category")
    tags = build_breakdown_rows(tag_rows, report_request, total_for_share, "tag")
    accounts = build_breakdown_rows(account_rows, report_request, total_for_share, "account")
    merchants = build_breakdown_rows(merchant_rows, report_request, total_for_share, "merchant")
    evidence = build_taxonomy_evidence_rows(evidence_rows)
    view = {
        **summary_view,
        "data_quality": build_data_quality(summary),
        "entity_target": target,
        "monthly_rows": monthly,
        "category_rows": categories,
        "tag_rows": tags,
        "account_rows": accounts,
        "merchant_rows": merchants,
        "entity_evidence_rows": evidence,
        "chart_data": build_chart_data(monthly, categories, tags, report_request.measure),
        "transaction_url": entity_transactions_url(target, report_request),
        "comparison_url": url_for("comparison.comparison"),
    }
    return {
        **view,
        "entity_export_rows": build_entity_export_rows(view),
    }


def entity_transactions_url(target: ReportEntityTarget, report_request: ReportRequest) -> str:
    """Return a transaction-list URL matching an account or merchant report target."""
    params = base_transaction_params(report_request)
    if report_request.measure == REPORT_MEASURE_SPENDING:
        params["amount_type"] = AMOUNT_TYPE_SPENDING
    elif report_request.measure == REPORT_MEASURE_INCOME:
        params["amount_type"] = AMOUNT_TYPE_CREDIT

    if target.kind == REPORT_ENTITY_ACCOUNT:
        params["account_id"] = target.id
    elif target.kind == REPORT_ENTITY_MERCHANT:
        params["merchant_key"] = target.name
    return build_app_url("transactions.transactions", **params)


def build_reports_income_view(
    report_request: ReportRequest,
    summary: Mapping[str, Any],
    monthly_rows: Sequence[Mapping[str, Any]],
    category_rows: Sequence[Mapping[str, Any]],
    tag_rows: Sequence[Mapping[str, Any]],
    account_rows: Sequence[Mapping[str, Any]],
    merchant_rows: Sequence[Mapping[str, Any]],
    evidence_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Build the income and credits report view model."""
    summary_view = build_summary_view(summary)
    total_for_share = selected_total_for_share(summary_view, report_request.measure)
    monthly = build_monthly_rows(monthly_rows, report_request.measure, total_for_share)
    categories = build_breakdown_rows(category_rows, report_request, total_for_share, "category")
    tags = build_breakdown_rows(tag_rows, report_request, total_for_share, "tag")
    accounts = build_breakdown_rows(account_rows, report_request, total_for_share, "account")
    merchants = build_breakdown_rows(merchant_rows, report_request, total_for_share, "merchant")
    evidence = build_taxonomy_evidence_rows(evidence_rows)
    transaction_url = income_transactions_url(report_request)
    view = {
        **summary_view,
        "average_income_credit": (
            round(summary_view["total_income"] / summary_view["transaction_count"], 2)
            if summary_view["transaction_count"]
            else 0
        ),
        "data_quality": build_data_quality(summary),
        "monthly_rows": monthly,
        "category_rows": categories,
        "tag_rows": tags,
        "account_rows": accounts,
        "merchant_rows": merchants,
        "income_evidence_rows": evidence,
        "chart_data": build_chart_data(monthly, categories, tags, report_request.measure),
        "transaction_url": transaction_url,
        "comparison_url": url_for("comparison.comparison"),
    }
    return {
        **view,
        "income_export_rows": build_income_export_rows(view),
    }


def income_transactions_url(report_request: ReportRequest) -> str:
    """Return a transaction-list URL matching the income and credits report."""
    params = base_transaction_params(report_request)
    params["amount_type"] = AMOUNT_TYPE_CREDIT
    return build_app_url("transactions.transactions", **params)


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
    view = {
        **summary_view,
        "data_quality": build_data_quality(summary),
        "taxonomy_target": target,
        "taxonomy_notes": build_taxonomy_target_notes(target),
        "taxonomy_panel": build_taxonomy_panel(target, semantic_summary),
        "monthly_rows": monthly,
        "taxonomy_composition_rows": composition,
        "account_rows": accounts,
        "merchant_rows": merchants,
        "taxonomy_evidence_rows": evidence,
        "chart_data": build_taxonomy_detail_chart_data(monthly, composition, target, report_request.measure),
        "transaction_url": taxonomy_transactions_url(target, report_request),
        "comparison_url": url_for("comparison.comparison"),
    }
    return {
        **view,
        "taxonomy_export_rows": build_taxonomy_export_rows(view),
    }


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


def build_taxonomy_evidence_rows(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Return table-ready evidence rows for a taxonomy detail report."""
    return [
        {
            "id": row["id"],
            "date": row["tx_date"],
            "description": row["description"],
            "account_name": row.get("account_name") or "",
            "merchant_label": row.get("merchant_label") or "",
            "category": row.get("category") or "",
            "amount": rounded_money_float(row.get("amount")),
            "spending": rounded_money_float(row.get("spending")),
            "income": rounded_money_float(row.get("income")),
            "net": rounded_money_float(row.get("net")),
            "transaction_kind": row.get("transaction_kind") or "",
        }
        for row in rows
    ]


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


def build_chart_data(
    monthly_rows: Sequence[Mapping[str, Any]],
    category_rows: Sequence[Mapping[str, Any]],
    tag_rows: Sequence[Mapping[str, Any]],
    measure: str,
) -> dict[str, Any]:
    """Return JSON-safe chart data for Reports overview charts."""
    return {
        "monthlyLabels": [row["label"] for row in monthly_rows],
        "monthlySpending": [row["spending"] for row in monthly_rows],
        "monthlyIncome": [row["income"] for row in monthly_rows],
        "monthlyNet": [row["net"] for row in monthly_rows],
        "categoryLabels": [row["label"] for row in category_rows[:12]],
        "categoryValues": [row["selected_value"] for row in category_rows[:12]],
        "tagLabels": [row["label"] for row in tag_rows[:12]],
        "tagValues": [row["selected_value"] for row in tag_rows[:12]],
        "measure": measure,
    }


def build_export_rows(context: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Return flattened overview rows for CSV and Excel exports."""
    rows: list[dict[str, Any]] = [
        export_row("Summary", "Spending", context["total_spending"], "", "", context["transaction_count"]),
        export_row("Summary", "Income and credits", "", context["total_income"], "", context["transaction_count"]),
        export_row("Summary", "Net cash flow", "", "", context["net_cashflow"], context["transaction_count"]),
    ]
    for section, key in (
        ("Monthly", "monthly_rows"),
        ("Category", "category_rows"),
        ("Tag", "tag_rows"),
        ("Account", "account_rows"),
        ("Merchant", "merchant_rows"),
    ):
        for row in context[key]:
            rows.append(
                export_row(
                    section,
                    row["label"],
                    row["spending"],
                    row["income"],
                    row["net"],
                    row["transaction_count"],
                    row.get("share", ""),
                )
            )
    return rows


def build_taxonomy_export_rows(context: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Return flattened taxonomy detail rows for CSV and Excel exports."""
    target = context["taxonomy_target"]
    rows: list[dict[str, Any]] = [
        export_row(target.report_label, "Spending", context["total_spending"], "", "", context["transaction_count"]),
        export_row(
            target.report_label,
            "Income and credits",
            "",
            context["total_income"],
            "",
            context["transaction_count"],
        ),
        export_row(target.report_label, "Net cash flow", "", "", context["net_cashflow"], context["transaction_count"]),
    ]
    for section, key in (
        ("Monthly", "monthly_rows"),
        (target.composition_title, "taxonomy_composition_rows"),
        ("Account", "account_rows"),
        ("Merchant", "merchant_rows"),
    ):
        for row in context[key]:
            rows.append(
                export_row(
                    section,
                    row["label"],
                    row["spending"],
                    row["income"],
                    row["net"],
                    row["transaction_count"],
                    row.get("share", ""),
                )
            )
    for row in context["taxonomy_evidence_rows"]:
        rows.append(
            export_row(
                "Evidence",
                row["description"],
                row["spending"],
                row["income"],
                row["net"],
                1,
            )
        )
    return rows


def build_entity_export_rows(context: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Return flattened account or merchant detail rows for CSV and Excel exports."""
    target = context["entity_target"]
    rows: list[dict[str, Any]] = [
        export_row(target.report_label, "Spending", context["total_spending"], "", "", context["transaction_count"]),
        export_row(
            target.report_label,
            "Income and credits",
            "",
            context["total_income"],
            "",
            context["transaction_count"],
        ),
        export_row(target.report_label, "Net cash flow", "", "", context["net_cashflow"], context["transaction_count"]),
    ]
    for section, key in (
        ("Monthly", "monthly_rows"),
        ("Category", "category_rows"),
        ("Tag", "tag_rows"),
        ("Account", "account_rows"),
        ("Merchant", "merchant_rows"),
    ):
        if target.kind == REPORT_ENTITY_ACCOUNT and key == "account_rows":
            continue
        if target.kind == REPORT_ENTITY_MERCHANT and key == "merchant_rows":
            continue
        for row in context[key]:
            rows.append(
                export_row(
                    section,
                    row["label"],
                    row["spending"],
                    row["income"],
                    row["net"],
                    row["transaction_count"],
                    row.get("share", ""),
                )
            )
    for row in context["entity_evidence_rows"]:
        rows.append(
            export_row(
                "Evidence",
                row["description"],
                row["spending"],
                row["income"],
                row["net"],
                1,
            )
        )
    return rows


def build_income_export_rows(context: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Return flattened income and credits rows for CSV and Excel exports."""
    rows: list[dict[str, Any]] = [
        export_row(
            "Income and credits", "Income and credits", "", context["total_income"], "", context["transaction_count"]
        ),
        export_row(
            "Income and credits", "Net cash flow", "", "", context["net_cashflow"], context["transaction_count"]
        ),
        export_row("Income and credits", "Average credit", "", context["average_income_credit"], "", ""),
    ]
    for section, key in (
        ("Monthly", "monthly_rows"),
        ("Category", "category_rows"),
        ("Tag", "tag_rows"),
        ("Account", "account_rows"),
        ("Merchant", "merchant_rows"),
    ):
        for row in context[key]:
            rows.append(
                export_row(
                    section,
                    row["label"],
                    row["spending"],
                    row["income"],
                    row["net"],
                    row["transaction_count"],
                    row.get("share", ""),
                )
            )
    for row in context["income_evidence_rows"]:
        rows.append(
            export_row(
                "Evidence",
                row["description"],
                row["spending"],
                row["income"],
                row["net"],
                1,
            )
        )
    return rows


def export_row(
    section: str,
    label: str,
    spending: object,
    income: object,
    net: object,
    transactions: object,
    share: object = "",
) -> dict[str, Any]:
    """Return one normalized export row."""
    return {
        "section": section,
        "label": label,
        "spending": spending,
        "income_and_credits": income,
        "net_cash_flow": net,
        "transactions": transactions,
        "share_percent": share,
    }
