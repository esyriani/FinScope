"""Application orchestration for the transactions feature."""

import json

from finance_app.core.config import settings
from finance_app.core.constants import TRANSACTION_KINDS, UNKNOWN_CATEGORY
from finance_app.core.periods import DATE_PERIOD_OPTIONS, PERIOD_CUSTOM
from finance_app.database.engine import db_core_transaction
from finance_app.modules.categories import llm as llm_module
from finance_app.modules.categories.categorization import categorize_transactions
from finance_app.modules.categories.repository import get_category_rules
from finance_app.modules.categories.service import classify_unknowns_with_llm, get_category_options
from finance_app.modules.categories.sources import (
    category_confidence_label,
    category_source_badge_class,
    category_source_label,
)
from finance_app.modules.categories.taxonomy import (
    get_category_description_map,
    get_tag_color_map,
    get_tag_option_rows,
    get_transaction_tag_names,
    get_transaction_tags_by_id,
)
from finance_app.modules.settings.runtime import get_bool_setting, get_int_setting, get_setting, get_unknown_category
from finance_app.modules.transactions.constants import (
    CATEGORY_SOURCE_FILTER_OPTIONS,
    IGNORED_FILTER_OPTIONS,
    REVIEW_FILTER_OPTIONS,
)
from finance_app.modules.transactions.filters import (
    build_transaction_core_filters,
    parse_transaction_filters,
    transaction_sort,
)
from finance_app.modules.transactions.presenter import build_transaction_rows
from finance_app.modules.transactions.queries import count_transactions, fetch_distinct_categories, fetch_transactions
from finance_app.modules.transactions.repository import apply_ai_category_update, get_transaction_for_ai_categorization
from finance_app.modules.transactions.urls import transactions_sort_url, transactions_url

RUN_TRANSACTION_AI_SETTING_KEY = "transaction_ai_rerun_enabled"


