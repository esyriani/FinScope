"""View-model builders for the transactions feature."""

from finance_app.core.constants import TRANSACTION_KINDS
from finance_app.core.money import money_to_float
from finance_app.modules.categories.sources import (
    category_confidence_label,
    category_source_badge_class,
    category_source_label,
)
from finance_app.modules.merchants.normalization import normalize_merchant


def build_transaction_rows(rows, tag_map, tag_colors, conn):
    """Build transaction rows."""
    result = []
    for row in rows:
        normalized_merchant = normalize_merchant(row["description"], conn=conn)
        merchant_key = normalized_merchant.merchant_key
        tags = tag_map.get(row["id"], [])
        result.append(
            {
                **dict(row),
                "amount": money_to_float(row["amount"]),
                "merchant_key": merchant_key,
                "transaction_kind_label": TRANSACTION_KINDS.get(row["transaction_kind"], row["transaction_kind"]),
                "category_source_label": category_source_label(row["category_source"]),
                "category_source_badge_class": category_source_badge_class(row["category_source"]),
                "category_confidence_label": category_confidence_label(row["category_confidence"]),
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
