"""Pinned report view orchestration for Reports.

This module normalizes saved report filters, enforces per-user pin limits, and
builds live overview card models from the same SQL query helpers used by the
Reports pages. It deliberately stores only the saved view definition; displayed
amounts are recalculated whenever the pinned section is rendered.
"""

import json
from collections.abc import Mapping, Sequence
from typing import Any

from flask import url_for
from flask_login import current_user  # type: ignore[import-untyped]
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from finance_app.core.analytics import (
    QUICK_VIEW_ALL,
    QUICK_VIEW_CATEGORIZED,
    QUICK_VIEW_NEEDS_REVIEW,
    QUICK_VIEW_UNKNOWN,
    REPORT_BASIS_OPTIONS,
    REPORT_MEASURE_INCOME,
    REPORT_MEASURE_NET,
    REPORT_MEASURE_OPTIONS,
)
from finance_app.core.config import settings as app_settings
from finance_app.core.constants import UNKNOWN_CATEGORY
from finance_app.core.i18n import gettext
from finance_app.core.money import rounded_money_float
from finance_app.core.periods import PERIOD_CUSTOM, get_period_label
from finance_app.database.engine import db_core_transaction
from finance_app.database.tables import accounts as accounts_table
from finance_app.modules.merchants.repository import find_merchant_by_id
from finance_app.modules.reports.definitions import (
    REPORT_ACCOUNTS,
    REPORT_INCOME,
    REPORT_MERCHANTS,
    REPORT_OVERVIEW,
    REPORT_TAXONOMY,
)
from finance_app.modules.reports.entities import (
    REPORT_ENTITY_ACCOUNT,
    REPORT_ENTITY_MERCHANT,
    ReportEntityTarget,
    resolve_account_report_target,
    resolve_merchant_report_target,
)
from finance_app.modules.reports.filters import (
    ReportRequest,
    parse_report_request,
    report_taxonomy_filters_active,
)
from finance_app.modules.reports.pins_repository import (
    count_pinned_reports,
    delete_pinned_reports,
    find_pinned_report_by_fingerprint,
    insert_pinned_report,
    list_pinned_reports,
    update_pinned_report_order_and_title,
)
from finance_app.modules.reports.queries import (
    account_target_condition,
    fetch_report_summary,
    income_credit_target_condition,
    merchant_target_condition,
    report_quick_view_conditions,
    report_taxonomy_filter_conditions,
    reports_base_filters,
    taxonomy_target_condition,
)
from finance_app.modules.reports.taxonomy import (
    TAXONOMY_TARGET_CATEGORY,
    TAXONOMY_TARGET_TAG,
    TaxonomyReportTarget,
    resolve_taxonomy_report_target,
)
from finance_app.modules.reports.urls import reports_url
from finance_app.modules.settings.runtime import get_setting, get_unknown_category

PINNED_REPORT_LIMIT_KEY = "pinned_report_limit"
PINNED_REPORT_LIMIT_MIN = 1
PINNED_REPORT_LIMIT_MAX = 12
PINNED_REPORT_SHORT_TITLE_MAX = 30

REPORT_TYPE_ACCOUNT = "account"
REPORT_TYPE_MERCHANT = "merchant"

REPORT_TYPE_SECTION = {
    REPORT_OVERVIEW: REPORT_OVERVIEW,
    REPORT_TAXONOMY: REPORT_TAXONOMY,
    REPORT_TYPE_ACCOUNT: REPORT_ACCOUNTS,
    REPORT_TYPE_MERCHANT: REPORT_MERCHANTS,
    REPORT_INCOME: REPORT_INCOME,
}

REPORT_TYPE_LABELS = {
    REPORT_OVERVIEW: "Overview",
    REPORT_TAXONOMY: "Categories and tags",
    REPORT_TYPE_ACCOUNT: "Account report",
    REPORT_TYPE_MERCHANT: "Merchant report",
    REPORT_INCOME: "Income and credits",
}

SCOPE_LABELS = {
    QUICK_VIEW_ALL: "All",
    QUICK_VIEW_CATEGORIZED: "Categorized",
    QUICK_VIEW_NEEDS_REVIEW: "Needs review",
    QUICK_VIEW_UNKNOWN: "Unknown",
}

PIN_VALUE_FIELDS = (
    "report_type",
    "target_kind",
    "target_category_id",
    "target_tag_id",
    "target_account_id",
    "target_merchant_id",
    "period",
    "date_from",
    "date_to",
    "measure",
    "basis",
    "account_filter_id",
    "merchant_filter_id",
    "merchant_query",
    "classification_scope",
    "category_filters",
    "tag_filters",
)


