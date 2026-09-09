"""Presentation shaping for reimbursement monitoring views."""

from collections.abc import Callable, Mapping, Sequence
from decimal import Decimal
from typing import Any

from finance_app.core.filters import format_date, format_money
from finance_app.core.i18n import gettext
from finance_app.core.money import money_to_decimal
from finance_app.modules.reimbursements.constants import REIMBURSABLE_TAG

MATCH_CANDIDATE_LIMIT = 5
SEARCH_FIELDS = (
    "action_reimbursements_q",
    "action_expenses_q",
    "received_q",
    "expenses_q",
    "history_q",
)


def build_reimbursements_view_model(
    reimbursement_rows: Sequence[dict[str, Any]],
    expense_rows: Sequence[dict[str, Any]],
    allocation_rows: Sequence[dict[str, Any]],
    expense_tag_map: Mapping[Any, Sequence[str]] | None = None,
    tag_colors: Mapping[str, str] | None = None,
    search_queries: Mapping[str, object] | None = None,
) -> dict[str, Any]:
    """Build a template-friendly reimbursement monitoring model."""
    search = normalize_search_queries(search_queries)
    reimbursements = [build_reimbursement_row(row) for row in reimbursement_rows]
    allocations = [build_allocation_row(row) for row in allocation_rows]
    allocations = with_allocation_update_limits(allocations)
    expenses = [
        build_expense_row(
            row,
            list((expense_tag_map or {}).get(row["id"], ())),
            tag_colors or {},
        )
        for row in expense_rows
    ]
    expenses = with_expense_matches(expenses, allocations)
    reimbursement_options = [row for row in reimbursements if row["remaining"] > 0]
    expense_options = active_reimbursable_expenses(expenses)
    reimbursement_match_items = build_reimbursement_match_items(reimbursement_options, expense_options, allocations)
    expenses = with_expense_reimbursement_candidates(expenses, reimbursement_options, allocations)
    reimbursement_match_modal_items = build_reimbursement_match_modal_payloads(reimbursement_match_items)
    expense_detail_modal_items = build_expense_detail_modal_payloads(expenses)
    expense_options = active_reimbursable_expenses(expenses)
    action_reimbursements = search_rows(
        reimbursement_match_items,
        search["action_reimbursements_q"],
        reimbursement_search_text,
    )
    action_expenses = search_rows(expense_options, search["action_expenses_q"], expense_search_text)
    return {
        "summary": build_summary(reimbursements, expenses, allocations),
        "reimbursements": search_rows(reimbursements, search["received_q"], reimbursement_search_text),
        "reimbursable_expenses": search_rows(expenses, search["expenses_q"], expense_search_text),
        "allocations": search_rows(allocations, search["history_q"], allocation_search_text),
        "reimbursement_options": reimbursement_options,
        "expense_options": expense_options,
        "reimbursement_match_items": reimbursement_match_items,
        "expense_detail_rows": expenses,
        "reimbursement_match_modal_items": reimbursement_match_modal_items,
        "expense_detail_modal_items": expense_detail_modal_items,
        "search": search,
        "action_needed": build_action_needed(
            action_reimbursements,
            action_expenses,
            has_items=bool(reimbursement_options or expense_options),
            first_reimbursement_id=reimbursement_options[0]["id"] if reimbursement_options else None,
        ),
    }


def build_reimbursement_row(row: dict[str, Any]) -> dict[str, Any]:
    """Return one display row for an incoming reimbursement credit."""
    amount = abs(money_to_decimal(row["amount"]))
    allocated = money_to_decimal(row["allocated"])
    remaining = amount - allocated
    return {
        "id": row["id"],
        "date": row["tx_date"],
        "description": row["description"],
        "amount": amount,
        "allocated": allocated,
        "remaining": remaining,
        "matched_percent": percentage(allocated, amount),
        "status_label": reimbursement_status_label(allocated, remaining),
        "status_class": reimbursement_status_class(allocated, remaining),
    }


