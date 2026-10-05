"""Transaction review-status policy and SQL helpers.

This module centralizes the user-visible transaction status derived from
category provenance, confidence, manual approval, and ignored state. The
transaction list presenter and SQL filters both use these helpers so badges,
filters, sorting, and search terms stay aligned.
"""

from collections.abc import Mapping
from typing import Any

from sqlalchemy import case, literal, or_

from finance_app.core.category_sql import transaction_category_label_expression
from finance_app.core.constants import CATEGORY_SOURCE_UNKNOWN, UNKNOWN_CATEGORY
from finance_app.database.tables import transactions as transactions_table

TRANSACTION_STATUS_IGNORED = "ignored"
TRANSACTION_STATUS_NEEDS_REVIEW = "needs_review"
TRANSACTION_STATUS_PENDING_APPROVAL = "pending_approval"
TRANSACTION_STATUS_PRE_APPROVED = "pre_approved"
TRANSACTION_STATUS_APPROVED = "approved"

PENDING_APPROVAL_CONFIDENCE_THRESHOLD = 0.90
PRE_APPROVED_CONFIDENCE_THRESHOLD = 1.0

TRANSACTION_STATUS_LABELS = {
    TRANSACTION_STATUS_IGNORED: "Ignored",
    TRANSACTION_STATUS_NEEDS_REVIEW: "Needs review",
    TRANSACTION_STATUS_PENDING_APPROVAL: "Pending approval",
    TRANSACTION_STATUS_PRE_APPROVED: "Pre-approved",
    TRANSACTION_STATUS_APPROVED: "Approved",
}

TRANSACTION_STATUS_BADGE_CLASSES = {
    TRANSACTION_STATUS_IGNORED: "text-bg-secondary",
    TRANSACTION_STATUS_NEEDS_REVIEW: "text-bg-warning",
    TRANSACTION_STATUS_PENDING_APPROVAL: "text-bg-secondary",
    TRANSACTION_STATUS_PRE_APPROVED: "text-bg-primary",
    TRANSACTION_STATUS_APPROVED: "text-bg-success",
}


def transaction_review_status(row: Mapping[str, Any], unknown_category: str = UNKNOWN_CATEGORY) -> str:
    """Return the user-visible status for a transaction row."""
    if row.get("ignored"):
        return TRANSACTION_STATUS_IGNORED
    if row.get("reviewed_at"):
        return TRANSACTION_STATUS_APPROVED
    if row_is_unknown(row, unknown_category):
        return TRANSACTION_STATUS_NEEDS_REVIEW

    confidence = confidence_value(row.get("category_confidence"))
    if confidence is not None and confidence >= PRE_APPROVED_CONFIDENCE_THRESHOLD:
        return TRANSACTION_STATUS_PRE_APPROVED
    if confidence is not None and confidence >= PENDING_APPROVAL_CONFIDENCE_THRESHOLD:
        return TRANSACTION_STATUS_PENDING_APPROVAL
    return TRANSACTION_STATUS_NEEDS_REVIEW


def row_is_unknown(row: Mapping[str, Any], unknown_category: str) -> bool:
    """Return whether a row is still categorized as unknown."""
    category = str(row.get("category") or "").strip()
    source = str(row.get("category_source") or "").strip()
    return category == unknown_category or category == UNKNOWN_CATEGORY or source == CATEGORY_SOURCE_UNKNOWN


def confidence_value(value: object) -> float | None:
    """Return a numeric confidence value or ``None``."""
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return None


def transaction_status_label(status: str) -> str:
    """Return the display label for a transaction status key."""
    return TRANSACTION_STATUS_LABELS.get(status, "Needs review")


def transaction_status_badge_class(status: str) -> str:
    """Return the Bootstrap badge class for a transaction status key."""
    return TRANSACTION_STATUS_BADGE_CLASSES.get(status, "text-bg-warning")


def transaction_status_expression(unknown_category: str) -> Any:
    """Return a SQL expression matching ``transaction_review_status``."""
    category_value = transaction_category_label_expression(unknown_category)
    return case(
        (transactions_table.c.ignored == 1, literal(TRANSACTION_STATUS_IGNORED)),
        (transactions_table.c.reviewed_at.is_not(None), literal(TRANSACTION_STATUS_APPROVED)),
        (
            or_(
                category_value == unknown_category,
                category_value == UNKNOWN_CATEGORY,
                transactions_table.c.category_source == CATEGORY_SOURCE_UNKNOWN,
            ),
            literal(TRANSACTION_STATUS_NEEDS_REVIEW),
        ),
        (
            transactions_table.c.category_confidence >= PRE_APPROVED_CONFIDENCE_THRESHOLD,
            literal(TRANSACTION_STATUS_PRE_APPROVED),
        ),
        (
            transactions_table.c.category_confidence >= PENDING_APPROVAL_CONFIDENCE_THRESHOLD,
            literal(TRANSACTION_STATUS_PENDING_APPROVAL),
        ),
        else_=literal(TRANSACTION_STATUS_NEEDS_REVIEW),
    )


def transaction_status_rank_expression(unknown_category: str) -> Any:
    """Return a SQL sort rank for transaction review status."""
    status = transaction_status_expression(unknown_category)
    return case(
        (status == TRANSACTION_STATUS_NEEDS_REVIEW, 4),
        (status == TRANSACTION_STATUS_PENDING_APPROVAL, 3),
        (status == TRANSACTION_STATUS_PRE_APPROVED, 2),
        (status == TRANSACTION_STATUS_APPROVED, 1),
        else_=0,
    )
