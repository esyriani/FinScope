"""Application orchestration for the transactions feature."""

import json

from finance_app.background.runner import (
    AI_JOB_QUEUE,
    append_background_job_log,
    is_job_cancel_requested,
    raise_if_cancel_requested,
    submit_background_job,
    update_background_job_progress,
)
from finance_app.core.config import settings
from finance_app.core.constants import CATEGORY_RULE_SOURCE_MANUAL, TRANSACTION_KINDS, UNKNOWN_CATEGORY
from finance_app.core.periods import DATE_PERIOD_OPTIONS, PERIOD_CUSTOM
from finance_app.database.engine import db_core_transaction
from finance_app.modules.categories import llm as llm_module
from finance_app.modules.categories.categorization import categorize_transactions
from finance_app.modules.categories.repository import get_category_rules
from finance_app.modules.categories.service import classify_unknowns_with_llm, get_category_options, save_category_rule
from finance_app.modules.categories.sources import (
    category_confidence_label,
    category_source_badge_class,
    category_source_label,
    utc_timestamp,
)
from finance_app.modules.categories.taxonomy import (
    get_category_description_map,
    get_tag_color_map,
    get_tag_option_rows,
    get_transaction_tag_names,
    get_transaction_tags_by_id,
)
from finance_app.modules.rules.forms import amount_bounds_label, normalize_rule_keyword
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
from finance_app.modules.transactions.queries import (
    count_transactions,
    fetch_distinct_categories,
    fetch_transaction_ids,
    fetch_transactions,
)
from finance_app.modules.transactions.repository import (
    apply_ai_category_update,
    get_transaction_for_ai_categorization,
    get_transactions_for_recategorization,
    mark_transactions_verified,
    normalized_transaction_ids,
    set_transactions_ignored,
    update_recategorized_transaction,
)
from finance_app.modules.transactions.urls import transactions_sort_url, transactions_url