class PinnedReportArgs:
    """Small query-args adapter for reconstructing saved report filters."""

    def __init__(self, pairs: Sequence[tuple[str, str]]) -> None:
        self._values: dict[str, list[str]] = {}
        for key, value in pairs:
            self.add(key, value)

    def add(self, key: str, value: str) -> None:
        """Append a query value."""
        self._values.setdefault(key, []).append(value)

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


def current_report_pin_context(
    report_type: str,
    report_request: ReportRequest,
    **target_values: Any,
) -> dict[str, Any]:
    """Return Pin/Pinned button context for the currently rendered report."""
    if not getattr(current_user, "is_authenticated", False):
        return {}

    payload = build_pin_payload(report_type, report_request, **target_values)
    values = normalize_pin_values(payload)
    with db_core_transaction() as conn:
        existing = find_pinned_report_by_fingerprint(conn, int(current_user.id), str(values["fingerprint"]))

    return {
        "pin_report_payload": payload,
        "pin_report_url": url_for("reports.pin_report"),
        "pin_report_is_pinned": existing is not None,
    }


def build_pin_payload(report_type: str, report_request: ReportRequest, **target_values: Any) -> dict[str, Any]:
    """Return a JSON-safe normalized payload for a report view."""
    account_filter_id = None if report_type == REPORT_TYPE_ACCOUNT else report_request.selected_account_id
    merchant_filter_id = None if report_type == REPORT_TYPE_MERCHANT else report_request.selected_merchant_id
    merchant_query = "" if report_type == REPORT_TYPE_MERCHANT else report_request.merchant_query
    category_filters: Sequence[str] = (
        report_request.selected_categories if report_taxonomy_filters_active(report_request) else []
    )
    tag_filters: Sequence[str] = report_request.selected_tags if report_taxonomy_filters_active(report_request) else []
    return {
        "report_type": report_type,
        "target_kind": target_values.get("target_kind"),
        "target_category_id": target_values.get("target_category_id"),
        "target_tag_id": target_values.get("target_tag_id"),
        "target_account_id": target_values.get("target_account_id"),
        "target_merchant_id": target_values.get("target_merchant_id"),
        "period": report_request.period,
        "date_from": report_request.date_from if report_request.period == PERIOD_CUSTOM else "",
        "date_to": report_request.date_to if report_request.period == PERIOD_CUSTOM else "",
        "measure": report_request.measure,
        "basis": report_request.basis,
        "account_filter_id": account_filter_id,
        "merchant_filter_id": merchant_filter_id,
        "merchant_query": merchant_query,
        "classification_scope": report_request.quick_view,
        "category_filters": list(category_filters),
        "tag_filters": list(tag_filters),
    }


def pin_current_report(user_id: int, payload: Mapping[str, Any]) -> dict[str, Any]:
    """Persist a current report view for the user if it is not already pinned."""
    values = normalize_pin_values(payload)
    with db_core_transaction() as conn:
        existing = find_pinned_report_by_fingerprint(conn, user_id, str(values["fingerprint"]))
        if existing is not None:
            return {
                "ok": True,
                "already_pinned": True,
                "message": gettext("Report is already pinned."),
                "pin_id": existing["id"],
            }

        limit = pinned_report_limit(conn, user_id)
        if count_pinned_reports(conn, user_id) >= limit:
            return {
                "ok": False,
                "limit_reached": True,
                "message": gettext("Pinned report limit reached."),
                "overview_url": url_for("reports.overview"),
                "settings_url": url_for("settings_page.settings_page"),
            }

        try:
            row = insert_pinned_report(conn, user_id, values)
        except IntegrityError:
            existing = find_pinned_report_by_fingerprint(conn, user_id, str(values["fingerprint"]))
            if existing is None:
                raise
            return {
                "ok": True,
                "already_pinned": True,
                "message": gettext("Report is already pinned."),
                "pin_id": existing["id"],
            }

    return {"ok": True, "already_pinned": False, "message": gettext("Report pinned."), "pin_id": row["id"]}


