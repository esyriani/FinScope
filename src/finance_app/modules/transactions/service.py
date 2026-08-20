"""Application orchestration for the transactions feature."""

from collections.abc import Iterable, Mapping, MutableMapping, Sequence
from typing import Any

from finance_app.background.runner import (
    AI_JOB_QUEUE,
    append_background_job_log,
    is_job_cancel_requested,
    raise_if_cancel_requested,
    submit_background_job,
    update_background_job_progress,
)
from finance_app.core.config import settings
from finance_app.core.constants import CATEGORY_RULE_SOURCE_MANUAL, UNKNOWN_CATEGORY
from finance_app.core.money import MoneyValue
from finance_app.core.periods import DATE_PERIOD_OPTIONS, PERIOD_CUSTOM
from finance_app.database.engine import db_core_transaction
from finance_app.modules.accounts.queries import list_account_options
from finance_app.modules.categories import llm as llm_module
from finance_app.modules.categories.categorization import categorize_transactions
from finance_app.modules.categories.llm_workflow import (
    PreparedTransactionLlmCategorization,
    prepare_transaction_llm_categorization,
    request_prepared_transaction_llm_categorization,
)
from finance_app.modules.categories.repository import get_category_rules
from finance_app.modules.categories.service import (
    estimate_llm_categorization_tokens,
    get_category_options,
    normalize_category,
    save_category_rule,
)
from finance_app.modules.categories.sources import utc_timestamp
from finance_app.modules.categories.taxonomy import (
    get_category_description_map,
    get_tag_color_map,
    get_tag_option_rows,
    get_tag_options,
    get_transaction_tag_names,
    get_transaction_tags_by_id,
    normalize_tag_names,
)
from finance_app.modules.rules.forms import amount_bounds_label, normalize_rule_keyword, parse_amount_bounds
from finance_app.modules.settings.runtime import (
    confirm_ai_token_usage_enabled,
    get_bool_setting,
    get_int_setting,
    get_setting,
    get_unknown_category,
)
from finance_app.modules.transactions.ai_presenter import (
    ai_token_estimate_result,
    build_transaction_ai_result,
    disabled_ai_apply_result,
    disabled_ai_estimate_result,
    disabled_ai_result,
    missing_transaction_ai_estimate_result,
    missing_transaction_ai_result,
    missing_transaction_apply_result,
    parse_ai_metadata,
)
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
    fetch_transactions,
)
from finance_app.modules.transactions.repository import (
    apply_ai_category_update,
    assign_manual_category,
    get_transaction_for_ai_categorization,
    get_transaction_for_category_update,
    get_transactions_for_recategorization,
    mark_transaction_verified,
    mark_transactions_verified,
    normalized_transaction_ids,
    set_transaction_ignored,
    set_transactions_ignored,
    update_recategorized_transaction,
)
from finance_app.modules.transactions.urls import transactions_sort_url, transactions_url

RUN_TRANSACTION_AI_SETTING_KEY = "transaction_ai_rerun_enabled"
APPLY_AI_SUGGESTION_ACTION = "apply"
APPLY_AI_SUGGESTION_WITH_RULE_ACTION = "apply_and_create_rule"


def build_transactions_context(args: Any) -> dict[str, Any]:
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
        all_transaction_ids = [row["id"] for row in fetched_rows]
        tag_map = get_transaction_tags_by_id(conn, [row["id"] for row in fetched_rows])
        rows = build_transaction_rows(fetched_rows, tag_map, get_tag_color_map(conn), conn)
        account_options = list_account_options(conn)
        categories = fetch_distinct_categories(conn)
        category_options = get_category_options(conn)
        category_descriptions = get_category_description_map(conn)
        tag_display_options = get_tag_option_rows(conn)
        run_transaction_ai_enabled = get_bool_setting(
            conn,
            RUN_TRANSACTION_AI_SETTING_KEY,
            settings.default_transaction_ai_rerun_enabled,
        )
        confirm_ai_token_usage = confirm_ai_token_usage_enabled(conn)

    return {
        "transactions": rows,
        "all_transaction_ids": all_transaction_ids,
        "categories": categories,
        "search": filters["search"],
        "selected_category": filters["category"],
        "selected_categories": filters["selected_categories"],
        "selected_tags": filters["selected_tags"],
        "selected_account_id": filters["account_id"],
        "account_options": account_options,
        "selected_review": filters["review"],
        "selected_category_source": filters["category_source"],
        "selected_ignored": filters["ignored"],
        "selected_period": filters["period"],
        "selected_date_from": filters["date_from"],
        "selected_date_to": filters["date_to"],
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
        "confirm_ai_token_usage_enabled": confirm_ai_token_usage,
    }