RUN_TRANSACTION_AI_SETTING_KEY = "transaction_ai_rerun_enabled"
APPLY_AI_SUGGESTION_ACTION = "apply"
APPLY_AI_SUGGESTION_WITH_RULE_ACTION = "apply_and_create_rule"


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
        all_transaction_ids = fetch_transaction_ids(
            conn,
            filter_criteria,
            sort_expression,
            filters["direction"],
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
        "all_transaction_ids": all_transaction_ids,
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


def approve_selected_transactions(transaction_ids):
    """Approve selected transactions and return the number of changed rows."""
    ids = normalized_transaction_ids(transaction_ids)
    if not ids:
        return 0

    with db_core_transaction() as conn:
        return mark_transactions_verified(conn, ids)


def ignore_selected_transactions(transaction_ids):
    """Ignore selected transactions and return the number of changed rows."""
    ids = normalized_transaction_ids(transaction_ids)
    if not ids:
        return 0

    with db_core_transaction() as conn:
        return set_transactions_ignored(conn, ids, 1)


def queue_selected_transaction_recategorization(transaction_ids):
    """Queue complete categorization for selected transactions.

    Args:
        transaction_ids: User-selected transaction IDs. Invalid or duplicate
            IDs are ignored before queueing.

    Returns:
        A dictionary containing the normalized selected count and optional
        background job ID. No job is queued when the selection is empty.
    """
    ids = normalized_transaction_ids(transaction_ids)
    if not ids:
        return {"selected_count": 0, "job_id": None}

    job_id = submit_background_job(
        f"Recategorize {len(ids)} selected transactions",
        recategorize_selected_transactions_job,
        ids,
        queue=AI_JOB_QUEUE,
    )
    return {"selected_count": len(ids), "job_id": job_id}


def recategorize_selected_transactions_job(transaction_ids):
    """Run the full categorization workflow for selected transaction IDs."""
    ids = normalized_transaction_ids(transaction_ids)
    if not ids:
        update_selected_recategorization_progress(
            0,
            0,
            0,
            log_message="No selected transactions to recategorize.",
        )
        return "No selected transactions to recategorize."

    processed_count = 0
    updated_count = 0
    with db_core_transaction() as conn:
        rows = get_transactions_for_recategorization(conn, ids)

    total = len(rows)
    if not total:
        update_selected_recategorization_progress(
            0,
            0,
            0,
            log_message="No selected transactions found.",
        )
        return "No selected transactions found."

    update_selected_recategorization_progress(
        0,
        total,
        0,
        log_message="Starting selected transaction recategorization for {total} transactions.",
        log_params={"total": total},
    )

    for index in range(0, total, llm_module.LLM_BATCH_SIZE):
        if is_job_cancel_requested():
            append_selected_recategorization_log(
                "Cancellation requested; stopping before the next batch.",
                level="warning",
            )
        raise_if_cancel_requested("Selected transaction recategorization cancelled after the current batch.")

        batch = rows[index : index + llm_module.LLM_BATCH_SIZE]
        batch_start = processed_count + 1
        batch_end = processed_count + len(batch)
        append_selected_recategorization_log(
            "Starting selected recategorization batch {start}-{end} of {total}.",
            params={
                "start": batch_start,
                "end": batch_end,
                "total": total,
            },
        )
        update_selected_recategorization_progress(
            processed_count,
            total,
            updated_count,
            message="Recategorizing {start}-{end} of {total}; {updated} updated so far.",
            params={
                "start": batch_start,
                "end": batch_end,
                "total": total,
                "updated": updated_count,
            },
        )

        batch_updated = recategorize_selected_transaction_rows(batch)
        processed_count += len(batch)
        updated_count += batch_updated
        append_selected_recategorization_log(
            "Finished selected recategorization batch {start}-{end}: {processed} processed; {updated} updated total.",
            params={
                "start": batch_start,
                "end": batch_end,
                "processed": len(batch),
                "updated": updated_count,
                "batch_updated": batch_updated,
            },
        )
        update_selected_recategorization_progress(processed_count, total, updated_count)

    summary = f"{updated_count} selected transaction{'s' if updated_count != 1 else ''} recategorized."
    append_selected_recategorization_log(
        "Selected transaction recategorization completed: {summary}",
        params={"summary": summary},
    )
    return summary


def recategorize_selected_transaction_rows(rows):
    """Categorize and persist one batch of selected transaction rows."""
    with db_core_transaction() as conn:
        unknown_category = get_unknown_category(conn) or UNKNOWN_CATEGORY
        categorized = categorize_transactions([dict(row) for row in rows], conn=conn, use_llm=True)
        updated_count = 0
        for transaction in categorized:
            if update_recategorized_transaction(conn, transaction, unknown_category):
                updated_count += 1
    return updated_count


def update_selected_recategorization_progress(
    current,
    total,
    updated,
    message=None,
    params=None,
    log_message=None,
    log_params=None,
    log_level="info",
):
    """Publish progress for the selected-transaction recategorization job."""
    default_message = "Recategorized {current} of {total}; {updated} updated."
    progress_params = {
        "current": current,
        "total": total,
        "updated": updated,
    }
    if params:
        progress_params.update(params)
    update_background_job_progress(
        current=current,
        total=total,
        message=message or default_message,
        params=progress_params,
    )
    if log_message:
        append_selected_recategorization_log(
            log_message,
            params={**progress_params, **(log_params or {})},
            level=log_level,
        )


def append_selected_recategorization_log(message, params=None, level="info"):
    """Append a selected-recategorization log entry to the current job."""
    append_background_job_log(message, params=params, level=level)


def suggest_transaction_ai_category(transaction_id):
    """Run LLM categorization synchronously for one transaction.

    The action is suggestion-first: it suppresses automatic rule creation and
    does not mutate the selected row. The returned result contains the display
    model and a signed-session persistence payload used only if the user
    explicitly applies the suggestion from the modal dialog.
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
        request_ok = request_status.get("status") == "ok"

        return build_transaction_ai_result(
            row,
            original_tags,
            llm_transaction,
            request_status,
            tag_colors,
            request_ok=request_ok,
            unknown_category=unknown_category,
            model_name=get_setting(conn, "openai_model") or settings.default_categorization_model,
        )


def apply_transaction_ai_suggestion(
    transaction_id,
    suggestion,
    action=APPLY_AI_SUGGESTION_ACTION,
    rule_keyword="",
    amount_min=None,
    amount_max=None,
):
    """Apply a pending single-transaction AI suggestion.

    Args:
        transaction_id: Transaction primary key targeted by the action.
        suggestion: Signed-session suggestion payload produced by
            `suggest_transaction_ai_category`.
        action: Whether to apply only the transaction or also create a rule.
        rule_keyword: Optional user-edited rule keyword for rule creation.
        amount_min: Optional lower rule amount bound.
        amount_max: Optional upper rule amount bound.

    Returns:
        A dictionary with `updated`, optional `saved_rule_id`, and a user-facing
        message. No mutation occurs when the suggestion is missing, stale, or
        does not contain an applicable category.
    """
    if not suggestion or int(suggestion.get("transaction_id") or 0) != int(transaction_id):
        return {
            "updated": False,
            "message": "AI suggestion expired. Use Suggest category again.",
        }
    if not suggestion.get("can_apply") or not suggestion.get("persistence"):
        return {"updated": False, "message": "AI suggestion cannot be applied."}

    with db_core_transaction() as conn:
        if not get_bool_setting(
            conn,
            RUN_TRANSACTION_AI_SETTING_KEY,
            settings.default_transaction_ai_rerun_enabled,
        ):
            return disabled_ai_apply_result()

        row = get_transaction_for_ai_categorization(conn, transaction_id)
        if row is None:
            return missing_transaction_apply_result(transaction_id)

        unknown_category = get_unknown_category(conn) or UNKNOWN_CATEGORY
        payload = accepted_ai_suggestion_payload(suggestion["persistence"])
        keyword = ""
        should_create_rule = action == APPLY_AI_SUGGESTION_WITH_RULE_ACTION
        if should_create_rule:
            keyword = normalize_rule_keyword(rule_keyword, row["description"])
            if not keyword:
                return {
                    "updated": False,
                    "message": "Rule keyword is required when saving a rule.",
                }

        updated = apply_ai_category_update(conn, transaction_id, payload, unknown_category)
        saved_rule_id = None
        if updated and should_create_rule:
            saved_rule_id = save_category_rule(
                conn,
                keyword,
                payload["category"],
                source=CATEGORY_RULE_SOURCE_MANUAL,
                amount_min=amount_min,
                amount_max=amount_max,
                tags=payload.get("tags") or [],
                merchant_id=row.get("merchant_id"),
            )

        if not updated:
            return {"updated": False, "message": "Transaction not found."}

        if should_create_rule:
            return {
                "updated": True,
                "saved_rule_id": saved_rule_id,
                "message": "AI suggestion applied. Rule saved.",
                "rule_label": f"{keyword}{amount_bounds_label(amount_min, amount_max)}",
            }
        return {
            "updated": True,
            "saved_rule_id": None,
            "message": "AI suggestion applied to this transaction.",
        }


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
    request_ok,
    unknown_category,
    model_name,
):
    """Build a JSON-serializable modal view model for a one-off AI run."""
    metadata = parse_ai_metadata(llm_transaction.get("category_metadata"))
    tags = list(llm_transaction.get("tags") or [])
    original_tag_list = list(original_tags or [])
    category = llm_transaction.get("category")
    can_apply = bool(request_ok and category and category != unknown_category)
    message = ai_suggestion_message(request_status, can_apply)
    amount = original_row.get("amount")
    return {
        "ok": bool(request_ok),
        "applied": False,
        "can_apply": can_apply,
        "message": message,
        "transaction_id": original_row["id"],
        "description": original_row["description"],
        "account_name": original_row.get("account_name"),
        "tx_date": stringify_date(original_row.get("tx_date")),
        "amount": amount,
        "transaction_kind": original_row.get("transaction_kind"),
        "transaction_kind_label": TRANSACTION_KINDS.get(
            original_row.get("transaction_kind"),
            original_row.get("transaction_kind"),
        ),
        "previous_category": original_row.get("category"),
        "previous_tags": original_tag_list,
        "previous_tag_pills": tag_pills(original_tag_list, tag_colors),
        "category": category,
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
        "rule_keyword": llm_transaction_rule_keyword(original_row),
        "rule_exact_amount": f"{amount:.2f}" if amount is not None else "",
        "persistence": ai_suggestion_persistence(llm_transaction, metadata),
    }


def ai_suggestion_persistence(llm_transaction, metadata):
    """Return the AI suggestion fields needed for a later explicit apply."""
    return {
        "category": llm_transaction.get("category"),
        "tags": list(llm_transaction.get("tags") or []),
        "needs_review": 1 if llm_transaction.get("needs_review") else 0,
        "category_source": llm_transaction.get("category_source"),
        "category_confidence": llm_transaction.get("category_confidence"),
        "category_rule_id": llm_transaction.get("category_rule_id"),
        "category_metadata": metadata,
        "categorized_at": llm_transaction.get("categorized_at"),
        "reviewed_at": llm_transaction.get("reviewed_at"),
        "amount": llm_transaction.get("amount"),
        "transaction_kind": llm_transaction.get("transaction_kind"),
    }


def accepted_ai_suggestion_payload(payload):
    """Return a persistence payload representing a user-accepted AI suggestion."""
    accepted = dict(payload)
    accepted_at = utc_timestamp()
    metadata = parse_ai_metadata(accepted.get("category_metadata"))
    metadata.update(
        {
            "accepted_by_user": True,
            "accepted_source": "single_transaction_ai_suggestion",
            "review_required_before_acceptance": bool(accepted.get("needs_review")),
        }
    )
    accepted["category_metadata"] = metadata
    accepted["needs_review"] = 0
    accepted["categorized_at"] = accepted.get("categorized_at") or accepted_at
    accepted["reviewed_at"] = accepted_at
    return accepted


def llm_transaction_rule_keyword(original_row):
    """Return the default rule keyword shown with an AI suggestion."""
    return normalize_rule_keyword("", original_row.get("description", ""))


def ai_suggestion_message(request_status, can_apply):
    """Return a user-facing message for a one-transaction AI suggestion."""
    if request_status.get("status") == "ok":
        return "AI suggestion ready." if can_apply else "AI suggestion cannot be applied."
    return ai_request_failure_message(request_status)


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


def disabled_ai_apply_result():
    """Return the apply result shown when single-transaction AI is disabled."""
    return {
        "updated": False,
        "message": "Single-transaction AI is disabled in settings.",
    }


def missing_transaction_apply_result(transaction_id):
    """Return the apply result shown when the selected transaction no longer exists."""
    return {
        "updated": False,
        "transaction_id": transaction_id,
        "message": "Transaction not found.",
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