def build_transactions_context(args):
    """Build transactions context."""
    with db_core_transaction() as conn:
        filters = parse_transaction_filters(args, conn)
        page_size = get_int_setting(conn, "default_table_page_size", settings.default_table_page_size)
        unknown_category = get_unknown_category(conn)
        sort, sort_expression = transaction_sort(filters, unknown_category)
        core_filters = build_transaction_core_filters(filters, unknown_category, conn=conn)
        filter_criteria = core_filters.criteria()
        total_count = count_transactions(conn, filter_criteria)
        total_pages = max(1, (total_count + page_size - 1) // page_size)
        page = min(filters["page"], total_pages)
        offset = (page - 1) * page_size

        fetched_rows = fetch_transactions(
            conn,
            filter_criteria,
            sort_expression,
            filters["direction"],
            page_size,
            offset,
        )
        tag_map = get_transaction_tags_by_id(conn, [row["id"] for row in fetched_rows])
        rows = build_transaction_rows(fetched_rows, tag_map, get_tag_color_map(conn), conn)
        categories = fetch_distinct_categories(conn)
        category_options = get_category_options(conn)
        category_descriptions = get_category_description_map(conn)
        tag_display_options = get_tag_option_rows(conn)
        run_transaction_ai_enabled = get_bool_setting(
            conn,
            RUN_TRANSACTION_AI_SETTING_KEY,
            settings.default_transaction_ai_rerun_enabled,
        )

    return {
        "transactions": rows,
        "categories": categories,
        "search": filters["search"],
        "selected_category": filters["category"],
        "selected_categories": filters["selected_categories"],
        "selected_tags": filters["selected_tags"],
        "selected_review": filters["review"],
        "selected_category_source": filters["category_source"],
        "selected_ignored": filters["ignored"],
        "selected_period": filters["period"],
        "period_options": DATE_PERIOD_OPTIONS,
        "period_custom": PERIOD_CUSTOM,
        "review_filter_options": REVIEW_FILTER_OPTIONS,
        "category_source_filter_options": CATEGORY_SOURCE_FILTER_OPTIONS,
        "ignored_filter_options": IGNORED_FILTER_OPTIONS,
        "sort": sort,
        "direction": filters["direction"],
        "page_url": lambda page_number: transactions_url(page=page_number),
        "sort_url": lambda sort_name: transactions_sort_url(sort_name, sort, filters["direction"]),
        "page": page,
        "page_size": page_size,
        "total_count": total_count,
        "total_pages": total_pages,
        "page_start": offset + 1 if total_count else 0,
        "page_end": min(offset + page_size, total_count),
        "category_options": category_options,
        "category_descriptions": category_descriptions,
        "tag_options": tag_display_options,
        "run_transaction_ai_enabled": run_transaction_ai_enabled,
    }


def run_transaction_ai_categorization(transaction_id):
    """Run LLM categorization synchronously for one transaction.

    The action is meant for development diagnostics, so it applies the returned
    category to only the selected row and suppresses automatic rule creation.
    If the LLM request cannot run, the current transaction state is left intact
    and the returned result explains why.
    """
    with db_core_transaction() as conn:
        if not get_bool_setting(
            conn,
            RUN_TRANSACTION_AI_SETTING_KEY,
            settings.default_transaction_ai_rerun_enabled,
        ):
            return disabled_ai_result()

        row = get_transaction_for_ai_categorization(conn, transaction_id)
        if row is None:
            return missing_transaction_ai_result(transaction_id)

        unknown_category = get_unknown_category(conn) or UNKNOWN_CATEGORY
        original_tags = get_transaction_tag_names(conn, transaction_id)
        tag_colors = get_tag_color_map(conn)
        rules = get_category_rules(conn)
        evidence_transaction = dict(row)
        categorize_transactions([evidence_transaction], conn=conn, use_llm=False)

        llm_transaction = prepare_single_transaction_llm_payload(evidence_transaction, row, unknown_category)
        llm_module.clear_llm_request_status()
        classify_unknowns_with_llm(
            conn,
            [llm_transaction],
            rules,
            unknown_category,
            save_automatic_rules=False,
        )
        request_status = llm_module.last_llm_request_status()
        applied = False
        request_ok = request_status.get("status") == "ok"
        if request_ok:
            applied = apply_ai_category_update(conn, transaction_id, llm_transaction, unknown_category)

        return build_transaction_ai_result(
            row,
            original_tags,
            llm_transaction,
            request_status,
            tag_colors,
            applied=applied,
            request_ok=request_ok,
            model_name=get_setting(conn, "openai_model") or settings.default_categorization_model,
        )


def prepare_single_transaction_llm_payload(evidence_transaction, original_row, unknown_category):
    """Return an LLM payload that preserves evidence while forcing AI review."""
    payload = dict(evidence_transaction)
    payload["category"] = unknown_category
    payload["category_id"] = None
    payload["needs_review"] = 1
    payload["tags"] = []
    payload["category_source"] = "unknown"
    payload["category_confidence"] = None
    payload["category_rule_id"] = None
    payload["category_metadata"] = None
    payload["categorized_at"] = None
    payload["reviewed_at"] = None
    payload["transaction_kind"] = original_row.get("transaction_kind")
    return payload


def build_transaction_ai_result(
    original_row,
    original_tags,
    llm_transaction,
    request_status,
    tag_colors,
    applied,
    request_ok,
    model_name,
):
    """Build a JSON-serializable modal view model for a one-off AI run."""
    metadata = parse_ai_metadata(llm_transaction.get("category_metadata"))
    tags = list(llm_transaction.get("tags") or [])
    original_tag_list = list(original_tags or [])
    message = (
        "AI categorization completed."
        if request_ok and applied
        else ai_request_failure_message(request_status)
    )
    return {
        "ok": bool(request_ok and applied),
        "applied": bool(applied),
        "message": message,
        "transaction_id": original_row["id"],
        "description": original_row["description"],
        "account_name": original_row.get("account_name"),
        "tx_date": stringify_date(original_row.get("tx_date")),
        "amount": original_row.get("amount"),
        "transaction_kind": original_row.get("transaction_kind"),
        "transaction_kind_label": TRANSACTION_KINDS.get(
            original_row.get("transaction_kind"),
            original_row.get("transaction_kind"),
        ),
        "previous_category": original_row.get("category"),
        "previous_tags": original_tag_list,
        "previous_tag_pills": tag_pills(original_tag_list, tag_colors),
        "category": llm_transaction.get("category"),
        "tags": tags,
        "tag_pills": tag_pills(tags, tag_colors),
        "needs_review": bool(llm_transaction.get("needs_review")),
        "category_source": llm_transaction.get("category_source"),
        "category_source_label": category_source_label(llm_transaction.get("category_source")),
        "category_source_badge_class": category_source_badge_class(llm_transaction.get("category_source")),
        "category_confidence": llm_transaction.get("category_confidence"),
        "category_confidence_label": category_confidence_label(llm_transaction.get("category_confidence")),
        "model": model_name,
        "request_status": request_status,
        "request_status_label": request_status_label(request_status),
        "request_detail": request_status.get("detail") or "",
        "metadata_pretty": json.dumps(metadata, ensure_ascii=True, indent=2, sort_keys=True) if metadata else "",
        "llm_reason": metadata.get("llm_reason") or "",
        "llm_confidence": metadata.get("llm_confidence"),
        "proposed_confidence": metadata.get("proposed_confidence"),
        "final_confidence": metadata.get("final_confidence"),
        "review_required": bool(metadata.get("review_required")),
        "failure_reason": metadata.get("failure_reason") or "",
        "supported_by_similar_transactions": metadata.get("supported_by_similar_transactions"),
        "rule_evidence": metadata.get("rule") or llm_transaction.get("rule_evidence") or {},
        "retrieval_evidence": metadata.get("retrieval") or llm_transaction.get("historical_evidence") or {},
    }


def disabled_ai_result():
    """Return the result shown when the per-transaction AI action is disabled."""
    return {
        "ok": False,
        "applied": False,
        "message": "Single-transaction AI is disabled in settings.",
        "request_status_label": "disabled",
    }


def missing_transaction_ai_result(transaction_id):
    """Return the result shown when the selected transaction no longer exists."""
    return {
        "ok": False,
        "applied": False,
        "transaction_id": transaction_id,
        "message": "Transaction not found.",
        "request_status_label": "not_found",
    }


def ai_request_failure_message(request_status):
    """Return a user-facing message for a request that was not persisted."""
    status = request_status.get("status")
    if status == "configuration_missing":
        return "OpenAI API key is not configured."
    if status == "dependency_missing":
        return "OpenAI package is not installed."
    if status == "not_requested":
        return "AI categorization did not run."
    return "AI categorization could not be applied."


def parse_ai_metadata(value):
    """Return category metadata as a dictionary for the result modal."""
    if isinstance(value, dict):
        return value
    text = str(value or "").strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return {"raw": text}
    return parsed if isinstance(parsed, dict) else {"raw": parsed}


def tag_pills(tags, tag_colors):
    """Return tag display pills for the AI result modal."""
    return [
        {
            "name": tag,
            "color": tag_colors.get(tag, "#64748b"),
        }
        for tag in tags
    ]


def request_status_label(request_status):
    """Return a compact status label for the LLM request result."""
    return str(request_status.get("status") or "unknown")


def stringify_date(value):
    """Return a stable date string for session storage and modal rendering."""
    if value is None:
        return ""
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)
