"""Read-side SQLAlchemy Core queries for Reports.

Queries in this module return detached row mappings for report presenters. They
use the shared reporting semantic helpers for reportable cash-flow basis and
plain ledger signs for ledger-row basis.
"""

from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import Any

from sqlalchemy import and_, case, exists, func, or_, select, true

from finance_app.core.money import money_to_decimal
from finance_app.core.periods import PERIOD_CUSTOM, period_start_date
from finance_app.core.query import CoreFilters
from finance_app.core.reporting import (
    cashflow_amount_expression,
    income_amount_expression,
    income_or_tagged_transfer_credit_clause,
    reportable_transaction_clause,
    spending_impact_amount_expression,
    spending_impact_clause,
)
from finance_app.database.dates import date_month, date_year, month_label
from finance_app.database.tables import accounts as accounts_table
from finance_app.database.tables import categories as categories_table
from finance_app.database.tables import merchants as merchants_table
from finance_app.database.tables import reimbursement_allocations as reimbursement_allocations_table
from finance_app.database.tables import reimbursement_expense_completions as expense_completions_table
from finance_app.database.tables import tags as tags_table
from finance_app.database.tables import transaction_tags as transaction_tags_table
from finance_app.database.tables import transactions as transactions_table
from finance_app.modules.accounts.filters import account_filter_condition
from finance_app.modules.categories.sources import (
    CATEGORY_SOURCE_AI,
    CATEGORY_SOURCE_HISTORY,
    CATEGORY_SOURCE_MANUAL,
    CATEGORY_SOURCE_RULE,
)
from finance_app.modules.dashboard.constants import (
    QUICK_VIEW_CATEGORIZED,
    QUICK_VIEW_NEEDS_REVIEW,
    QUICK_VIEW_UNKNOWN,
)
from finance_app.modules.merchants.filters import merchant_filter_condition
from finance_app.modules.merchants.repository import merchant_identity_from_row
from finance_app.modules.reports.constants import REPORT_BASIS_CASH_FLOW, REPORT_BASIS_LEDGER
from finance_app.modules.reports.filters import ReportRequest
from finance_app.modules.reports.taxonomy import (
    TAXONOMY_TARGET_CATEGORY,
    TAXONOMY_TARGET_TAG,
    TaxonomyReportTarget,
)


def reports_base_filters(
    report_request: ReportRequest,
    *,
    include_account: bool = True,
    include_merchant: bool = True,
) -> CoreFilters:
    """Return date, account, and merchant filters shared by Reports queries."""
    filters = CoreFilters()
    filters.add(transactions_table.c.ignored == 0)
    if include_account:
        filters.add(account_filter_condition(report_request.selected_account_id))
    if include_merchant:
        filters.add(merchant_filter_condition(report_request.selected_merchant_id, report_request.merchant_query))
    start_date = period_start_date(report_request.period)
    if start_date:
        filters.add(transactions_table.c.tx_date >= start_date)
    if report_request.period == PERIOD_CUSTOM:
        if report_request.date_from:
            filters.add(transactions_table.c.tx_date >= report_request.date_from)
        if report_request.date_to:
            filters.add(transactions_table.c.tx_date <= report_request.date_to)
    return filters


def category_label_expression(unknown_category: str) -> Any:
    """Return the category label expression used by report rows."""
    return func.coalesce(transactions_table.c.category, unknown_category)


def category_lookup_join_condition(unknown_category: str) -> Any:
    """Return the category join condition for rows with legacy cached labels."""
    category_label = category_label_expression(unknown_category)
    return or_(
        categories_table.c.id == transactions_table.c.category_id,
        and_(
            transactions_table.c.category_id.is_(None),
            func.lower(categories_table.c.name) == func.lower(category_label),
        ),
    )


def taxonomy_target_condition(target: TaxonomyReportTarget, unknown_category: str) -> Any:
    """Return the transaction predicate for a category or tag report target."""
    if target.kind == TAXONOMY_TARGET_CATEGORY:
        category_label = category_label_expression(unknown_category)
        return or_(
            transactions_table.c.category_id == target.id,
            func.lower(category_label) == target.name.casefold(),
        )
    if target.kind == TAXONOMY_TARGET_TAG:
        return exists(
            select(1).where(
                transaction_tags_table.c.transaction_id == transactions_table.c.id,
                transaction_tags_table.c.tag_id == target.id,
            )
        )
    return true()