def build_expense_row(
    row: dict[str, Any],
    tags: Sequence[str] | None = None,
    tag_colors: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Return one display row for an expense that can be reimbursed."""
    amount = money_to_decimal(row["amount"])
    allocated = money_to_decimal(row["allocated"])
    remaining = amount - allocated
    completed = bool(row.get("completion_id"))
    pending_remaining = Decimal("0") if completed else remaining
    tag_list = list(tags or ())
    has_reimbursable_tag = bool(row.get("has_reimbursable_tag")) or REIMBURSABLE_TAG in tag_list
    return {
        "id": row["id"],
        "date": row["tx_date"],
        "description": row["description"],
        "category": row["category"],
        "account_name": row.get("account_name"),
        "amount": amount,
        "allocated": allocated,
        "remaining": remaining,
        "pending_remaining": pending_remaining,
        "is_complete": completed,
        "completed_at": row.get("completed_at"),
        "has_reimbursable_tag": has_reimbursable_tag,
        "tags": tag_list,
        "tag_pills": tag_pills(tag_list, tag_colors or {}),
        "reimbursed_percent": percentage(allocated, amount),
        "status_label": expense_status_label(allocated, remaining, completed),
        "status_class": expense_status_class(allocated, remaining, completed),
        "matched_reimbursements": [],
        "matched_reimbursement_count": 0,
    }


def build_allocation_row(row: dict[str, Any]) -> dict[str, Any]:
    """Return one display row for an allocation link."""
    return {
        "id": row["id"],
        "amount": money_to_decimal(row["amount"]),
        "created_at": row["created_at"],
        "reimbursement_transaction_id": row["reimbursement_transaction_id"],
        "expense_transaction_id": row["expense_transaction_id"],
        "reimbursement_date": row["reimbursement_date"],
        "reimbursement_description": row["reimbursement_description"],
        "reimbursement_amount": abs(money_to_decimal(row["reimbursement_amount"])),
        "expense_date": row["expense_date"],
        "expense_description": row["expense_description"],
        "expense_amount": money_to_decimal(row["expense_amount"]),
        "expense_category": row["expense_category"],
    }


def with_allocation_update_limits(allocations: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Add per-row edit maximums for existing reimbursement matches."""
    reimbursement_totals: dict[int, Decimal] = {}
    expense_totals: dict[int, Decimal] = {}
    for row in allocations:
        reimbursement_id = int(row["reimbursement_transaction_id"])
        expense_id = int(row["expense_transaction_id"])
        reimbursement_totals[reimbursement_id] = (
            reimbursement_totals.get(reimbursement_id, Decimal("0")) + row["amount"]
        )
        expense_totals[expense_id] = expense_totals.get(expense_id, Decimal("0")) + row["amount"]

    limited_rows = []
    for row in allocations:
        reimbursement_id = int(row["reimbursement_transaction_id"])
        expense_id = int(row["expense_transaction_id"])
        amount = row["amount"]
        reimbursement_limit = row["reimbursement_amount"]
        expense_limit = row["expense_amount"]
        reimbursement_other = reimbursement_totals[reimbursement_id] - amount
        expense_other = expense_totals[expense_id] - amount
        max_amount = min(reimbursement_limit - reimbursement_other, expense_limit - expense_other)
        limited_rows.append({**row, "max_amount": max(max_amount, Decimal("0"))})
    return limited_rows


def with_expense_matches(
    expenses: Sequence[dict[str, Any]],
    allocations: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Attach reimbursement matches to each expense row."""
    matches_by_expense: dict[int, list[dict[str, Any]]] = {}
    for row in allocations:
        expense_id = int(row["expense_transaction_id"])
        matches_by_expense.setdefault(expense_id, []).append(
            {
                "allocation_id": row["id"],
                "reimbursement_transaction_id": row["reimbursement_transaction_id"],
                "date": row["reimbursement_date"],
                "description": row["reimbursement_description"],
                "amount": row["amount"],
                "reimbursement_amount": row["reimbursement_amount"],
            }
        )

    matched_rows = []
    for row in expenses:
        matches = matches_by_expense.get(int(row["id"]), [])
        matched_rows.append(
            {
                **row,
                "matched_reimbursements": matches,
                "matched_reimbursement_count": len(matches),
            }
        )
    return matched_rows


def with_expense_reimbursement_candidates(
    expenses: Sequence[dict[str, Any]],
    reimbursements: Sequence[dict[str, Any]],
    allocations: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Attach date-aware reimbursement candidates to each expense detail row."""
    matched_pairs = {(row["reimbursement_transaction_id"], row["expense_transaction_id"]) for row in allocations}
    return [
        {
            **row,
            **build_expense_match_candidate_summary(row, reimbursements, matched_pairs),
        }
        for row in expenses
    ]


def build_summary(
    reimbursements: Sequence[dict[str, Any]],
    expenses: Sequence[dict[str, Any]],
    allocations: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    """Return aggregate metrics for the reimbursement page."""
    active_expenses = active_reimbursable_expenses(expenses)
    total_reimbursed = sum((row["amount"] for row in reimbursements), Decimal("0"))
    total_allocated = sum((row["amount"] for row in allocations), Decimal("0"))
    pending_credits = sum((positive_remaining(row) for row in reimbursements), Decimal("0"))
    pending_expenses = sum((positive_pending_remaining(row) for row in active_expenses), Decimal("0"))
    pending_credit_count = sum(1 for row in reimbursements if positive_remaining(row) > 0)
    pending_expense_count = len(active_expenses)
    return {
        "reimbursement_count": len(reimbursements),
        "expense_count": len(expenses),
        "allocation_count": len(allocations),
        "total_reimbursed": total_reimbursed,
        "total_allocated": total_allocated,
        "pending_reimbursement_credits": pending_credits,
        "pending_reimbursement_count": pending_credit_count,
        "pending_reimbursable_expenses": pending_expenses,
        "pending_expense_count": pending_expense_count,
    }


def build_reimbursement_match_items(
    reimbursements: Sequence[dict[str, Any]],
    expenses: Sequence[dict[str, Any]],
    allocations: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return reimbursement rows with their eligible expense candidates."""
    matched_pairs = {(row["reimbursement_transaction_id"], row["expense_transaction_id"]) for row in allocations}
    return [
        {
            **row,
            **build_match_candidate_summary(row, expenses, matched_pairs),
        }
        for row in reimbursements
    ]


def build_action_needed(
    reimbursements: Sequence[dict[str, Any]],
    expenses: Sequence[dict[str, Any]],
    *,
    has_items: bool,
    first_reimbursement_id: int | None,
) -> dict[str, Any]:
    """Return unresolved items and contextual match candidates."""
    return {
        "reimbursements": list(reimbursements),
        "expenses": list(expenses),
        "first_reimbursement_id": first_reimbursement_id,
        "has_items": has_items,
        "total_reimbursement_count": len(reimbursements),
        "total_expense_count": len(expenses),
    }


def active_reimbursable_expenses(expenses: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return tagged, active expense rows that still need reimbursement action."""
    return [
        row
        for row in expenses
        if row["has_reimbursable_tag"] and not row["is_complete"] and positive_pending_remaining(row) > 0
    ]


def build_match_candidate_summary(
    reimbursement: dict[str, Any],
    expenses: Sequence[dict[str, Any]],
    matched_pairs: set[tuple[int, int]],
) -> dict[str, Any]:
    """Return a limited set of candidate expenses for one reimbursement."""
    reimbursement_remaining = positive_remaining(reimbursement)
    candidates: list[dict[str, Any]] = []
    candidate_count = 0
    eligible_expenses = sorted(
        (expense for expense in expenses if date_key(expense.get("date")) < date_key(reimbursement.get("date"))),
        key=lambda row: (date_key(row.get("date")), int(row["id"])),
        reverse=True,
    )
    for expense in eligible_expenses:
        expense_remaining = positive_pending_remaining(expense)
        if expense_remaining <= 0:
            continue
        if (reimbursement["id"], expense["id"]) in matched_pairs:
            continue

        candidate_count += 1
        if len(candidates) >= MATCH_CANDIDATE_LIMIT:
            continue

        max_amount = min(reimbursement_remaining, expense_remaining)
        candidates.append(
            {
                **expense,
                "default_amount": max_amount,
                "max_amount": max_amount,
            }
        )
    return {
        "match_candidates": candidates,
        "match_candidate_count": candidate_count,
        "hidden_match_candidate_count": max(0, candidate_count - len(candidates)),
    }


def build_expense_match_candidate_summary(
    expense: dict[str, Any],
    reimbursements: Sequence[dict[str, Any]],
    matched_pairs: set[tuple[int, int]],
) -> dict[str, Any]:
    """Return a limited set of candidate reimbursements for one expense."""
    expense_remaining = positive_pending_remaining(expense)
    candidates: list[dict[str, Any]] = []
    candidate_count = 0
    eligible_reimbursements = sorted(
        (
            reimbursement
            for reimbursement in reimbursements
            if date_key(reimbursement.get("date")) > date_key(expense.get("date"))
        ),
        key=lambda row: (date_key(row.get("date")), int(row["id"])),
    )
    for reimbursement in eligible_reimbursements:
        reimbursement_remaining = positive_remaining(reimbursement)
        if reimbursement_remaining <= 0:
            continue
        if (reimbursement["id"], expense["id"]) in matched_pairs:
            continue

        candidate_count += 1
        if len(candidates) >= MATCH_CANDIDATE_LIMIT:
            continue

        max_amount = min(expense_remaining, reimbursement_remaining)
        candidates.append(
            {
                **reimbursement,
                "default_amount": max_amount,
                "max_amount": max_amount,
            }
        )
    return {
        "reimbursement_candidates": candidates,
        "reimbursement_candidate_count": candidate_count,
        "hidden_reimbursement_candidate_count": max(0, candidate_count - len(candidates)),
    }


def build_reimbursement_match_modal_payloads(rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return JSON-ready reimbursement match modal payloads."""
    return [build_reimbursement_match_modal_payload(row) for row in rows]


def build_reimbursement_match_modal_payload(row: dict[str, Any]) -> dict[str, Any]:
    """Return one JSON-ready payload for the shared reimbursement match modal."""
    candidates = [build_reimbursement_expense_candidate_payload(candidate) for candidate in row["match_candidates"]]
    return {
        "id": row["id"],
        "date": date_key(row.get("date")),
        "date_label": format_date(row.get("date")),
        "description": row["description"],
        "amount": decimal_string(row["amount"]),
        "amount_label": format_money(row["amount"]),
        "allocated": decimal_string(row["allocated"]),
        "allocated_label": format_money(row["allocated"]),
        "remaining": decimal_string(row["remaining"]),
        "remaining_label": format_money(row["remaining"]),
        "candidate_count": row["match_candidate_count"],
        "candidate_count_label": gettext("{count} expenses", count=row["match_candidate_count"]),
        "hidden_candidate_count": row["hidden_match_candidate_count"],
        "hidden_candidate_message": hidden_reimbursement_candidate_message(
            row["match_candidates"],
            row["match_candidate_count"],
        ),
        "candidates": candidates,
    }


def build_reimbursement_expense_candidate_payload(row: dict[str, Any]) -> dict[str, Any]:
    """Return a JSON-ready expense candidate for a reimbursement match modal."""
    return {
        "id": row["id"],
        "date": date_key(row.get("date")),
        "date_label": format_date(row.get("date")),
        "category": row["category"],
        "description": row["description"],
        "amount": decimal_string(row["amount"]),
        "amount_label": format_money(row["amount"]),
        "allocated": decimal_string(row["allocated"]),
        "allocated_label": format_money(row["allocated"]),
        "pending_remaining": decimal_string(row["pending_remaining"]),
        "pending_remaining_label": format_money(row["pending_remaining"]),
        "default_amount": decimal_string(row["default_amount"]),
        "max_amount": decimal_string(row["max_amount"]),
    }


def hidden_reimbursement_candidate_message(
    candidates: Sequence[dict[str, Any]],
    candidate_count: int,
) -> str:
    """Return the hidden-candidate note for a reimbursement match modal."""
    if candidate_count <= len(candidates):
        return ""
    return gettext(
        "Showing {shown} of {total} candidate expenses. Use the Expenses tab to review all.",
        shown=len(candidates),
        total=candidate_count,
    )


def build_expense_detail_modal_payloads(rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return JSON-ready expense detail modal payloads."""
    return [build_expense_detail_modal_payload(row) for row in rows]


def build_expense_detail_modal_payload(row: dict[str, Any]) -> dict[str, Any]:
    """Return one JSON-ready payload for the shared expense detail modal."""
    reimbursement_candidates = [
        build_expense_reimbursement_candidate_payload(candidate) for candidate in row["reimbursement_candidates"]
    ]
    matched_reimbursements = [
        {
            "date": date_key(match.get("date")),
            "date_label": format_date(match.get("date")),
            "description": match["description"],
            "amount": decimal_string(match["amount"]),
            "amount_label": format_money(match["amount"]),
        }
        for match in row["matched_reimbursements"]
    ]
    return {
        "id": row["id"],
        "date": date_key(row.get("date")),
        "date_label": format_date(row.get("date")),
        "description": row["description"],
        "category": row["category"],
        "account_name": row["account_name"] or gettext("Personal"),
        "amount": decimal_string(row["amount"]),
        "amount_label": format_money(row["amount"]),
        "allocated": decimal_string(row["allocated"]),
        "allocated_label": format_money(row["allocated"]),
        "pending_remaining": decimal_string(row["pending_remaining"]),
        "pending_remaining_label": format_money(row["pending_remaining"]),
        "status_label": gettext(row["status_label"]),
        "status_class": row["status_class"],
        "has_reimbursable_tag": bool(row["has_reimbursable_tag"]),
        "has_reimbursable_tag_label": gettext("Yes") if row["has_reimbursable_tag"] else gettext("No"),
        "is_complete": bool(row["is_complete"]),
        "tags": [{"name": tag["name"], "color": tag["color"]} for tag in row["tag_pills"]],
        "reimbursement_candidate_count": row["reimbursement_candidate_count"],
        "reimbursement_candidate_count_label": gettext(
            "{count} credits",
            count=row["reimbursement_candidate_count"],
        ),
        "hidden_reimbursement_candidate_count": row["hidden_reimbursement_candidate_count"],
        "hidden_reimbursement_candidate_message": hidden_expense_candidate_message(
            row["reimbursement_candidates"],
            row["reimbursement_candidate_count"],
        ),
        "reimbursement_candidates": reimbursement_candidates,
        "matched_reimbursement_count": row["matched_reimbursement_count"],
        "matched_reimbursement_count_label": gettext("{count} matches", count=row["matched_reimbursement_count"]),
        "matched_reimbursements": matched_reimbursements,
    }


def build_expense_reimbursement_candidate_payload(row: dict[str, Any]) -> dict[str, Any]:
    """Return a JSON-ready reimbursement candidate for an expense detail modal."""
    return {
        "id": row["id"],
        "date": date_key(row.get("date")),
        "date_label": format_date(row.get("date")),
        "description": row["description"],
        "amount": decimal_string(row["amount"]),
        "amount_label": format_money(row["amount"]),
        "allocated": decimal_string(row["allocated"]),
        "allocated_label": format_money(row["allocated"]),
        "remaining": decimal_string(row["remaining"]),
        "remaining_label": format_money(row["remaining"]),
        "default_amount": decimal_string(row["default_amount"]),
        "max_amount": decimal_string(row["max_amount"]),
    }


def hidden_expense_candidate_message(
    candidates: Sequence[dict[str, Any]],
    candidate_count: int,
) -> str:
    """Return the hidden-candidate note for an expense detail modal."""
    if candidate_count <= len(candidates):
        return ""
    return gettext(
        "Showing {shown} of {total} candidate reimbursements. Use the Received reimbursements tab to review all.",
        shown=len(candidates),
        total=candidate_count,
    )


def decimal_string(value: Any) -> str:
    """Return a JSON-safe decimal string for form values and limits."""
    return format(money_to_decimal(value), "f")


def normalize_search_queries(search_queries: Mapping[str, object] | None) -> dict[str, str]:
    """Return trimmed reimbursement table search values keyed by query param."""
    search_queries = search_queries or {}
    return {key: str(search_queries.get(key, "") or "").strip() for key in SEARCH_FIELDS}


def search_rows(
    rows: Sequence[dict[str, Any]],
    query: object,
    text_builder: Callable[[dict[str, Any]], str],
) -> list[dict[str, Any]]:
    """Return rows whose searchable table text contains every query term."""
    terms = [term.casefold() for term in str(query or "").split() if term.strip()]
    if not terms:
        return list(rows)
    return [row for row in rows if all(term in text_builder(row).casefold() for term in terms)]


def reimbursement_search_text(row: dict[str, Any]) -> str:
    """Return searchable text for reimbursement table rows."""
    return searchable_text(
        row.get("date"),
        row.get("description"),
        row.get("amount"),
        row.get("allocated"),
        row.get("remaining"),
        row.get("matched_percent"),
        row.get("status_label"),
    )


def expense_search_text(row: dict[str, Any]) -> str:
    """Return searchable text for reimbursable expense table rows."""
    return searchable_text(
        row.get("date"),
        row.get("category"),
        row.get("description"),
        row.get("account_name"),
        row.get("amount"),
        row.get("allocated"),
        row.get("remaining"),
        row.get("pending_remaining"),
        row.get("reimbursed_percent"),
        row.get("status_label"),
        row.get("tags"),
    )


def allocation_search_text(row: dict[str, Any]) -> str:
    """Return searchable text for reimbursement history table rows."""
    return searchable_text(
        row.get("reimbursement_date"),
        row.get("reimbursement_description"),
        row.get("reimbursement_amount"),
        row.get("expense_date"),
        row.get("expense_description"),
        row.get("expense_amount"),
        row.get("expense_category"),
        row.get("amount"),
    )


def searchable_text(*values: Any) -> str:
    """Flatten display values into case-insensitive table-search text."""
    parts: list[str] = []
    for value in values:
        if value is None:
            continue
        if isinstance(value, str):
            parts.append(value)
        elif isinstance(value, Decimal):
            parts.extend((str(value), f"{value:.2f}"))
        elif isinstance(value, Mapping):
            parts.append(searchable_text(*value.values()))
        elif isinstance(value, Sequence):
            parts.append(searchable_text(*value))
        else:
            parts.append(str(value))
    return " ".join(parts)


def date_key(value: Any) -> str:
    """Return an ISO-like date key that can compare date objects and strings."""
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value or "")


def positive_remaining(row: dict[str, Any]) -> Decimal:
    """Return a non-negative remaining amount for summary totals."""
    return max(row["remaining"], Decimal("0"))


def positive_pending_remaining(row: dict[str, Any]) -> Decimal:
    """Return non-negative remaining amount still needing reimbursement action."""
    return max(row["pending_remaining"], Decimal("0"))


def percentage(part: Decimal, total: Decimal) -> int:
    """Return a bounded whole-number percentage for progress indicators."""
    if total <= 0:
        return 0
    return min(100, max(0, int((part / total) * 100)))


def tag_pills(tags: Sequence[str], tag_colors: Mapping[str, str]) -> list[dict[str, str]]:
    """Return tag display pills for an expense detail modal."""
    return [
        {
            "name": tag,
            "color": tag_colors.get(tag, "#64748b"),
        }
        for tag in tags
    ]


def reimbursement_status_label(allocated: Decimal, remaining: Decimal) -> str:
    """Return the status label for a reimbursement credit."""
    if remaining <= 0:
        return "Fully matched"
    if allocated > 0:
        return "Partially matched"
    return "Unmatched"


def reimbursement_status_class(allocated: Decimal, remaining: Decimal) -> str:
    """Return Bootstrap badge classes for a reimbursement credit."""
    if remaining <= 0:
        return "text-bg-success"
    if allocated > 0:
        return "text-bg-warning"
    return "text-bg-info"


def expense_status_label(allocated: Decimal, remaining: Decimal, completed: bool) -> str:
    """Return the status label for a reimbursable expense."""
    if completed:
        return "Complete"
    if remaining <= 0:
        return "Fully reimbursed"
    if allocated > 0:
        return "Partially reimbursed"
    return "Awaiting reimbursement"


def expense_status_class(allocated: Decimal, remaining: Decimal, completed: bool) -> str:
    """Return Bootstrap badge classes for a reimbursable expense."""
    if completed:
        return "text-bg-success"
    if remaining <= 0:
        return "text-bg-success"
    if allocated > 0:
        return "text-bg-warning"
    return "text-bg-secondary"
