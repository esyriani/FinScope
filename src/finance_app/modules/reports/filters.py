"""Request parsing helpers for the Reports feature.

The first increment only preserves query-string state for bookmarkable report
shell routes. Later increments will extend this boundary with period, measure,
basis, account, merchant, and target filters.
"""

from dataclasses import dataclass
from typing import Protocol

from finance_app.core.periods import (
    DEFAULT_DATE_PERIOD,
    PERIOD_CUSTOM,
    DatePeriod,
    normalize_date_period,
    parse_iso_date,
)
from finance_app.core.query import QueryArgs, query_value
from finance_app.modules.accounts.filters import parse_account_id
from finance_app.modules.dashboard.constants import (
    QUICK_VIEW_ALL,
    QUICK_VIEW_CATEGORIZED,
    QUICK_VIEW_NEEDS_REVIEW,
    QUICK_VIEW_UNKNOWN,
)
from finance_app.modules.merchants.filters import parse_merchant_id, parse_merchant_query
from finance_app.modules.reports.constants import (
    REPORT_BASES,
    REPORT_BASIS_CASH_FLOW,
    REPORT_MEASURE_INCOME,
    REPORT_MEASURE_SPENDING,
    REPORT_MEASURES,
)
from finance_app.modules.reports.definitions import REPORT_INCOME


class ReportsArgs(QueryArgs, Protocol):
    """Represent Flask query arguments used by Reports routes."""

    def to_dict(self, flat: bool = True) -> dict[str, object]:
        """Return query parameters as a dictionary."""
        ...


@dataclass(frozen=True)
class ReportRequest:
    """Represent the shared request state for a Reports route."""

    args: ReportsArgs
    section_key: str
    period: DatePeriod
    measure: str
    basis: str
    selected_account_id: int | None
    selected_merchant_id: int | None
    merchant_query: str
    quick_view: str
    date_from: str
    date_to: str


def parse_report_request(section_key: str, args: ReportsArgs) -> ReportRequest:
    """Return normalized Reports query parameters."""
    period = normalize_date_period(query_value(args, "period", DEFAULT_DATE_PERIOD).strip())
    date_from = parse_iso_date(query_value(args, "date_from")) if period == PERIOD_CUSTOM else ""
    date_to = parse_iso_date(query_value(args, "date_to")) if period == PERIOD_CUSTOM else ""
    if date_from and date_to and date_from > date_to:
        date_from, date_to = date_to, date_from

    default_measure = REPORT_MEASURE_INCOME if section_key == REPORT_INCOME else REPORT_MEASURE_SPENDING
    measure = parse_report_measure(query_value(args, "measure"), default=default_measure)
    basis = parse_report_basis(query_value(args, "basis"))
    merchant_query = parse_merchant_query(query_value(args, "merchant_query") or query_value(args, "merchant_search"))

    return ReportRequest(
        args=args,
        section_key=section_key,
        period=period,
        measure=measure,
        basis=basis,
        selected_account_id=parse_account_id(query_value(args, "account_id")),
        selected_merchant_id=parse_merchant_id(query_value(args, "merchant_id")),
        merchant_query=merchant_query,
        quick_view=parse_report_quick_view(query_value(args, "quick_view")),
        date_from=date_from,
        date_to=date_to,
    )


def parse_report_measure(value: object, *, default: str = REPORT_MEASURE_SPENDING) -> str:
    """Return a supported Reports measure."""
    measure = str(value or "").strip()
    return measure if measure in REPORT_MEASURES else default


def parse_report_basis(value: object) -> str:
    """Return a supported Reports basis."""
    basis = str(value or "").strip()
    return basis if basis in REPORT_BASES else REPORT_BASIS_CASH_FLOW


def parse_report_quick_view(value: object) -> str:
    """Return a supported Reports quick-view shortcut."""
    quick_view = str(value or "").strip()
    if quick_view in {
        QUICK_VIEW_ALL,
        QUICK_VIEW_CATEGORIZED,
        QUICK_VIEW_NEEDS_REVIEW,
        QUICK_VIEW_UNKNOWN,
    }:
        return quick_view
    return QUICK_VIEW_CATEGORIZED
