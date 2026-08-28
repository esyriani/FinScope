"""Presentation shaping for Reports overview data.

The presenter converts SQL row mappings into template-ready view models and a
flat export model. It keeps money as database values until the final display
and serialization boundary.
"""

from collections.abc import Mapping, Sequence
from typing import Any

from finance_app.core.analytics import (
    REPORT_MEASURE_INCOME,
    REPORT_MEASURE_NET,
    REPORT_MEASURE_SPENDING,
    build_cash_flow_summary,
    build_data_quality,
)
from finance_app.core.builtin_taxonomy import (
    BUILTIN_CATEGORY_TRANSFERS,
    BUILTIN_CATEGORY_UNKNOWN,
)
from finance_app.core.money import rounded_money_float
from finance_app.core.urls import build_app_url
from finance_app.modules.comparison.urls import build_comparison_url
from finance_app.modules.reports.definitions import REPORT_ACCOUNTS, REPORT_MERCHANTS
from finance_app.modules.reports.entities import (
    REPORT_ENTITY_ACCOUNT,
    REPORT_ENTITY_MERCHANT,
    ReportEntityTarget,
)
from finance_app.modules.reports.export_presenter import (
    build_entity_export_rows,
    build_income_export_rows,
)
from finance_app.modules.reports.filters import ReportRequest, report_taxonomy_filters_active
from finance_app.modules.reports.taxonomy import (
    TAXONOMY_TARGET_CATEGORY,
    TAXONOMY_TARGET_TAG,
)
from finance_app.modules.reports.urls import reports_url
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
                "url": breakdown_report_url(row_kind, row, report_request),
            }
        )

    prepared.sort(key=lambda row: (-float(row["selected_value"]), str(row["label"])))
    max_value = max((abs(float(row["selected_value"])) for row in prepared), default=0)
    for row in prepared:
        row["bar_width"] = round((abs(float(row["selected_value"])) / max_value) * 100, 1) if max_value else 0
    return prepared


def breakdown_report_url(row_kind: str, row: Mapping[str, Any], report_request: ReportRequest) -> str:
    """Return a Reports drilldown URL for a breakdown row."""
    if row_kind == "category":
        target_id = row.get("category_id")
        return (
            reports_url(
                report_request.args, endpoint="reports.category_report", route_values={"category_id": target_id}
            )
            if target_id
            else reports_url(report_request.args, endpoint="reports.taxonomy")
        )
    if row_kind == "tag":
        target_id = row.get("tag_id")
        if row.get("untagged"):
            return ""
        return (
            reports_url(report_request.args, endpoint="reports.tag_report", route_values={"tag_id": target_id})
            if target_id
            else reports_url(report_request.args, endpoint="reports.taxonomy")
        )
    if row_kind == "account":
        target_id = row.get("account_id")
        return (
            reports_url(
                report_request.args,
                endpoint="reports.account_report",
                route_values={"account_id": target_id},
                account_id=None,
            )
            if target_id
            else ""
        )
    if row_kind == "merchant":
        target_id = row.get("merchant_id")
        if target_id:
            return reports_url(
                report_request.args,
                endpoint="reports.merchant_report",
                route_values={"merchant_id": target_id},
                merchant_id=None,
                merchant_query=None,
                merchant_search=None,
            )
        merchant_query = row.get("merchant_key") or row.get("label")
        if merchant_query:
            return reports_url(
                report_request.args,
                endpoint="reports.merchants",
                merchant_id=None,
                merchant_query=merchant_query,
            )
        return ""
    return ""


def base_transaction_params(report_request: ReportRequest) -> dict[str, object]:
    """Return transaction-list parameters matching Reports overview filters."""
    params: dict[str, object] = {
        "period": report_request.period,
        "ignored": IGNORED_FILTER_ACTIVE,
    }
    if report_request.period == "custom":
        params["date_from"] = report_request.date_from
        params["date_to"] = report_request.date_to
    if report_request.selected_account_id and report_request.section_key != REPORT_ACCOUNTS:
        params["account_id"] = report_request.selected_account_id
    if report_request.merchant_query and report_request.section_key != REPORT_MERCHANTS:
        params["merchant_key"] = report_request.merchant_query
    if report_taxonomy_filters_active(report_request):
        if report_request.selected_categories:
            params["categories"] = report_request.selected_categories
        if report_request.selected_tags:
            params["tags"] = report_request.selected_tags
    return params