def save_pinned_report_edits(user_id: int, payload: Mapping[str, Any]) -> dict[str, Any]:
    """Persist overview edit-mode removals, order changes, and short titles."""
    raw_items = payload.get("pins")
    if not isinstance(raw_items, list):
        raise ValueError("Pinned report edits are invalid.")

    remove_ids: list[int] = []
    ordered_items: list[tuple[int, str | None]] = []
    seen_ids: set[int] = set()
    for item in raw_items:
        if not isinstance(item, Mapping):
            continue
        pin_id = parse_optional_int(item.get("id"))
        if pin_id is None or pin_id in seen_ids:
            continue
        seen_ids.add(pin_id)
        if bool(item.get("remove")):
            remove_ids.append(pin_id)
            continue
        ordered_items.append((pin_id, normalize_short_title(item.get("short_title"))))

    with db_core_transaction() as conn:
        delete_pinned_reports(conn, user_id, remove_ids)
        existing_ids = {int(row["id"]) for row in list_pinned_reports(conn, user_id)}
        sort_order = 0
        for pin_id, short_title in ordered_items:
            if pin_id not in existing_ids:
                continue
            update_pinned_report_order_and_title(conn, user_id, pin_id, sort_order, short_title)
            sort_order += 1

    return {"ok": True, "message": gettext("Pinned reports saved.")}


def pinned_reports_overview_context() -> dict[str, Any]:
    """Return overview-only pinned report section context for the current user."""
    if not getattr(current_user, "is_authenticated", False):
        return {
            "pinned_reports": [],
            "pinned_report_limit": app_settings.default_pinned_report_limit,
            "pinned_reports_save_url": "",
        }

    with db_core_transaction() as conn:
        user_id = int(current_user.id)
        limit = pinned_report_limit(conn, user_id)
        rows = list_pinned_reports(conn, user_id)
        cards = [build_pinned_report_card(conn, row) for row in rows]

    return {
        "pinned_reports": cards,
        "pinned_report_limit": limit,
        "pinned_reports_save_url": url_for("reports.save_pinned_reports"),
    }


def build_pinned_report_card(conn: Any, row: Mapping[str, Any]) -> dict[str, Any]:
    """Return a live card view model for one pinned report row."""
    target = resolve_pin_target(conn, row)
    missing_target = target is None and row.get("target_kind") is not None
    title = normalize_short_title(row.get("short_title")) or generated_pin_title(row, target)
    base_card = {
        "id": int(row["id"]),
        "title": title,
        "short_title": normalize_short_title(row.get("short_title")) or "",
        "type_label": pin_type_label(row, target),
        "filter_summary": pin_filter_summary(conn, row),
        "sort_order": int(row["sort_order"] or 0),
        "is_missing": missing_target,
        "missing_message": "Report target no longer exists.",
        "open_url": "",
        "primary_label": measure_label(str(row["measure"])),
        "primary_value": 0.0,
        "transaction_count": 0,
    }
    if missing_target:
        return base_card

    report_request = report_request_from_pin(row)
    summary = fetch_pin_summary(conn, row, report_request, target)
    return {
        **base_card,
        "open_url": pin_open_url(row, report_request, target),
        "primary_value": summary_measure_value(summary, str(row["measure"])),
        "transaction_count": int(summary.get("transaction_count") or 0),
    }