def update_transaction_category_from_form(transaction_id: int, form: Any) -> dict[str, Any]:
    """Apply a manual category update submitted for one transaction."""
    with db_core_transaction() as conn:
        category_options = get_category_options(conn)
        tag_options = get_tag_options(conn)
        new_category = normalize_category(form.get("category", ""), category_options)
        tag_names = normalize_tag_names(form.getlist("tags"), tag_options)

        if not new_category:
            return {"message": "Category cannot be empty."}

        tx = get_transaction_for_category_update(conn, transaction_id)
        if tx is None:
            return {"message": "Transaction not found."}

        description = tx["description"].strip()
        rule_action = form.get("rule_action", "transaction_only")
        merchant_key = ""
        amount_min = None
        amount_max = None

        if rule_action == "save":
            merchant_key = normalize_rule_keyword(form.get("keyword", ""), description)
            amount_min, amount_max = parse_amount_bounds(
                form.get("amount_min", ""),
                form.get("amount_max", ""),
            )
            if not merchant_key:
                return {"message": "Rule keyword is required when saving a rule."}

        result = assign_manual_category(
            conn,
            transaction_id,
            new_category,
            tag_names=tag_names,
            rule_keyword=merchant_key if rule_action == "save" else None,
            amount_min=amount_min,
            amount_max=amount_max,
            rule_merchant_id=tx["merchant_id"] if rule_action == "save" else None,
        )

        if not result.updated:
            return {"message": "Transaction not found."}

        if rule_action == "save":
            return {
                "message": (
                    "Category updated. Rule saved for: {keyword}{amount_bounds}"
                    if result.transaction_changed
                    else "Rule saved for: {keyword}{amount_bounds}"
                ),
                "params": {
                    "keyword": merchant_key,
                    "amount_bounds": amount_bounds_label(amount_min, amount_max),
                },
            }
        if result.transaction_changed:
            return {"message": "Category updated for this transaction only."}
        return {"message": "No transaction changes to save."}


def verify_transaction_by_id(transaction_id: int) -> bool:
    """Mark one transaction as manually verified and return whether it changed."""
    with db_core_transaction() as conn:
        return mark_transaction_verified(conn, transaction_id)


def set_transaction_ignored_by_id(transaction_id: int, ignored: int) -> bool:
    """Update one transaction ignored flag and return whether it changed."""
    with db_core_transaction() as conn:
        return set_transaction_ignored(conn, transaction_id, ignored)


def approve_selected_transactions(transaction_ids: Iterable[object] | None) -> int:
    """Approve selected transactions and return the number of changed rows."""
    ids = normalized_transaction_ids(transaction_ids)
    if not ids:
        return 0

    with db_core_transaction() as conn:
        return mark_transactions_verified(conn, ids)


def ignore_selected_transactions(transaction_ids: Iterable[object] | None) -> int:
    """Ignore selected transactions and return the number of changed rows."""
    ids = normalized_transaction_ids(transaction_ids)
    if not ids:
        return 0

    with db_core_transaction() as conn:
        return set_transactions_ignored(conn, ids, 1)


def queue_selected_transaction_recategorization(transaction_ids: Iterable[object] | None) -> dict[str, Any]:
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


def estimate_selected_transaction_recategorization(transaction_ids: Iterable[object] | None) -> dict[str, Any]:
    """Return a token estimate for selected-transaction recategorization."""
    ids = normalized_transaction_ids(transaction_ids)
    if not ids:
        return {"ok": False, "message": "Select at least one transaction."}

    with db_core_transaction() as conn:
        rows = get_transactions_for_recategorization(conn, ids)
        if not rows:
            return {"ok": False, "message": "No selected transactions found."}

        unknown_category = get_unknown_category(conn) or UNKNOWN_CATEGORY
        transactions = categorize_transactions([dict(row) for row in rows], conn=conn, use_llm=False)
        estimate = estimate_llm_categorization_tokens(
            conn,
            transactions,
            get_category_rules(conn),
            unknown_category,
        )

    return ai_token_estimate_result("selected_transactions", len(rows), estimate)


