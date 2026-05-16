"""Constants for transaction filtering and presentation modes.

Defines request/query-string vocabularies used by transaction routes and
dashboard links. These values are UI filters rather than persisted database
states, so they stay near the transaction feature instead of the schema.
"""

from finance_app.core.constants import CATEGORY_SOURCE_AI, CATEGORY_SOURCE_HISTORY, CATEGORY_SOURCE_RULE


CATEGORY_STATUS_UNKNOWN = "unknown"
CATEGORY_STATUS_CATEGORIZED = "categorized"
CATEGORY_STATUS_FILTERS = (
    "",
    CATEGORY_STATUS_UNKNOWN,
    CATEGORY_STATUS_CATEGORIZED,
)

CATEGORY_SOURCE_FILTER_MANUAL_REVIEWED = "manual_reviewed"
CATEGORY_SOURCE_FILTER_OPTIONS = (
    ("", "All sources"),
    (CATEGORY_SOURCE_FILTER_MANUAL_REVIEWED, "Manual reviewed"),
    (CATEGORY_SOURCE_RULE, "Rule"),
    (CATEGORY_SOURCE_HISTORY, "Similarity"),
    (CATEGORY_SOURCE_AI, "AI"),
)

AMOUNT_TYPE_SPENDING = "spending"
AMOUNT_TYPE_INCOME = "income"
AMOUNT_TYPE_CREDIT = "credit"
AMOUNT_TYPE_PAYMENT = "payment"
AMOUNT_TYPE_TRANSFER = "transfer"
AMOUNT_TYPE_FILTERS = (
    "",
    AMOUNT_TYPE_SPENDING,
    AMOUNT_TYPE_INCOME,
    AMOUNT_TYPE_CREDIT,
    AMOUNT_TYPE_PAYMENT,
    AMOUNT_TYPE_TRANSFER,
)

REVIEW_FILTER_NEEDS_REVIEW = "needs_review"
REVIEW_FILTER_READY_TO_APPROVE = "ready_to_approve"
REVIEW_FILTER_VERIFIED = "verified"
REVIEW_FILTERS = (
    "",
    REVIEW_FILTER_NEEDS_REVIEW,
    REVIEW_FILTER_READY_TO_APPROVE,
    REVIEW_FILTER_VERIFIED,
)
REVIEW_FILTER_OPTIONS = (
    ("", "All"),
    (REVIEW_FILTER_NEEDS_REVIEW, "Needs review"),
    (REVIEW_FILTER_READY_TO_APPROVE, "Ready to approve"),
    (REVIEW_FILTER_VERIFIED, "Verified"),
)

IGNORED_FILTER_ACTIVE = "active"
IGNORED_FILTER_IGNORED = "ignored"
IGNORED_FILTER_ALL = "all"
IGNORED_FILTERS = (
    IGNORED_FILTER_ACTIVE,
    IGNORED_FILTER_IGNORED,
    IGNORED_FILTER_ALL,
)
IGNORED_FILTER_OPTIONS = (
    (IGNORED_FILTER_ACTIVE, "Active only"),
    (IGNORED_FILTER_IGNORED, "Ignored only"),
    (IGNORED_FILTER_ALL, "All"),
)

TRANSACTION_SORT_DATE = "date"
TRANSACTION_SORT_ACCOUNT = "account"
TRANSACTION_SORT_DESCRIPTION = "description"
TRANSACTION_SORT_AMOUNT = "amount"
TRANSACTION_SORT_CATEGORY = "category"
TRANSACTION_SORT_REVIEW = "review"
TRANSACTION_SORT_IGNORED = "ignored"