def report_comparison_url(
    report_request: ReportRequest,
    *,
    categories: Sequence[str] | None = None,
    tags: Sequence[str] | None = None,
    account_id: int | None = None,
    merchant_id: int | None = None,
    merchant_query: str | None = None,
    analysis_mode: str | None = None,
) -> str:
    """Return a Comparison URL pre-filtered for the current report scope."""
    period_categories = list(categories) if categories is not None else []
    period_tags = list(tags) if tags is not None else []
    if categories is None and tags is None and report_taxonomy_filters_active(report_request):
        period_categories = report_request.selected_categories
        period_tags = report_request.selected_tags
    params: dict[str, object] = {
        "comparison_view": "period",
        "analysis_mode": analysis_mode or report_request.measure,
        "period_categories": period_categories,
        "period_tags": period_tags,
    }
    if account_id is not None:
        params["account_id"] = account_id
    elif report_request.selected_account_id is not None and report_request.section_key != REPORT_ACCOUNTS:
        params["account_id"] = report_request.selected_account_id

    if merchant_id is not None:
        params["merchant_id"] = merchant_id
        params["merchant_query"] = merchant_query or ""
    elif report_request.selected_merchant_id is not None and report_request.section_key != REPORT_MERCHANTS:
        params["merchant_id"] = report_request.selected_merchant_id
        params["merchant_query"] = report_request.merchant_query
    elif report_request.merchant_query and report_request.section_key != REPORT_MERCHANTS:
        params["merchant_query"] = report_request.merchant_query

    return build_comparison_url(**params)


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
        "comparison_url": report_comparison_url(report_request),
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
    """Return explorer-ready taxonomy index rows."""
    prepared: list[dict[str, Any]] = []
    for row in rows:
        target_id = row.get("id")
        selected_value = metric_value(row, report_request.measure)
        label = str(row["label"] or "")
        builtin_key = str(row.get("builtin_key") or "")
        url = taxonomy_target_report_url(kind, target_id, report_request)
        prepared.append(
            {
                "id": target_id,
                "kind": kind,
                "type_label": "Category" if kind == TAXONOMY_TARGET_CATEGORY else "Tag",
                "label": label,
                "description": str(row.get("description") or ""),
                "builtin_key": builtin_key,
                "color": str(row.get("color") or ""),
                "spending": rounded_money_float(row.get("spending")),
                "income": rounded_money_float(row.get("income")),
                "net": rounded_money_float(row.get("net")),
                "transaction_count": int(row.get("transaction_count") or 0),
                "selected_value": selected_value,
                "share": round((selected_value / total_for_share) * 100, 1) if total_for_share else 0,
                "bar_width": 0,
                "url": url,
                "transactions_url": taxonomy_row_transactions_url(kind, label, report_request),
                "comparison_url": taxonomy_row_comparison_url(kind, label, report_request),
                "target_key": taxonomy_target_key(kind, target_id),
                "is_builtin": bool(builtin_key),
                "needs_review": kind == TAXONOMY_TARGET_CATEGORY and builtin_key == BUILTIN_CATEGORY_UNKNOWN,
                "is_analytics_category": is_analytics_taxonomy_category(kind, builtin_key, label),
                "has_income": rounded_money_float(row.get("income")) > 0,
                "has_spending": rounded_money_float(row.get("spending")) > 0,
                "search_text": taxonomy_search_text(label, kind, builtin_key, row.get("description")),
            }
        )

    prepared.sort(key=lambda row: (-float(row["selected_value"]), str(row["label"])))
    max_value = max((abs(float(row["selected_value"])) for row in prepared), default=0)
    for row in prepared:
        row["bar_width"] = round((abs(float(row["selected_value"])) / max_value) * 100, 1) if max_value else 0
    return prepared