def recategorize_selected_transactions_job(transaction_ids: Iterable[object] | None) -> str:
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


def recategorize_selected_transaction_rows(rows: Sequence[Mapping[str, Any]]) -> int:
    """Categorize and persist one batch of selected transaction rows."""
    llm_module.clear_llm_request_status()
    transactions: list[MutableMapping[str, Any]] = [dict(row) for row in rows]
    prepared = prepare_transaction_llm_categorization(transactions)
    outcome = request_prepared_transaction_llm_categorization(prepared)
    with db_core_transaction() as conn:
        llm_module.apply_llm_categorization_outcome(
            conn,
            transactions,
            outcome,
            prepared.unknown_category,
        )
        updated_count = 0
        for transaction in transactions:
            if update_recategorized_transaction(conn, transaction, prepared.unknown_category):
                updated_count += 1
    return updated_count


def update_selected_recategorization_progress(
    current: int,
    total: int,
    updated: int,
    message: str | None = None,
    params: Mapping[str, Any] | None = None,
    log_message: str | None = None,
    log_params: Mapping[str, Any] | None = None,
    log_level: str = "info",
) -> None:
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


def append_selected_recategorization_log(
    message: str,
    params: Mapping[str, Any] | None = None,
    level: str = "info",
) -> None:
    """Append a selected-recategorization log entry to the current job."""
    append_background_job_log(message, params=params, level=level)


def suggest_transaction_ai_category(transaction_id: int) -> dict[str, Any]:
    """Run LLM categorization synchronously for one transaction.

    The action is suggestion-first: it suppresses automatic rule creation and
    does not mutate the selected row. The returned result contains the display
    model and a signed-session persistence payload used only if the user
    explicitly applies the suggestion from the modal dialog.
    """
    llm_module.clear_llm_request_status()
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
        request_context = llm_module.prepare_llm_categorization_request_context(
            conn,
            [llm_transaction],
            unknown_category,
        )
        model_name = (
            request_context.openai_model
            if request_context is not None
            else get_setting(conn, "openai_model") or settings.default_categorization_model
        )

    prepared = PreparedTransactionLlmCategorization(
        unknown_category=unknown_category,
        rules=rules,
        request_context=request_context,
    )
    outcome = request_prepared_transaction_llm_categorization(prepared)
    with db_core_transaction() as conn:
        llm_module.apply_llm_categorization_outcome(
            conn,
            [llm_transaction],
            outcome,
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
        model_name=model_name,
    )


def estimate_transaction_ai_category(transaction_id: int) -> dict[str, Any]:
    """Return a token estimate for a one-transaction AI category suggestion."""
    with db_core_transaction() as conn:
        if not get_bool_setting(
            conn,
            RUN_TRANSACTION_AI_SETTING_KEY,
            settings.default_transaction_ai_rerun_enabled,
        ):
            return disabled_ai_estimate_result()

        row = get_transaction_for_ai_categorization(conn, transaction_id)
        if row is None:
            return missing_transaction_ai_estimate_result(transaction_id)

        unknown_category = get_unknown_category(conn) or UNKNOWN_CATEGORY
        evidence_transaction = dict(row)
        categorize_transactions([evidence_transaction], conn=conn, use_llm=False)
        llm_transaction = prepare_single_transaction_llm_payload(evidence_transaction, row, unknown_category)
        estimate = estimate_llm_categorization_tokens(
            conn,
            [llm_transaction],
            get_category_rules(conn),
            unknown_category,
        )

    return ai_token_estimate_result("single_transaction", 1, estimate)


def apply_transaction_ai_suggestion(
    transaction_id: int,
    suggestion: Mapping[str, Any] | None,
    action: str = APPLY_AI_SUGGESTION_ACTION,
    rule_keyword: str = "",
    amount_min: MoneyValue | None = None,
    amount_max: MoneyValue | None = None,
) -> dict[str, Any]:
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


def prepare_single_transaction_llm_payload(
    evidence_transaction: Mapping[str, Any],
    original_row: Mapping[str, Any],
    unknown_category: str,
) -> dict[str, Any]:
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


def accepted_ai_suggestion_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
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
