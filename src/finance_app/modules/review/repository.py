"""Review persistence helpers.

Provides SQLAlchemy-backed candidate queries for review workflows. Callers own
transaction boundaries and pass returned rows to service or presenter helpers.
"""

from finance_app.modules.categories.taxonomy import get_transaction_tags_by_id
from finance_app.modules.review.normalization import review_merchant_key
from finance_app.modules.review.queries import review_candidate_rows


def review_candidates_with_tags(conn, unknown_category, merchant_candidate=""):
    """Return review candidate rows plus their transaction-tag mapping."""
    rows = review_candidate_rows(conn, unknown_category, merchant_candidate)
    transaction_tags = get_transaction_tags_by_id(conn, [row["id"] for row in rows])
    return rows, transaction_tags


def review_group_rows(conn, merchant_key, unknown_category):
    """Return review candidate rows for one normalized merchant key."""
    return [
        row
        for row in review_candidate_rows(conn, unknown_category, merchant_key)
        if review_merchant_key(row["description"], conn=conn) == merchant_key
    ]