def account_target_condition(account_id: int) -> Any:
    """Return the transaction predicate for an account report target."""
    return transactions_table.c.account_id == account_id


def merchant_target_condition(merchant_id: int) -> Any:
    """Return the transaction predicate for a merchant report target."""
    return transactions_table.c.merchant_id == merchant_id


def income_credit_target_condition(basis: str) -> Any:
    """Return the transaction predicate for rows contributing income and credits."""
    return income_credit_amount_expression(basis) > 0


def report_scope_clause(basis: str) -> Any:
    """Return the row scope predicate for a Reports basis."""
    return reportable_transaction_clause() if basis == REPORT_BASIS_CASH_FLOW else true()


def report_quick_view_conditions(quick_view: str, unknown_category: str) -> list[Any]:
    """Return SQL predicates for a Reports quick-view shortcut."""
    category = category_label_expression(unknown_category)
    if quick_view == QUICK_VIEW_NEEDS_REVIEW:
        return [transactions_table.c.needs_review == 1]
    if quick_view == QUICK_VIEW_UNKNOWN:
        return [category == unknown_category]
    if quick_view == QUICK_VIEW_CATEGORIZED:
        return [category != unknown_category, transactions_table.c.needs_review == 0]
    return []


def spending_amount_expression(basis: str) -> Any:
    """Return the positive outflow amount expression for a Reports basis."""
    if basis == REPORT_BASIS_LEDGER:
        return case((transactions_table.c.amount > 0, transactions_table.c.amount), else_=0)
    return case((spending_impact_clause(), spending_impact_amount_expression()), else_=0)


def income_credit_amount_expression(basis: str) -> Any:
    """Return the positive credit amount expression for a Reports basis."""
    if basis == REPORT_BASIS_LEDGER:
        return case((transactions_table.c.amount < 0, -transactions_table.c.amount), else_=0)
    return case(
        (
            and_(transactions_table.c.amount < 0, income_or_tagged_transfer_credit_clause(False)),
            income_amount_expression(),
        ),
        else_=0,
    )


def net_cash_flow_expression(basis: str) -> Any:
    """Return the signed net cash-flow expression for a Reports basis."""
    if basis == REPORT_BASIS_LEDGER:
        return -transactions_table.c.amount
    return cashflow_amount_expression()


def aggregate_columns(basis: str) -> tuple[Any, Any, Any, Any]:
    """Return standard aggregate columns for report breakdowns."""
    return (
        func.coalesce(func.sum(spending_amount_expression(basis)), 0).label("spending"),
        func.coalesce(func.sum(income_credit_amount_expression(basis)), 0).label("income"),
        func.coalesce(func.sum(net_cash_flow_expression(basis)), 0).label("net"),
        func.count().label("transaction_count"),
    )