def normalize_pin_values(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and normalize pinned report payload values for persistence."""
    report_type = str(payload.get("report_type") or "").strip()
    if report_type not in {
        REPORT_OVERVIEW,
        REPORT_TAXONOMY,
        REPORT_TYPE_ACCOUNT,
        REPORT_TYPE_MERCHANT,
        REPORT_INCOME,
    }:
        raise ValueError("Pinned report type is invalid.")

    report_request = report_request_from_payload(report_type, payload)
    target_values = normalize_target_values(report_type, payload)
    category_filters = report_request.selected_categories if report_taxonomy_filters_active(report_request) else []
    tag_filters = report_request.selected_tags if report_taxonomy_filters_active(report_request) else []
    values: dict[str, Any] = {
        **target_values,
        "report_type": report_type,
        "period": report_request.period,
        "date_from": report_request.date_from or None,
        "date_to": report_request.date_to or None,
        "measure": report_request.measure,
        "basis": report_request.basis,
        "account_filter_id": None if report_type == REPORT_TYPE_ACCOUNT else report_request.selected_account_id,
        "merchant_filter_id": None if report_type == REPORT_TYPE_MERCHANT else report_request.selected_merchant_id,
        "merchant_query": "" if report_type == REPORT_TYPE_MERCHANT else report_request.merchant_query,
        "classification_scope": report_request.quick_view,
        "category_filters": dump_filter_list(category_filters),
        "tag_filters": dump_filter_list(tag_filters),
        "short_title": normalize_short_title(payload.get("short_title")),
    }
    values["fingerprint"] = pin_fingerprint(values)
    return values


def normalize_target_values(report_type: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    """Return target columns matching the pinned report type."""
    values = {
        "target_kind": None,
        "target_category_id": None,
        "target_tag_id": None,
        "target_account_id": None,
        "target_merchant_id": None,
    }
    if report_type in {REPORT_OVERVIEW, REPORT_INCOME}:
        return values
    if report_type == REPORT_TAXONOMY:
        target_kind = str(payload.get("target_kind") or "").strip()
        if target_kind == TAXONOMY_TARGET_CATEGORY:
            target_id = require_int(payload.get("target_category_id"), "Category report target is invalid.")
            return {**values, "target_kind": target_kind, "target_category_id": target_id}
        if target_kind == TAXONOMY_TARGET_TAG:
            target_id = require_int(payload.get("target_tag_id"), "Tag report target is invalid.")
            return {**values, "target_kind": target_kind, "target_tag_id": target_id}
        raise ValueError("Pinned report target is invalid.")
    if report_type == REPORT_TYPE_ACCOUNT:
        target_id = require_int(payload.get("target_account_id"), "Account report target is invalid.")
        return {**values, "target_kind": REPORT_ENTITY_ACCOUNT, "target_account_id": target_id}
    if report_type == REPORT_TYPE_MERCHANT:
        target_id = require_int(payload.get("target_merchant_id"), "Merchant report target is invalid.")
        return {**values, "target_kind": REPORT_ENTITY_MERCHANT, "target_merchant_id": target_id}
    return values


def report_request_from_payload(report_type: str, payload: Mapping[str, Any]) -> ReportRequest:
    """Build a normalized Reports request from a JSON payload."""
    args = PinnedReportArgs(
        [
            ("period", str(payload.get("period") or "")),
            ("date_from", str(payload.get("date_from") or "")),
            ("date_to", str(payload.get("date_to") or "")),
            ("measure", str(payload.get("measure") or "")),
            ("basis", str(payload.get("basis") or "")),
            ("account_id", str(payload.get("account_filter_id") or "")),
            ("merchant_id", str(payload.get("merchant_filter_id") or "")),
            ("merchant_query", str(payload.get("merchant_query") or "")),
            ("quick_view", str(payload.get("classification_scope") or payload.get("quick_view") or "")),
        ]
    )
    for category in clean_filter_values(payload.get("category_filters")):
        args.add("categories", category)
    for tag in clean_filter_values(payload.get("tag_filters")):
        args.add("tags", tag)
    return parse_report_request(REPORT_TYPE_SECTION[report_type], args)


def report_request_from_pin(row: Mapping[str, Any]) -> ReportRequest:
    """Build a normalized Reports request from a persisted pin row."""
    payload = {
        "report_type": row["report_type"],
        "period": row["period"],
        "date_from": row.get("date_from") or "",
        "date_to": row.get("date_to") or "",
        "measure": row["measure"],
        "basis": row["basis"],
        "account_filter_id": row.get("account_filter_id"),
        "merchant_filter_id": row.get("merchant_filter_id"),
        "merchant_query": row.get("merchant_query") or "",
        "classification_scope": row.get("classification_scope"),
        "category_filters": load_filter_list(row.get("category_filters")),
        "tag_filters": load_filter_list(row.get("tag_filters")),
    }
    return report_request_from_payload(str(row["report_type"]), payload)


def fetch_pin_summary(
    conn: Any,
    row: Mapping[str, Any],
    report_request: ReportRequest,
    target: TaxonomyReportTarget | ReportEntityTarget | None,
) -> Mapping[str, Any]:
    """Fetch a live summary for a pinned report."""
    unknown_category = get_unknown_category(conn) or UNKNOWN_CATEGORY
    report_type = str(row["report_type"])
    base_filters = reports_base_filters(
        report_request,
        include_account=report_type != REPORT_TYPE_ACCOUNT,
        include_merchant=report_type != REPORT_TYPE_MERCHANT,
    ).criteria()
    target_filters = list(base_filters)
    if report_type == REPORT_INCOME:
        target_filters.append(income_credit_target_condition(report_request.basis))
    elif report_type == REPORT_TAXONOMY and isinstance(target, TaxonomyReportTarget):
        target_filters.append(taxonomy_target_condition(target, unknown_category))
    elif report_type == REPORT_TYPE_ACCOUNT and isinstance(target, ReportEntityTarget):
        target_filters.append(account_target_condition(target.id))
    elif report_type == REPORT_TYPE_MERCHANT and isinstance(target, ReportEntityTarget):
        target_filters.append(merchant_target_condition(target.id))

    filters = [
        *target_filters,
        *report_quick_view_conditions(report_request.quick_view, unknown_category),
        *(
            report_taxonomy_filter_conditions(report_request, unknown_category)
            if report_taxonomy_filters_active(report_request)
            else []
        ),
    ]
    return fetch_report_summary(conn, filters, unknown_category, report_request.basis)


def resolve_pin_target(
    conn: Any,
    row: Mapping[str, Any],
) -> TaxonomyReportTarget | ReportEntityTarget | None:
    """Return a pinned report target, or None for targetless or missing reports."""
    report_type = str(row["report_type"])
    if report_type == REPORT_TAXONOMY:
        if row.get("target_kind") == TAXONOMY_TARGET_CATEGORY and row.get("target_category_id"):
            return resolve_taxonomy_report_target(conn, TAXONOMY_TARGET_CATEGORY, int(row["target_category_id"]))
        if row.get("target_kind") == TAXONOMY_TARGET_TAG and row.get("target_tag_id"):
            return resolve_taxonomy_report_target(conn, TAXONOMY_TARGET_TAG, int(row["target_tag_id"]))
        return None
    if report_type == REPORT_TYPE_ACCOUNT and row.get("target_account_id"):
        return resolve_account_report_target(conn, int(row["target_account_id"]))
    if report_type == REPORT_TYPE_MERCHANT and row.get("target_merchant_id"):
        return resolve_merchant_report_target(conn, int(row["target_merchant_id"]))
    return None


def pin_open_url(
    row: Mapping[str, Any],
    report_request: ReportRequest,
    target: TaxonomyReportTarget | ReportEntityTarget | None,
) -> str:
    """Return the report URL for a pinned card."""
    report_type = str(row["report_type"])
    if report_type == REPORT_OVERVIEW:
        return reports_url(report_request.args, endpoint="reports.overview")
    if report_type == REPORT_INCOME:
        return reports_url(report_request.args, endpoint="reports.income")
    if report_type == REPORT_TAXONOMY and isinstance(target, TaxonomyReportTarget):
        endpoint = "reports.category_report" if target.kind == TAXONOMY_TARGET_CATEGORY else "reports.tag_report"
        route_key = "category_id" if target.kind == TAXONOMY_TARGET_CATEGORY else "tag_id"
        return reports_url(report_request.args, endpoint=endpoint, route_values={route_key: target.id})
    if report_type == REPORT_TYPE_ACCOUNT and isinstance(target, ReportEntityTarget):
        return reports_url(
            report_request.args,
            endpoint="reports.account_report",
            route_values={"account_id": target.id},
        )
    if report_type == REPORT_TYPE_MERCHANT and isinstance(target, ReportEntityTarget):
        return reports_url(
            report_request.args,
            endpoint="reports.merchant_report",
            route_values={"merchant_id": target.id},
        )
    return ""


def pin_filter_summary(conn: Any, row: Mapping[str, Any]) -> str:
    """Return a compact saved-filter summary for a pinned report card."""
    request = report_request_from_pin(row)
    parts = [
        f"{gettext('Period')}: {gettext(get_period_label(request.period, request.date_from, request.date_to))}",
        f"{gettext('Measure')}: {gettext(measure_label(request.measure))}",
        f"{gettext('Basis')}: {gettext(basis_label(request.basis))}",
        f"{gettext('Scope')}: {gettext(SCOPE_LABELS.get(request.quick_view, 'Categorized'))}",
    ]
    if request.selected_account_id and row["report_type"] != REPORT_TYPE_ACCOUNT:
        parts.append(f"{gettext('Account')}: {account_filter_label(conn, request.selected_account_id)}")
    merchant_label = merchant_filter_label(conn, request.selected_merchant_id, request.merchant_query)
    if merchant_label and row["report_type"] != REPORT_TYPE_MERCHANT:
        parts.append(f"{gettext('Merchant')}: {merchant_label}")
    if report_taxonomy_filters_active(request):
        if request.selected_categories:
            parts.append(f"{gettext('Categories')}: {', '.join(request.selected_categories)}")
        if request.selected_tags:
            parts.append(f"{gettext('Tags')}: {', '.join(request.selected_tags)}")
    return " - ".join(parts)


def generated_pin_title(row: Mapping[str, Any], target: TaxonomyReportTarget | ReportEntityTarget | None) -> str:
    """Return the default card title for a pinned report."""
    if target is not None:
        return target.name
    if row.get("target_kind") is not None:
        return "Missing report target"
    return REPORT_TYPE_LABELS.get(str(row["report_type"]), "Report")


def pin_type_label(row: Mapping[str, Any], target: TaxonomyReportTarget | ReportEntityTarget | None) -> str:
    """Return the card type label for a pinned report."""
    if target is not None:
        return target.report_label
    if row.get("target_kind") == TAXONOMY_TARGET_CATEGORY:
        return "Category report"
    if row.get("target_kind") == TAXONOMY_TARGET_TAG:
        return "Tag report"
    return REPORT_TYPE_LABELS.get(str(row["report_type"]), "Report")


def pinned_report_limit(conn: Any, user_id: int) -> int:
    """Return the bounded pinned report limit for a user."""
    raw_value = get_setting(conn, PINNED_REPORT_LIMIT_KEY, user_id=user_id)
    try:
        parsed = int(str(raw_value))
    except (TypeError, ValueError):
        parsed = app_settings.default_pinned_report_limit
    return max(PINNED_REPORT_LIMIT_MIN, min(PINNED_REPORT_LIMIT_MAX, parsed))


def pin_fingerprint(values: Mapping[str, Any]) -> str:
    """Return a deterministic exact-view fingerprint for a pinned report."""
    payload: dict[str, Any] = {}
    for field in PIN_VALUE_FIELDS:
        value = values.get(field)
        if field in {"category_filters", "tag_filters"}:
            value = load_filter_list(value)
        payload[field] = value or None
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def dump_filter_list(values: Sequence[str]) -> str:
    """Return a canonical JSON representation for multi-select filters."""
    return json.dumps(sorted({str(value).strip() for value in values if str(value).strip()}), separators=(",", ":"))


def load_filter_list(value: object) -> list[str]:
    """Return a clean list from a persisted JSON filter column."""
    try:
        parsed = json.loads(str(value or "[]"))
    except json.JSONDecodeError:
        return []
    return clean_filter_values(parsed)


def clean_filter_values(value: object) -> list[str]:
    """Return unique non-empty filter labels preserving first-seen order."""
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    cleaned: list[str] = []
    seen: set[str] = set()
    for item in value:
        text = str(item or "").strip()
        if not text or text.casefold() in seen:
            continue
        seen.add(text.casefold())
        cleaned.append(text)
    return cleaned


def normalize_short_title(value: object) -> str | None:
    """Return a trimmed optional title with a hard 30-character limit."""
    text = str(value or "").strip()
    if not text:
        return None
    return text[:PINNED_REPORT_SHORT_TITLE_MAX]


def parse_optional_int(value: object) -> int | None:
    """Return a positive integer or None."""
    try:
        parsed = int(str(value))
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def require_int(value: object, message: str) -> int:
    """Return a required positive integer."""
    parsed = parse_optional_int(value)
    if parsed is None:
        raise ValueError(message)
    return parsed


def summary_measure_value(summary: Mapping[str, Any], measure: str) -> float:
    """Return the selected measure from a summary row."""
    if measure == REPORT_MEASURE_INCOME:
        return rounded_money_float(summary.get("total_income"))
    if measure == REPORT_MEASURE_NET:
        return rounded_money_float(summary.get("net_cashflow"))
    return rounded_money_float(summary.get("total_spending"))


def measure_label(measure: str) -> str:
    """Return a report measure label."""
    return next((str(option["label"]) for option in REPORT_MEASURE_OPTIONS if option["value"] == measure), "Spending")


def basis_label(basis: str) -> str:
    """Return a report basis label."""
    return next(
        (str(option["label"]) for option in REPORT_BASIS_OPTIONS if option["value"] == basis),
        "Reportable cash flow",
    )


def account_filter_label(conn: Any, account_id: int) -> str:
    """Return an account filter label."""
    row = conn.execute(select(accounts_table.c.name).where(accounts_table.c.id == account_id)).mappings().fetchone()
    return str(row["name"]) if row is not None else gettext("All accounts")


def merchant_filter_label(conn: Any, merchant_id: int | None, merchant_query: str) -> str:
    """Return a merchant filter label."""
    if merchant_id is None:
        return merchant_query
    merchant = find_merchant_by_id(conn, merchant_id)
    return str(merchant["merchant_key"]) if merchant is not None else merchant_query