def taxonomy_target_key(kind: str, target_id: object) -> str:
    """Return a stable key for a taxonomy category or tag target."""
    return f"{kind}:{target_id}"


def taxonomy_target_report_url(kind: str, target_id: object, report_request: ReportRequest) -> str:
    """Return a report-detail URL for a category or tag target."""
    if not target_id:
        return ""
    endpoint = "reports.category_report" if kind == TAXONOMY_TARGET_CATEGORY else "reports.tag_report"
    route_key = "category_id" if kind == TAXONOMY_TARGET_CATEGORY else "tag_id"
    return reports_url(report_request.args, endpoint=endpoint, route_values={route_key: target_id})


def taxonomy_row_transactions_url(kind: str, label: str, report_request: ReportRequest) -> str:
    """Return a transaction-list URL scoped to a taxonomy row."""
    params = base_transaction_params(report_request)
    params["filter_mode"] = "include"
    if report_request.measure == REPORT_MEASURE_SPENDING:
        params["amount_type"] = AMOUNT_TYPE_SPENDING
    elif report_request.measure == REPORT_MEASURE_INCOME:
        params["amount_type"] = AMOUNT_TYPE_CREDIT
    if kind == TAXONOMY_TARGET_CATEGORY:
        params["categories"] = [label]
    else:
        params["tags"] = [label]
    return build_app_url("transactions.transactions", **params)


def taxonomy_row_comparison_url(kind: str, label: str, report_request: ReportRequest) -> str:
    """Return a Comparison URL scoped to a taxonomy row."""
    if kind == TAXONOMY_TARGET_CATEGORY:
        return report_comparison_url(report_request, categories=[label])
    return report_comparison_url(report_request, tags=[label])


def is_analytics_taxonomy_category(kind: str, builtin_key: str, label: str) -> bool:
    """Return whether a target belongs in ordinary category analytics."""
    if kind != TAXONOMY_TARGET_CATEGORY:
        return False
    normalized_label = label.strip().casefold()
    return builtin_key not in {BUILTIN_CATEGORY_TRANSFERS, BUILTIN_CATEGORY_UNKNOWN} and normalized_label not in {
        "transfers",
        "unknown",
    }


def taxonomy_search_text(label: str, kind: str, builtin_key: str, description: object) -> str:
    """Return lowercase text used by browser-side taxonomy target search."""
    return " ".join(
        item.casefold()
        for item in (
            label,
            "Category" if kind == TAXONOMY_TARGET_CATEGORY else "Tag",
            builtin_key,
            str(description or ""),
        )
        if item
    )


def build_taxonomy_target_options(
    rows: Sequence[Mapping[str, Any]],
    report_request: ReportRequest,
) -> list[dict[str, Any]]:
    """Return all category and tag targets available for direct navigation."""
    prepared = []
    for row in rows:
        kind = str(row["kind"])
        target_id = row.get("id")
        builtin_key = str(row.get("builtin_key") or "")
        label = str(row["label"] or "")
        prepared.append(
            {
                "id": target_id,
                "kind": kind,
                "type_label": "Category" if kind == TAXONOMY_TARGET_CATEGORY else "Tag",
                "label": label,
                "display_label": f"{label} ({'Category' if kind == TAXONOMY_TARGET_CATEGORY else 'Tag'})",
                "description": str(row.get("description") or ""),
                "builtin_key": builtin_key,
                "color": str(row.get("color") or ""),
                "url": taxonomy_target_report_url(kind, target_id, report_request),
                "target_key": taxonomy_target_key(kind, target_id),
                "is_builtin": bool(builtin_key),
                "search_text": taxonomy_search_text(label, kind, builtin_key, row.get("description")),
            }
        )

    return sorted(
        prepared,
        key=lambda row: (
            not row["is_builtin"],
            str(row["type_label"]),
            str(row["label"]).casefold(),
        ),
    )