def fetch_report_summary(
    conn: Any,
    filters: Sequence[Any],
    unknown_category: str,
    basis: str,
) -> Mapping[str, Any]:
    """Fetch headline totals and data-quality counters for a Reports scope."""
    category = func.coalesce(transactions_table.c.category, unknown_category)
    categorized = category != unknown_category
    scope = report_scope_clause(basis)
    spending_amount = spending_amount_expression(basis)
    has_tag = exists(select(1).where(transaction_tags_table.c.transaction_id == transactions_table.c.id))
    untagged_spending = spending_amount > 0
    return (
        conn.execute(
            select(
                func.coalesce(func.sum(spending_amount), 0).label("total_spending"),
                func.coalesce(func.sum(income_credit_amount_expression(basis)), 0).label("total_income"),
                func.coalesce(func.sum(net_cash_flow_expression(basis)), 0).label("net_cashflow"),
                func.count().label("transaction_count"),
                func.coalesce(func.avg(func.abs(transactions_table.c.amount)), 0).label("average_transaction_amount"),
                func.coalesce(func.sum(case((category == unknown_category, 1), else_=0)), 0).label(
                    "uncategorized_count"
                ),
                func.coalesce(func.sum(case((untagged_spending & ~has_tag, 1), else_=0)), 0).label(
                    "untagged_spending_count"
                ),
                func.coalesce(func.sum(case((untagged_spending & ~has_tag, spending_amount), else_=0)), 0).label(
                    "untagged_spending_total"
                ),
                func.coalesce(
                    func.sum(
                        case(
                            (
                                (transactions_table.c.needs_review == 1) & (category == unknown_category),
                                1,
                            ),
                            else_=0,
                        )
                    ),
                    0,
                ).label("unknown_needs_review_count"),
                func.coalesce(func.sum(case((categorized, 1), else_=0)), 0).label("categorized_count"),
                func.coalesce(func.sum(case((transactions_table.c.needs_review == 1, 1), else_=0)), 0).label(
                    "needs_review_count"
                ),
                func.coalesce(func.sum(case((transactions_table.c.reviewed_at.is_not(None), 1), else_=0)), 0).label(
                    "manually_reviewed_count"
                ),
                func.coalesce(
                    func.sum(case((categorized & (transactions_table.c.category_source == CATEGORY_SOURCE_RULE), 1))),
                    0,
                ).label("rule_count"),
                func.coalesce(
                    func.sum(
                        case((categorized & (transactions_table.c.category_source == CATEGORY_SOURCE_HISTORY), 1))
                    ),
                    0,
                ).label("history_count"),
                func.coalesce(
                    func.sum(case((categorized & (transactions_table.c.category_source == CATEGORY_SOURCE_AI), 1))),
                    0,
                ).label("ai_count"),
                func.coalesce(
                    func.sum(case((categorized & (transactions_table.c.category_source == CATEGORY_SOURCE_MANUAL), 1))),
                    0,
                ).label("manual_source_count"),
                func.min(transactions_table.c.tx_date).label("first_tx_date"),
                func.max(transactions_table.c.tx_date).label("last_tx_date"),
            ).where(scope, *filters)
        )
        .mappings()
        .fetchone()
    )


def fetch_report_quick_view_counts(
    conn: Any,
    filters: Sequence[Any],
    unknown_category: str,
    basis: str,
) -> dict[str, Any]:
    """Fetch quick-view counters for the current Reports scope."""
    category = category_label_expression(unknown_category)
    row = (
        conn.execute(
            select(
                func.count().label("all_count"),
                func.coalesce(
                    func.sum(case((transactions_table.c.needs_review == 1, 1), else_=0)),
                    0,
                ).label("needs_review_count"),
                func.coalesce(func.sum(case((category == unknown_category, 1), else_=0)), 0).label("unknown_count"),
                func.coalesce(
                    func.sum(
                        case(
                            (
                                (category != unknown_category) & (transactions_table.c.needs_review == 0),
                                1,
                            ),
                            else_=0,
                        )
                    ),
                    0,
                ).label("categorized_count"),
            ).where(report_scope_clause(basis), *filters)
        )
        .mappings()
        .fetchone()
    )
    return {
        "all_count": row["all_count"] or 0,
        "needs_review_count": row["needs_review_count"] or 0,
        "unknown_count": row["unknown_count"] or 0,
        "categorized_count": row["categorized_count"] or 0,
    }


def fetch_monthly_overview(conn: Any, filters: Sequence[Any], basis: str) -> list[dict[str, Any]]:
    """Fetch monthly spending, credits, and net cash flow rows."""
    year = date_year(transactions_table.c.tx_date)
    month = date_month(transactions_table.c.tx_date)
    rows = (
        conn.execute(
            select(
                year.label("year"),
                month.label("month"),
                *aggregate_columns(basis),
            )
            .where(report_scope_clause(basis), *filters)
            .group_by(year, month)
            .order_by(year, month)
        )
        .mappings()
        .fetchall()
    )
    return [
        {
            "label": month_label(row["year"], row["month"]),
            "spending": row["spending"],
            "income": row["income"],
            "net": row["net"],
            "transaction_count": row["transaction_count"],
        }
        for row in rows
    ]


def fetch_category_breakdown(
    conn: Any,
    filters: Sequence[Any],
    unknown_category: str,
    basis: str,
) -> list[Mapping[str, Any]]:
    """Fetch category-level report totals."""
    category = func.coalesce(transactions_table.c.category, unknown_category)
    return (
        conn.execute(
            select(categories_table.c.id.label("category_id"), category.label("label"), *aggregate_columns(basis))
            .select_from(
                transactions_table.outerjoin(
                    categories_table,
                    category_lookup_join_condition(unknown_category),
                )
            )
            .where(report_scope_clause(basis), *filters)
            .group_by(categories_table.c.id, category)
            .order_by(func.sum(spending_amount_expression(basis)).desc(), category)
        )
        .mappings()
        .fetchall()
    )


