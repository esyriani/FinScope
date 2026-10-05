"""View-model builders for the transactions feature."""

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from finance_app.core.constants import TRANSACTION_KINDS, UNKNOWN_CATEGORY
from finance_app.core.money import money_to_float
from finance_app.modules.categories.sources import (
    category_confidence_label,
    category_source_badge_class,
    category_source_label,
)
from finance_app.modules.merchants.normalization import normalize_merchant
from finance_app.modules.transactions.status import (
    transaction_review_status,
    transaction_status_badge_class,
    transaction_status_label,
)


def build_transaction_rows(
    rows: Iterable[Mapping[str, Any]],
    tag_map: Mapping[int, Sequence[str]],
    tag_colors: Mapping[str, str],
    conn: object,
    unknown_category: str = UNKNOWN_CATEGORY,
) -> list[dict[str, Any]]:
    """Build transaction row view models for list rendering."""
    result: list[dict[str, Any]] = []
    for row in rows:
        normalized_merchant = normalize_merchant(row["description"], conn=conn)
        merchant_key = normalized_merchant.merchant_key
        tags = list(tag_map.get(row["id"], ()))
        review_status = transaction_review_status(row, unknown_category)
        result.append(
            {
                **dict(row),
                "amount": money_to_float(row["amount"]),
                "merchant_key": merchant_key,
                "transaction_kind_label": TRANSACTION_KINDS.get(row["transaction_kind"], row["transaction_kind"]),
                "category_source_label": category_source_label(row["category_source"]),
                "category_source_badge_class": category_source_badge_class(row["category_source"]),
                "category_confidence_label": category_confidence_label(row["category_confidence"]),
                "review_status": review_status,
                "review_status_label": transaction_status_label(review_status),
                "review_status_badge_class": transaction_status_badge_class(review_status),
                "tags": tags,
                "tag_label": ", ".join(tags),
                "tag_pills": [
                    {
                        "name": tag,
                        "color": tag_colors.get(tag, "#64748b"),
                    }
                    for tag in tags
                ],
            }
        )
    return result
