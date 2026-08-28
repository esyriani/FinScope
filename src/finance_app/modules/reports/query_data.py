"""Database query bundles for Reports service contexts.

Reports routes parse request state in ``reports.service``. This module owns the
transactional read-side fetches and the dataclasses that carry raw query data to
presenters.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

from finance_app.core.analytics import QUICK_VIEW_ALL, QUICK_VIEW_CATEGORIZED
from finance_app.core.constants import UNKNOWN_CATEGORY
from finance_app.database.engine import db_core_transaction
from finance_app.modules.accounts.queries import list_account_options
from finance_app.modules.categories.service import get_category_options
from finance_app.modules.categories.taxonomy import get_tag_option_rows
from finance_app.modules.merchants.repository import find_merchant_by_id
from finance_app.modules.merchants.service import get_merchant_suggestion_limit
from finance_app.modules.reports.entities import (
    REPORT_ENTITY_ACCOUNT,
    REPORT_ENTITY_MERCHANT,
    ReportEntityTarget,
    resolve_account_report_target,
    resolve_merchant_report_target,
)
from finance_app.modules.reports.filters import (
    ReportRequest,
    report_taxonomy_filters_active,
)
from finance_app.modules.reports.queries import (
    account_target_condition,
    fetch_account_breakdown,
    fetch_category_breakdown,
    fetch_entity_target_options,
    fetch_merchant_breakdown,
    fetch_monthly_overview,
    fetch_reimbursable_tag_summary,
    fetch_reimbursement_category_summary,
    fetch_report_quick_view_counts,
    fetch_report_summary,
    fetch_tag_breakdown,
    fetch_taxonomy_category_rows,
    fetch_taxonomy_evidence_rows,
    fetch_taxonomy_tag_rows,
    fetch_taxonomy_target_options,
    income_credit_target_condition,
    merchant_target_condition,
    report_quick_view_conditions,
    report_taxonomy_filter_conditions,
    reports_base_filters,
    taxonomy_target_condition,
)
from finance_app.modules.reports.taxonomy import (
    TAXONOMY_TARGET_CATEGORY,
    TaxonomyReportTarget,
    resolve_taxonomy_report_target,
)
from finance_app.modules.settings.runtime import get_unknown_category


@dataclass(frozen=True)
class ReportsOverviewQueryData:
    """Database-backed rows for the Reports overview."""

    report_request: ReportRequest
    unknown_category: str
    account_options: list[dict[str, Any]]
    selected_merchant_label: str
    merchant_suggestion_limit: int
    quick_view_counts: dict[str, Any]
    summary: Any
    monthly_rows: list[dict[str, Any]]
    category_rows: list[Any]
    tag_rows: list[dict[str, Any]]
    account_rows: list[Any]
    merchant_rows: list[dict[str, Any]]


@dataclass(frozen=True)
class ReportsTaxonomyIndexQueryData:
    """Database-backed rows for the taxonomy report index."""

    report_request: ReportRequest
    unknown_category: str
    account_options: list[dict[str, Any]]
    selected_merchant_label: str
    merchant_suggestion_limit: int
    quick_view_counts: dict[str, Any]
    summary: Any
    category_rows: list[Any]
    tag_rows: list[Any]
    target_options: list[Any]


@dataclass(frozen=True)
class ReportsTaxonomyDetailQueryData:
    """Database-backed rows for a category or tag report detail page."""

    report_request: ReportRequest
    unknown_category: str
    account_options: list[dict[str, Any]]
    selected_merchant_label: str
    merchant_suggestion_limit: int
    quick_view_counts: dict[str, Any]
    target: TaxonomyReportTarget
    summary: Any
    monthly_rows: list[dict[str, Any]]
    composition_rows: list[Any]
    account_rows: list[Any]
    merchant_rows: list[dict[str, Any]]
    evidence_rows: list[dict[str, Any]]
    semantic_summary: Any
    target_options: list[Any]


@dataclass(frozen=True)
class ReportsEntityIndexQueryData:
    """Database-backed rows for account and merchant report indexes."""

    report_request: ReportRequest
    unknown_category: str
    account_options: list[dict[str, Any]]
    selected_merchant_label: str
    merchant_suggestion_limit: int
    quick_view_counts: dict[str, Any]
    summary: Any
    rows: list[Any]
    target_options: list[Any]
    category_options: list[str]
    tag_options: list[dict[str, str]]


@dataclass(frozen=True)
class ReportsEntityDetailQueryData:
    """Database-backed rows for an account or merchant report detail page."""

    report_request: ReportRequest
    unknown_category: str
    account_options: list[dict[str, Any]]
    selected_merchant_label: str
    merchant_suggestion_limit: int
    quick_view_counts: dict[str, Any]
    target: ReportEntityTarget
    summary: Any
    monthly_rows: list[dict[str, Any]]
    category_rows: list[Any]
    tag_rows: list[Any]
    account_rows: list[Any]
    merchant_rows: list[dict[str, Any]]
    evidence_rows: list[dict[str, Any]]
    target_options: list[Any]
    category_options: list[str]
    tag_options: list[dict[str, str]]


@dataclass(frozen=True)
class ReportsIncomeQueryData:
    """Database-backed rows for the income and credits report."""

    report_request: ReportRequest
    unknown_category: str
    account_options: list[dict[str, Any]]
    selected_merchant_label: str
    merchant_suggestion_limit: int
    quick_view_counts: dict[str, Any]
    summary: Any
    monthly_rows: list[dict[str, Any]]
    category_rows: list[Any]
    tag_rows: list[Any]
    account_rows: list[Any]
    merchant_rows: list[dict[str, Any]]
    evidence_rows: list[dict[str, Any]]
    category_options: list[str]
    tag_options: list[dict[str, str]]


class EffectiveReportArgs:
    """Query-args adapter used when Reports resolves an implicit scope."""

    def __init__(self, source: Any, overrides: Mapping[str, object]) -> None:
        raw_values = source.to_dict(flat=False)
        self._values: dict[str, list[object]] = {}
        for key, value in raw_values.items():
            if isinstance(value, (list, tuple)):
                self._values[str(key)] = list(value)
            else:
                self._values[str(key)] = [value]

        for key, value in overrides.items():
            if value in (None, ""):
                self._values.pop(key, None)
            elif isinstance(value, (list, tuple)):
                self._values[key] = [item for item in value if item not in (None, "")]
            else:
                self._values[key] = [value]

    def get(self, key: str, default: object | None = None) -> object:
        """Return one query value for a key."""
        values = self._values.get(key)
        return values[0] if values else default

    def getlist(self, key: str) -> list[object]:
        """Return all query values for a repeated key."""
        return list(self._values.get(key, []))

    def to_dict(self, flat: bool = True) -> dict[str, object]:
        """Return query parameters as a dictionary."""
        if flat:
            return {key: values[0] for key, values in self._values.items() if values}
        return {key: list(values) for key, values in self._values.items() if values}


def effective_report_request(report_request: ReportRequest, quick_view_counts: Mapping[str, Any]) -> ReportRequest:
    """Return a report request that avoids an empty implicit categorized view."""
    explicit_quick_view = str(report_request.args.get("quick_view", "") or "").strip()
    categorized_count = int(quick_view_counts.get("categorized_count") or 0)
    all_count = int(quick_view_counts.get("all_count") or 0)
    if (
        not explicit_quick_view
        and report_request.quick_view == QUICK_VIEW_CATEGORIZED
        and categorized_count == 0
        and all_count > 0
    ):
        return replace(
            report_request,
            quick_view=QUICK_VIEW_ALL,
            args=EffectiveReportArgs(report_request.args, {"quick_view": QUICK_VIEW_ALL}),
        )
    return report_request


def fetch_reports_overview_query_data(report_request: ReportRequest) -> ReportsOverviewQueryData:
    """Fetch Reports overview aggregates inside one database transaction."""
    with db_core_transaction() as conn:
        unknown_category = get_unknown_category(conn) or UNKNOWN_CATEGORY
        base_filters = reports_base_filters(report_request).criteria()
        quick_view_counts = fetch_report_quick_view_counts(
            conn,
            base_filters,
            unknown_category,
            report_request.basis,
        )
        report_request = effective_report_request(report_request, quick_view_counts)
        filters = report_filters_with_quick_view(base_filters, report_request, unknown_category)
        account_options = list_account_options(conn)
        selected_merchant_label = selected_merchant_option_name(
            conn,
            report_request.selected_merchant_id,
            report_request.merchant_query,
        )
        return ReportsOverviewQueryData(
            report_request=report_request,
            unknown_category=unknown_category,
            account_options=account_options,
            selected_merchant_label=selected_merchant_label,
            merchant_suggestion_limit=get_merchant_suggestion_limit(conn),
            quick_view_counts=quick_view_counts,
            summary=fetch_report_summary(conn, filters, unknown_category, report_request.basis),
            monthly_rows=fetch_monthly_overview(conn, filters, report_request.basis),
            category_rows=fetch_category_breakdown(conn, filters, unknown_category, report_request.basis),
            tag_rows=fetch_tag_breakdown(conn, filters, report_request.basis),
            account_rows=fetch_account_breakdown(conn, filters, report_request.basis),
            merchant_rows=fetch_merchant_breakdown(conn, filters, report_request.basis),
        )


def fetch_reports_income_query_data(report_request: ReportRequest) -> ReportsIncomeQueryData:
    """Fetch income and credits report aggregates inside one database transaction."""
    with db_core_transaction() as conn:
        unknown_category = get_unknown_category(conn) or UNKNOWN_CATEGORY
        base_filters = reports_base_filters(report_request).criteria()
        income_scope_filters = [*base_filters, income_credit_target_condition(report_request.basis)]
        quick_view_counts = fetch_report_quick_view_counts(
            conn,
            income_scope_filters,
            unknown_category,
            report_request.basis,
        )
        report_request = effective_report_request(report_request, quick_view_counts)
        income_filters = report_filters_with_quick_view(income_scope_filters, report_request, unknown_category)
        account_options = list_account_options(conn)
        selected_merchant_label = selected_merchant_option_name(
            conn,
            report_request.selected_merchant_id,
            report_request.merchant_query,
        )
        return ReportsIncomeQueryData(
            report_request=report_request,
            unknown_category=unknown_category,
            account_options=account_options,
            selected_merchant_label=selected_merchant_label,
            merchant_suggestion_limit=get_merchant_suggestion_limit(conn),
            quick_view_counts=quick_view_counts,
            summary=fetch_report_summary(conn, income_filters, unknown_category, report_request.basis),
            monthly_rows=fetch_monthly_overview(conn, income_filters, report_request.basis),
            category_rows=fetch_category_breakdown(conn, income_filters, unknown_category, report_request.basis),
            tag_rows=fetch_tag_breakdown(conn, income_filters, report_request.basis),
            account_rows=fetch_account_breakdown(conn, income_filters, report_request.basis),
            merchant_rows=fetch_merchant_breakdown(conn, income_filters, report_request.basis),
            evidence_rows=fetch_taxonomy_evidence_rows(conn, income_filters, unknown_category, report_request.basis),
            category_options=report_category_filter_options(conn, unknown_category),
            tag_options=get_tag_option_rows(conn),
        )


def fetch_reports_taxonomy_index_query_data(report_request: ReportRequest) -> ReportsTaxonomyIndexQueryData:
    """Fetch taxonomy index aggregates inside one database transaction."""
    with db_core_transaction() as conn:
        unknown_category = get_unknown_category(conn) or UNKNOWN_CATEGORY
        base_filters = reports_base_filters(report_request).criteria()
        quick_view_counts = fetch_report_quick_view_counts(
            conn,
            base_filters,
            unknown_category,
            report_request.basis,
        )
        report_request = effective_report_request(report_request, quick_view_counts)
        filters = report_filters_with_quick_view(base_filters, report_request, unknown_category)
        account_options = list_account_options(conn)
        selected_merchant_label = selected_merchant_option_name(
            conn,
            report_request.selected_merchant_id,
            report_request.merchant_query,
        )
        return ReportsTaxonomyIndexQueryData(
            report_request=report_request,
            unknown_category=unknown_category,
            account_options=account_options,
            selected_merchant_label=selected_merchant_label,
            merchant_suggestion_limit=get_merchant_suggestion_limit(conn),
            quick_view_counts=quick_view_counts,
            summary=fetch_report_summary(conn, filters, unknown_category, report_request.basis),
            category_rows=fetch_taxonomy_category_rows(conn, filters, unknown_category, report_request.basis),
            tag_rows=fetch_taxonomy_tag_rows(conn, filters, report_request.basis),
            target_options=fetch_taxonomy_target_options(conn),
        )


def fetch_reports_taxonomy_detail_query_data(
    kind: str,
    target_id: int,
    report_request: ReportRequest,
) -> ReportsTaxonomyDetailQueryData:
    """Fetch scoped aggregates for one category or tag report target."""
    with db_core_transaction() as conn:
        unknown_category = get_unknown_category(conn) or UNKNOWN_CATEGORY
        target = resolve_taxonomy_report_target(conn, kind, target_id)
        if target is None:
            raise LookupError("Taxonomy report target was not found.")

        base_filters = reports_base_filters(report_request).criteria()
        target_scope_filters = [*base_filters, taxonomy_target_condition(target, unknown_category)]
        quick_view_counts = fetch_report_quick_view_counts(
            conn,
            target_scope_filters,
            unknown_category,
            report_request.basis,
        )
        report_request = effective_report_request(report_request, quick_view_counts)
        target_filters = report_filters_with_quick_view(target_scope_filters, report_request, unknown_category)
        account_options = list_account_options(conn)
        selected_merchant_label = selected_merchant_option_name(
            conn,
            report_request.selected_merchant_id,
            report_request.merchant_query,
        )
        composition_rows = (
            fetch_tag_breakdown(conn, target_filters, report_request.basis)
            if target.kind == TAXONOMY_TARGET_CATEGORY
            else fetch_category_breakdown(conn, target_filters, unknown_category, report_request.basis)
        )
        semantic_summary = None
        if target.is_reimbursable_tag:
            semantic_summary = fetch_reimbursable_tag_summary(conn, target_filters)
        elif target.is_reimbursement_category:
            semantic_summary = fetch_reimbursement_category_summary(conn, target_filters)

        return ReportsTaxonomyDetailQueryData(
            report_request=report_request,
            unknown_category=unknown_category,
            account_options=account_options,
            selected_merchant_label=selected_merchant_label,
            merchant_suggestion_limit=get_merchant_suggestion_limit(conn),
            quick_view_counts=quick_view_counts,
            target=target,
            summary=fetch_report_summary(conn, target_filters, unknown_category, report_request.basis),
            monthly_rows=fetch_monthly_overview(conn, target_filters, report_request.basis),
            composition_rows=composition_rows,
            account_rows=fetch_account_breakdown(conn, target_filters, report_request.basis),
            merchant_rows=fetch_merchant_breakdown(conn, target_filters, report_request.basis),
            evidence_rows=fetch_taxonomy_evidence_rows(
                conn,
                target_filters,
                unknown_category,
                report_request.basis,
                limit=5,
            ),
            semantic_summary=semantic_summary,
            target_options=fetch_taxonomy_target_options(conn),
        )


def fetch_reports_entity_index_query_data(kind: str, report_request: ReportRequest) -> ReportsEntityIndexQueryData:
    """Fetch account or merchant index aggregates inside one database transaction."""
    with db_core_transaction() as conn:
        unknown_category = get_unknown_category(conn) or UNKNOWN_CATEGORY
        base_filters = reports_base_filters(
            report_request,
            include_account=kind != REPORT_ENTITY_ACCOUNT,
            include_merchant=kind != REPORT_ENTITY_MERCHANT,
        ).criteria()
        quick_view_counts = fetch_report_quick_view_counts(
            conn,
            base_filters,
            unknown_category,
            report_request.basis,
        )
        report_request = effective_report_request(report_request, quick_view_counts)
        filters = report_filters_with_quick_view(base_filters, report_request, unknown_category)
        account_options = list_account_options(conn)
        selected_merchant_label = selected_merchant_option_name(
            conn,
            report_request.selected_merchant_id,
            report_request.merchant_query,
        )
        rows = (
            fetch_account_breakdown(conn, filters, report_request.basis)
            if kind == REPORT_ENTITY_ACCOUNT
            else fetch_merchant_breakdown(conn, filters, report_request.basis)
        )
        return ReportsEntityIndexQueryData(
            report_request=report_request,
            unknown_category=unknown_category,
            account_options=account_options,
            selected_merchant_label=selected_merchant_label,
            merchant_suggestion_limit=get_merchant_suggestion_limit(conn),
            quick_view_counts=quick_view_counts,
            summary=fetch_report_summary(conn, filters, unknown_category, report_request.basis),
            rows=rows,
            target_options=fetch_entity_target_options(conn, kind),
            category_options=report_category_filter_options(conn, unknown_category),
            tag_options=get_tag_option_rows(conn),
        )


def fetch_reports_entity_detail_query_data(
    kind: str,
    target_id: int,
    report_request: ReportRequest,
) -> ReportsEntityDetailQueryData:
    """Fetch scoped aggregates for one account or merchant report target."""
    with db_core_transaction() as conn:
        unknown_category = get_unknown_category(conn) or UNKNOWN_CATEGORY
        target = (
            resolve_account_report_target(conn, target_id)
            if kind == REPORT_ENTITY_ACCOUNT
            else resolve_merchant_report_target(conn, target_id)
        )
        if target is None:
            raise LookupError("Report target was not found.")

        base_filters = reports_base_filters(
            report_request,
            include_account=kind != REPORT_ENTITY_ACCOUNT,
            include_merchant=kind != REPORT_ENTITY_MERCHANT,
        ).criteria()
        target_filter = (
            account_target_condition(target.id)
            if kind == REPORT_ENTITY_ACCOUNT
            else merchant_target_condition(target.id)
        )
        target_scope_filters = [*base_filters, target_filter]
        quick_view_counts = fetch_report_quick_view_counts(
            conn,
            target_scope_filters,
            unknown_category,
            report_request.basis,
        )
        report_request = effective_report_request(report_request, quick_view_counts)
        target_filters = report_filters_with_quick_view(target_scope_filters, report_request, unknown_category)
        account_options = list_account_options(conn)
        selected_merchant_label = selected_merchant_option_name(
            conn,
            report_request.selected_merchant_id,
            report_request.merchant_query,
        )

        return ReportsEntityDetailQueryData(
            report_request=report_request,
            unknown_category=unknown_category,
            account_options=account_options,
            selected_merchant_label=selected_merchant_label,
            merchant_suggestion_limit=get_merchant_suggestion_limit(conn),
            quick_view_counts=quick_view_counts,
            target=target,
            summary=fetch_report_summary(conn, target_filters, unknown_category, report_request.basis),
            monthly_rows=fetch_monthly_overview(conn, target_filters, report_request.basis),
            category_rows=fetch_category_breakdown(conn, target_filters, unknown_category, report_request.basis),
            tag_rows=fetch_tag_breakdown(conn, target_filters, report_request.basis),
            account_rows=fetch_account_breakdown(conn, target_filters, report_request.basis),
            merchant_rows=fetch_merchant_breakdown(conn, target_filters, report_request.basis),
            evidence_rows=fetch_taxonomy_evidence_rows(
                conn,
                target_filters,
                unknown_category,
                report_request.basis,
                limit=5,
            ),
            target_options=fetch_entity_target_options(conn, kind),
            category_options=report_category_filter_options(conn, unknown_category),
            tag_options=get_tag_option_rows(conn),
        )


def report_filters_with_quick_view(
    filters: Sequence[Any],
    report_request: ReportRequest,
    unknown_category: str,
) -> list[Any]:
    """Return report filters plus the selected quick-view status shortcut."""
    taxonomy_filters = (
        report_taxonomy_filter_conditions(report_request, unknown_category)
        if report_taxonomy_filters_active(report_request)
        else []
    )
    return [
        *filters,
        *report_quick_view_conditions(report_request.quick_view, unknown_category),
        *taxonomy_filters,
    ]


def report_category_filter_options(conn: Any, unknown_category: str) -> list[str]:
    """Return category filter choices for categorized report refiners."""
    return [category for category in get_category_options(conn) if category != unknown_category]


def selected_merchant_option_name(conn: Any, selected_merchant_id: int | None, merchant_query: str = "") -> str:
    """Return the selected merchant label for report filter summaries and forms."""
    if selected_merchant_id is None:
        return merchant_query
    merchant = find_merchant_by_id(conn, selected_merchant_id)
    if merchant is None:
        return merchant_query
    return str(merchant["merchant_key"])