def fetch_tag_breakdown(conn: Any, filters: Sequence[Any], basis: str) -> list[dict[str, Any]]:
    """Fetch tag-level report totals, including an explicit untagged row."""
    tagged_rows = (
        conn.execute(
            select(tags_table.c.id.label("tag_id"), tags_table.c.name.label("label"), *aggregate_columns(basis))
            .select_from(
                transactions_table.join(
                    transaction_tags_table,
                    transaction_tags_table.c.transaction_id == transactions_table.c.id,
                ).join(tags_table, tags_table.c.id == transaction_tags_table.c.tag_id)
            )
            .where(report_scope_clause(basis), *filters)
            .group_by(tags_table.c.id, tags_table.c.name)
            .order_by(func.sum(spending_amount_expression(basis)).desc(), tags_table.c.name)
        )
        .mappings()
        .fetchall()
    )
    rows = [dict(row) for row in tagged_rows]

    has_tag = exists(select(1).where(transaction_tags_table.c.transaction_id == transactions_table.c.id))
    untagged = (
        conn.execute(select(*aggregate_columns(basis)).where(report_scope_clause(basis), ~has_tag, *filters))
        .mappings()
        .fetchone()
    )
    if untagged and int(untagged["transaction_count"] or 0):
        rows.append({"label": "Untagged", "untagged": True, **dict(untagged)})

    rows.sort(key=lambda row: (-money_to_decimal(row.get("spending")), str(row["label"])))
    return rows


def fetch_taxonomy_category_rows(
    conn: Any,
    filters: Sequence[Any],
    unknown_category: str,
    basis: str,
) -> list[Mapping[str, Any]]:
    """Fetch category rows for the taxonomy report index."""
    category_label = func.coalesce(categories_table.c.name, category_label_expression(unknown_category))
    return (
        conn.execute(
            select(
                categories_table.c.id,
                category_label.label("label"),
                categories_table.c.builtin_key,
                categories_table.c.description,
                *aggregate_columns(basis),
            )
            .select_from(
                transactions_table.outerjoin(
                    categories_table,
                    category_lookup_join_condition(unknown_category),
                )
            )
            .where(report_scope_clause(basis), *filters)
            .group_by(
                categories_table.c.id,
                category_label,
                categories_table.c.builtin_key,
                categories_table.c.description,
            )
            .order_by(func.sum(spending_amount_expression(basis)).desc(), category_label)
        )
        .mappings()
        .fetchall()
    )


def fetch_taxonomy_tag_rows(conn: Any, filters: Sequence[Any], basis: str) -> list[Mapping[str, Any]]:
    """Fetch tag rows for the taxonomy report index."""
    return (
        conn.execute(
            select(
                tags_table.c.id,
                tags_table.c.name.label("label"),
                tags_table.c.builtin_key,
                tags_table.c.description,
                tags_table.c.color,
                *aggregate_columns(basis),
            )
            .select_from(
                transactions_table.join(
                    transaction_tags_table,
                    transaction_tags_table.c.transaction_id == transactions_table.c.id,
                ).join(tags_table, tags_table.c.id == transaction_tags_table.c.tag_id)
            )
            .where(report_scope_clause(basis), *filters)
            .group_by(
                tags_table.c.id,
                tags_table.c.name,
                tags_table.c.builtin_key,
                tags_table.c.description,
                tags_table.c.color,
            )
            .order_by(func.sum(spending_amount_expression(basis)).desc(), tags_table.c.name)
        )
        .mappings()
        .fetchall()
    )