def build_taxonomy_filter_chips(explorer_rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Return client-side filter chip metadata for the taxonomy explorer."""
    rows = list(explorer_rows)
    return [
        {"value": "all", "label": "All", "count": len(rows), "active": True},
        {
            "value": "categories",
            "label": "Categories",
            "count": sum(1 for row in rows if row["kind"] == TAXONOMY_TARGET_CATEGORY),
            "active": False,
        },
        {
            "value": "tags",
            "label": "Tags",
            "count": sum(1 for row in rows if row["kind"] == TAXONOMY_TARGET_TAG),
            "active": False,
        },
        {
            "value": "analytics-categories",
            "label": "Analytics categories",
            "count": sum(1 for row in rows if row["is_analytics_category"]),
            "active": False,
        },
        {
            "value": "has-income",
            "label": "Has income",
            "count": sum(1 for row in rows if row["has_income"]),
            "active": False,
        },
        {
            "value": "has-spending",
            "label": "Has spending",
            "count": sum(1 for row in rows if row["has_spending"]),
            "active": False,
        },
    ]


def build_reports_taxonomy_index_view(
    report_request: ReportRequest,
    summary: Mapping[str, Any],
    category_rows: Sequence[Mapping[str, Any]],
    tag_rows: Sequence[Mapping[str, Any]],
    target_options: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Build the taxonomy index view model."""
    summary_view = build_summary_view(summary)
    total_for_share = selected_total_for_share(summary_view, report_request.measure)
    data_quality = build_data_quality(summary)
    category_index_rows = build_taxonomy_index_rows(
        category_rows,
        report_request,
        total_for_share,
        TAXONOMY_TARGET_CATEGORY,
    )
    tag_index_rows = build_taxonomy_index_rows(
        tag_rows,
        report_request,
        total_for_share,
        TAXONOMY_TARGET_TAG,
    )
    explorer_rows = sorted(
        [*category_index_rows, *tag_index_rows],
        key=lambda row: (-float(row["selected_value"]), str(row["label"])),
    )
    target_option_rows = build_taxonomy_target_options(target_options, report_request)
    return {
        **summary_view,
        "data_quality": data_quality,
        "taxonomy_category_rows": category_index_rows,
        "taxonomy_tag_rows": tag_index_rows,
        "taxonomy_explorer_rows": explorer_rows,
        "taxonomy_target_options": target_option_rows,
        "taxonomy_filter_chips": build_taxonomy_filter_chips(explorer_rows),
        "transaction_url": build_app_url("transactions.transactions", **base_transaction_params(report_request)),
        "comparison_url": report_comparison_url(report_request),
    }


def build_entity_index_rows(
    rows: Sequence[Mapping[str, Any]],
    report_request: ReportRequest,
    total_for_share: float,
    kind: str,
) -> list[dict[str, Any]]:
    """Return explorer-ready account or merchant report index rows."""
    prepared: list[dict[str, Any]] = []
    id_key = "account_id" if kind == REPORT_ENTITY_ACCOUNT else "merchant_id"
    for row in rows:
        target_id = row.get(id_key)
        selected_value = metric_value(row, report_request.measure)
        account_type = str(row.get("account_type") or "")
        label = str(row["label"] or "")
        type_label = entity_row_type_label(kind, account_type)
        url = entity_target_report_url(kind, target_id, report_request)
        prepared.append(
            {
                "id": target_id,
                "kind": kind,
                "type_label": type_label,
                "label": label,
                "description": "",
                "color": "",
                "spending": rounded_money_float(row.get("spending")),
                "income": rounded_money_float(row.get("income")),
                "net": rounded_money_float(row.get("net")),
                "transaction_count": int(row.get("transaction_count") or 0),
                "selected_value": selected_value,
                "share": round((selected_value / total_for_share) * 100, 1) if total_for_share else 0,
                "bar_width": 0,
                "url": url,
                "transactions_url": entity_row_transactions_url(kind, target_id, label, report_request),
                "comparison_url": entity_row_comparison_url(kind, target_id, label, report_request),
                "target_key": entity_target_key(kind, target_id),
                "account_type": account_type,
                "has_income": rounded_money_float(row.get("income")) > 0,
                "has_spending": rounded_money_float(row.get("spending")) > 0,
                "filter_tokens": entity_filter_tokens(kind, account_type, row),
                "search_text": entity_search_text(label, type_label, account_type),
            }
        )

    prepared.sort(key=lambda row: (-float(row["selected_value"]), str(row["label"])))
    max_value = max((abs(float(row["selected_value"])) for row in prepared), default=0)
    for row in prepared:
        row["bar_width"] = round((abs(float(row["selected_value"])) / max_value) * 100, 1) if max_value else 0
    return prepared


def entity_target_key(kind: str, target_id: object) -> str:
    """Return a stable key for an account or merchant report target."""
    return f"{kind}:{target_id}"


def entity_target_report_url(kind: str, target_id: object, report_request: ReportRequest) -> str:
    """Return a report-detail URL for an account or merchant target."""
    if not target_id:
        return ""
    endpoint = "reports.account_report" if kind == REPORT_ENTITY_ACCOUNT else "reports.merchant_report"
    route_key = "account_id" if kind == REPORT_ENTITY_ACCOUNT else "merchant_id"
    params: dict[str, object | None] = {}
    if kind == REPORT_ENTITY_ACCOUNT:
        params["account_id"] = None
    else:
        params["merchant_id"] = None
        params["merchant_query"] = None
        params["merchant_search"] = None
    return reports_url(
        report_request.args,
        endpoint=endpoint,
        route_values={route_key: target_id},
        **params,
    )


def entity_row_transactions_url(
    kind: str,
    target_id: object,
    label: str,
    report_request: ReportRequest,
) -> str:
    """Return a transaction-list URL scoped to an account or merchant row."""
    params = base_transaction_params(report_request)
    if report_request.measure == REPORT_MEASURE_SPENDING:
        params["amount_type"] = AMOUNT_TYPE_SPENDING
    elif report_request.measure == REPORT_MEASURE_INCOME:
        params["amount_type"] = AMOUNT_TYPE_CREDIT
    if kind == REPORT_ENTITY_ACCOUNT:
        params["account_id"] = target_id
    else:
        params["merchant_key"] = label
    return build_app_url("transactions.transactions", **params)


def entity_row_comparison_url(
    kind: str,
    target_id: object,
    label: str,
    report_request: ReportRequest,
) -> str:
    """Return a Comparison URL scoped to an account or merchant row."""
    if target_id is None:
        return ""
    try:
        target_value = int(str(target_id))
    except ValueError:
        return ""
    if kind == REPORT_ENTITY_ACCOUNT:
        return report_comparison_url(report_request, account_id=target_value)
    return report_comparison_url(report_request, merchant_id=target_value, merchant_query=label)


def entity_filter_tokens(kind: str, account_type: str, row: Mapping[str, Any]) -> str:
    """Return client-side filter tokens for an account or merchant explorer row."""
    tokens = []
    if kind == REPORT_ENTITY_ACCOUNT:
        token = account_type.replace("_", "-")
        if token:
            tokens.append(token)
    if rounded_money_float(row.get("income")) > 0:
        tokens.append("has-income")
    if rounded_money_float(row.get("spending")) > 0:
        tokens.append("has-spending")
    return " ".join(tokens)


def entity_search_text(label: str, type_label: str, account_type: str = "") -> str:
    """Return lowercase text used by browser-side account and merchant search."""
    return " ".join(item.casefold() for item in (label, type_label, account_type.replace("_", " ")) if item)


def entity_row_type_label(kind: str, account_type: str) -> str:
    """Return an index row type label for an account or merchant."""
    if kind == REPORT_ENTITY_ACCOUNT:
        return {
            "checking": "Checking account",
            "savings": "Savings account",
            "credit_card": "Credit card",
        }.get(account_type, "Account")
    return "Merchant"


def build_entity_target_options(
    rows: Sequence[Mapping[str, Any]],
    report_request: ReportRequest,
    kind: str,
) -> list[dict[str, Any]]:
    """Return all account or merchant targets available for direct navigation."""
    prepared = []
    id_key = "id"
    label_key = "name" if kind == REPORT_ENTITY_ACCOUNT else "merchant_key"
    for row in rows:
        target_id = row.get(id_key)
        account_type = str(row.get("account_type") or "")
        label = str(row.get(label_key) or row.get("label") or "")
        type_label = entity_row_type_label(kind, account_type)
        prepared.append(
            {
                "id": target_id,
                "kind": kind,
                "type_label": type_label,
                "label": label,
                "display_label": f"{label} ({type_label})",
                "description": account_type.replace("_", " "),
                "url": entity_target_report_url(kind, target_id, report_request),
                "target_key": entity_target_key(kind, target_id),
                "search_text": entity_search_text(label, type_label, account_type),
            }
        )
    return sorted(prepared, key=lambda row: str(row["label"]).casefold())


def build_entity_filter_chips(
    explorer_rows: Sequence[Mapping[str, Any]],
    kind: str,
) -> list[dict[str, Any]]:
    """Return client-side filter chip metadata for an account or merchant explorer."""
    rows = list(explorer_rows)
    chips = [{"value": "all", "label": "All", "count": len(rows), "active": True}]
    if kind == REPORT_ENTITY_ACCOUNT:
        account_chips = [
            ("checking", "Checking accounts"),
            ("savings", "Savings accounts"),
            ("credit-card", "Credit cards"),
        ]
        for value, label in account_chips:
            count = sum(1 for row in rows if value in str(row["filter_tokens"]).split())
            if count:
                chips.append({"value": value, "label": label, "count": count, "active": False})
    chips.extend(
        [
            {
                "value": "has-income",
                "label": "Has income",
                "count": sum(1 for row in rows if row["has_income"]),
                "active": False,
            },
            {
                "value": "has-spending",
                "label": "Has spending",
                "count": sum(1 for row in rows if row["has_spending"]),
                "active": False,
            },
        ]
    )
    return chips


def build_reports_entity_index_view(
    report_request: ReportRequest,
    summary: Mapping[str, Any],
    rows: Sequence[Mapping[str, Any]],
    kind: str,
    target_options: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Build the account or merchant report index view model."""
    summary_view = build_summary_view(summary)
    total_for_share = selected_total_for_share(summary_view, report_request.measure)
    explorer_rows = build_entity_index_rows(rows, report_request, total_for_share, kind)
    return {
        **summary_view,
        "data_quality": build_data_quality(summary),
        "entity_rows": explorer_rows,
        "entity_explorer_rows": explorer_rows,
        "entity_index_title": "Account reports" if kind == REPORT_ENTITY_ACCOUNT else "Merchant reports",
        "entity_index_label_heading": "Account" if kind == REPORT_ENTITY_ACCOUNT else "Merchant",
        "entity_open_label": (
            "Open an account report..." if kind == REPORT_ENTITY_ACCOUNT else "Open a merchant report..."
        ),
        "entity_search_placeholder": "Search accounts" if kind == REPORT_ENTITY_ACCOUNT else "Search merchants",
        "entity_filter_chips": build_entity_filter_chips(explorer_rows, kind),
        "entity_target_options": build_entity_target_options(target_options, report_request, kind),
        "transaction_url": build_app_url("transactions.transactions", **base_transaction_params(report_request)),
        "comparison_url": report_comparison_url(report_request),
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
    target_options: Sequence[Mapping[str, Any]],
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
        "comparison_url": entity_comparison_url(target, report_request),
        "entity_target_options": build_entity_target_options(target_options, report_request, target.kind),
        "entity_breadcrumbs": build_entity_breadcrumbs(target, report_request),
        "entity_back_url": entity_back_url(target, report_request),
        "entity_detail_subnav": build_entity_detail_subnav(target),
        "entity_related_links": build_entity_related_links(
            target,
            entity_transactions_url(target, report_request),
            entity_comparison_url(target, report_request),
            categories,
            tags,
            accounts,
            merchants,
        ),
    }
    return {
        **view,
        "entity_export_rows": build_entity_export_rows(view),
    }


def entity_back_url(target: ReportEntityTarget, report_request: ReportRequest) -> str:
    """Return a state-preserving account or merchant report index URL."""
    if target.kind == REPORT_ENTITY_ACCOUNT:
        return reports_url(report_request.args, endpoint="reports.accounts", account_id=None)
    return reports_url(
        report_request.args,
        endpoint="reports.merchants",
        merchant_id=None,
        merchant_query=None,
        merchant_search=None,
    )


def build_entity_breadcrumbs(
    target: ReportEntityTarget,
    report_request: ReportRequest,
) -> list[dict[str, str]]:
    """Return breadcrumbs for account and merchant detail pages."""
    index_label = "Accounts" if target.kind == REPORT_ENTITY_ACCOUNT else "Merchants"
    index_endpoint = "reports.accounts" if target.kind == REPORT_ENTITY_ACCOUNT else "reports.merchants"
    return [
        {"label": "Reports", "url": reports_url(report_request.args, endpoint="reports.overview")},
        {"label": index_label, "url": reports_url(report_request.args, endpoint=index_endpoint)},
        {"label": target.name, "url": ""},
    ]


def build_entity_detail_subnav(target: ReportEntityTarget) -> list[dict[str, str]]:
    """Return in-page section links for account and merchant detail reports."""
    related_label = "Merchants" if target.kind == REPORT_ENTITY_ACCOUNT else "Accounts"
    related_target = "entity-merchants" if target.kind == REPORT_ENTITY_ACCOUNT else "entity-accounts"
    return [
        {"label": "Summary", "target": "entity-summary"},
        {"label": "Monthly", "target": "entity-monthly"},
        {"label": "Composition", "target": "entity-composition"},
        {"label": related_label, "target": related_target},
        {"label": "Transactions", "target": "entity-transactions"},
    ]


def build_entity_related_links(
    target: ReportEntityTarget,
    transaction_url: str,
    comparison_url: str,
    category_rows: Sequence[Mapping[str, Any]],
    tag_rows: Sequence[Mapping[str, Any]],
    account_rows: Sequence[Mapping[str, Any]],
    merchant_rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, str]]:
    """Return related report links for an account or merchant detail page."""
    links = [
        {"label": "View transactions", "detail": "", "url": transaction_url, "icon": "list-ul"},
        {"label": "Compare this report", "detail": "", "url": comparison_url, "icon": "layout-split"},
    ]
    top_category = next((row for row in category_rows if row.get("url")), None)
    top_tag = next((row for row in tag_rows if row.get("url")), None)
    top_account = next((row for row in account_rows if row.get("url")), None)
    top_merchant = next((row for row in merchant_rows if row.get("url")), None)
    if top_category:
        links.append(
            {
                "label": "Open related category report",
                "detail": str(top_category["label"]),
                "url": str(top_category["url"]),
                "icon": "tags",
            }
        )
    if top_tag:
        links.append(
            {
                "label": "Open related tag report",
                "detail": str(top_tag["label"]),
                "url": str(top_tag["url"]),
                "icon": "tag",
            }
        )
    if target.kind == REPORT_ENTITY_ACCOUNT and top_merchant:
        links.append(
            {
                "label": "Open related merchant report",
                "detail": str(top_merchant["label"]),
                "url": str(top_merchant["url"]),
                "icon": "shop",
            }
        )
    elif target.kind == REPORT_ENTITY_MERCHANT and top_account:
        links.append(
            {
                "label": "Open related account report",
                "detail": str(top_account["label"]),
                "url": str(top_account["url"]),
                "icon": "bank",
            }
        )
    return links


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


def entity_comparison_url(target: ReportEntityTarget, report_request: ReportRequest) -> str:
    """Return a Comparison URL scoped to an account or merchant report target."""
    if target.kind == REPORT_ENTITY_ACCOUNT:
        return report_comparison_url(report_request, account_id=target.id)
    return report_comparison_url(report_request, merchant_id=target.id, merchant_query=target.name)


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
        "comparison_url": report_comparison_url(report_request, analysis_mode=REPORT_MEASURE_INCOME),
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