def fetch_taxonomy_target_options(conn: Any) -> list[Mapping[str, Any]]:
    """Fetch all category and tag targets available for report navigation."""
    category_rows = (
        conn.execute(
            select(
                categories_table.c.id,
                categories_table.c.name.label("label"),
                categories_table.c.builtin_key,
                categories_table.c.description,
            ).order_by(categories_table.c.name)
        )
        .mappings()
        .fetchall()
    )
    tag_rows = (
        conn.execute(
            select(
                tags_table.c.id,
                tags_table.c.name.label("label"),
                tags_table.c.builtin_key,
                tags_table.c.description,
                tags_table.c.color,
            ).order_by(tags_table.c.name)
        )
        .mappings()
        .fetchall()
    )
    return [
        {
            "id": row["id"],
            "kind": TAXONOMY_TARGET_CATEGORY,
            "label": row["label"],
            "builtin_key": row["builtin_key"],
            "description": row["description"],
            "color": "",
        }
        for row in category_rows
    ] + [
        {
            "id": row["id"],
            "kind": TAXONOMY_TARGET_TAG,
            "label": row["label"],
            "builtin_key": row["builtin_key"],
            "description": row["description"],
            "color": row["color"],
        }
        for row in tag_rows
    ]


def fetch_account_breakdown(conn: Any, filters: Sequence[Any], basis: str) -> list[Mapping[str, Any]]:
    """Fetch account-level report totals."""
    account_label = func.coalesce(accounts_table.c.name, "No account")
    return (
        conn.execute(
            select(
                transactions_table.c.account_id.label("account_id"),
                account_label.label("label"),
                accounts_table.c.account_type.label("account_type"),
                *aggregate_columns(basis),
            )
            .select_from(
                transactions_table.outerjoin(
                    accounts_table,
                    accounts_table.c.id == transactions_table.c.account_id,
                )
            )
            .where(report_scope_clause(basis), *filters)
            .group_by(transactions_table.c.account_id, account_label, accounts_table.c.account_type)
            .order_by(func.sum(spending_amount_expression(basis)).desc(), account_label)
        )
        .mappings()
        .fetchall()
    )


def fetch_merchant_breakdown(conn: Any, filters: Sequence[Any], basis: str) -> list[dict[str, Any]]:
    """Fetch merchant-level report totals using the shared merchant identity boundary."""
    rows = (
        conn.execute(
            select(
                transactions_table.c.description,
                transactions_table.c.merchant_id,
                merchants_table.c.merchant_key.label("merchant_name"),
                merchants_table.c.merchant_key.label("merchant_key"),
                spending_amount_expression(basis).label("spending"),
                income_credit_amount_expression(basis).label("income"),
                net_cash_flow_expression(basis).label("net"),
            )
            .select_from(
                transactions_table.outerjoin(
                    merchants_table,
                    merchants_table.c.id == transactions_table.c.merchant_id,
                )
            )
            .where(report_scope_clause(basis), *filters)
        )
        .mappings()
        .fetchall()
    )
    aggregates: dict[str, dict[str, Any]] = {}
    for row in rows:
        identity = merchant_identity_from_row(row, conn=conn)
        key = str(identity["key"])
        aggregate = aggregates.setdefault(
            key,
            {
                "label": identity["name"],
                "merchant_key": identity["name"],
                "merchant_id": identity["id"],
                "spending": Decimal("0"),
                "income": Decimal("0"),
                "net": Decimal("0"),
                "transaction_count": 0,
            },
        )
        aggregate["spending"] += money_to_decimal(row["spending"])
        aggregate["income"] += money_to_decimal(row["income"])
        aggregate["net"] += money_to_decimal(row["net"])
        aggregate["transaction_count"] += 1

    return sorted(
        aggregates.values(),
        key=lambda row: (-money_to_decimal(row["spending"]), str(row["label"])),
    )


def fetch_taxonomy_evidence_rows(
    conn: Any,
    filters: Sequence[Any],
    unknown_category: str,
    basis: str,
    *,
    limit: int = 25,
) -> list[dict[str, Any]]:
    """Fetch recent transaction evidence rows for a taxonomy detail report."""
    category_label = category_label_expression(unknown_category)
    rows = (
        conn.execute(
            select(
                transactions_table.c.id,
                transactions_table.c.tx_date,
                transactions_table.c.description,
                transactions_table.c.amount,
                transactions_table.c.transaction_kind,
                category_label.label("category"),
                accounts_table.c.name.label("account_name"),
                transactions_table.c.merchant_id,
                merchants_table.c.merchant_key.label("merchant_name"),
                merchants_table.c.merchant_key.label("merchant_key"),
                spending_amount_expression(basis).label("spending"),
                income_credit_amount_expression(basis).label("income"),
                net_cash_flow_expression(basis).label("net"),
            )
            .select_from(
                transactions_table.outerjoin(
                    accounts_table,
                    accounts_table.c.id == transactions_table.c.account_id,
                ).outerjoin(
                    merchants_table,
                    merchants_table.c.id == transactions_table.c.merchant_id,
                )
            )
            .where(report_scope_clause(basis), *filters)
            .order_by(transactions_table.c.tx_date.desc(), transactions_table.c.id.desc())
            .limit(limit)
        )
        .mappings()
        .fetchall()
    )
    evidence_rows = []
    for row in rows:
        identity = merchant_identity_from_row(row, conn=conn)
        evidence_rows.append({**dict(row), "merchant_label": identity["name"]})
    return evidence_rows


def allocation_totals_by_expense() -> Any:
    """Return reimbursement allocation totals grouped by expense transaction."""
    return (
        select(
            reimbursement_allocations_table.c.expense_transaction_id.label("transaction_id"),
            func.coalesce(func.sum(reimbursement_allocations_table.c.amount), 0).label("allocated"),
        )
        .group_by(reimbursement_allocations_table.c.expense_transaction_id)
        .subquery()
    )


def allocation_totals_by_reimbursement() -> Any:
    """Return reimbursement allocation totals grouped by reimbursement credit."""
    return (
        select(
            reimbursement_allocations_table.c.reimbursement_transaction_id.label("transaction_id"),
            func.coalesce(func.sum(reimbursement_allocations_table.c.amount), 0).label("allocated"),
        )
        .group_by(reimbursement_allocations_table.c.reimbursement_transaction_id)
        .subquery()
    )


def fetch_reimbursable_tag_summary(conn: Any, filters: Sequence[Any]) -> Mapping[str, Any]:
    """Fetch read-only reimbursement coverage totals for a Reimbursable tag report."""
    allocated = allocation_totals_by_expense()
    allocated_amount = func.coalesce(allocated.c.allocated, 0)
    gross_amount = case((transactions_table.c.amount > 0, transactions_table.c.amount), else_=0)
    pending_amount = case(
        (expense_completions_table.c.id.is_not(None), 0),
        else_=gross_amount - allocated_amount,
    )
    return (
        conn.execute(
            select(
                func.count().label("transaction_count"),
                func.coalesce(func.sum(gross_amount), 0).label("gross_amount"),
                func.coalesce(func.sum(allocated_amount), 0).label("matched_amount"),
                func.coalesce(func.sum(pending_amount), 0).label("pending_amount"),
                func.coalesce(
                    func.sum(case((expense_completions_table.c.id.is_not(None), 1), else_=0)),
                    0,
                ).label("completed_count"),
                func.coalesce(func.sum(case((pending_amount > 0, 1), else_=0)), 0).label("pending_count"),
            )
            .select_from(
                transactions_table.outerjoin(
                    allocated,
                    allocated.c.transaction_id == transactions_table.c.id,
                ).outerjoin(
                    expense_completions_table,
                    expense_completions_table.c.expense_transaction_id == transactions_table.c.id,
                )
            )
            .where(*filters, transactions_table.c.amount > 0)
        )
        .mappings()
        .fetchone()
    )


def fetch_reimbursement_category_summary(conn: Any, filters: Sequence[Any]) -> Mapping[str, Any]:
    """Fetch read-only received-credit totals for a Reimbursement category report."""
    allocated = allocation_totals_by_reimbursement()
    allocated_amount = func.coalesce(allocated.c.allocated, 0)
    credit_amount = case((transactions_table.c.amount < 0, -transactions_table.c.amount), else_=0)
    pending_amount = credit_amount - allocated_amount
    return (
        conn.execute(
            select(
                func.count().label("transaction_count"),
                func.coalesce(func.sum(credit_amount), 0).label("received_amount"),
                func.coalesce(func.sum(allocated_amount), 0).label("matched_amount"),
                func.coalesce(func.sum(pending_amount), 0).label("pending_amount"),
                func.coalesce(func.sum(case((pending_amount > 0, 1), else_=0)), 0).label("pending_count"),
            )
            .select_from(
                transactions_table.outerjoin(
                    allocated,
                    allocated.c.transaction_id == transactions_table.c.id,
                )
            )
            .where(*filters, transactions_table.c.amount < 0)
        )
        .mappings()
        .fetchone()
    )
