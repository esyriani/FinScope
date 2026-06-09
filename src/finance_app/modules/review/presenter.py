"""View-model builders for the review feature."""

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from finance_app.core.money import money_to_float
from finance_app.modules.categories.sources import (
    category_confidence_label,
    category_source_badge_class,
    category_source_label,
)
from finance_app.modules.review.normalization import review_merchant_key
from finance_app.modules.review.urls import build_review_url


def build_review_groups(
    rows: Iterable[Mapping[str, Any]],
    transaction_tags: Mapping[int, Iterable[str]],
    unknown_category: str,
) -> list[dict[str, Any]]:
    """Build review groups from candidate rows and tag mappings."""
    groups_by_key: dict[str, dict[str, Any]] = {}

    for row in rows:
        key = review_merchant_key(row["description"])
        if not key:
            continue

        group = groups_by_key.setdefault(
            key,
            {
                "merchant_key": key,
                "count": 0,
                "unknown_count": 0,
                "review_count": 0,
                "total_amount": 0,
                "absolute_amount": 0,
                "first_date": row["tx_date"],
                "last_date": row["tx_date"],
                "categories": set(),
                "category_sources": set(),
                "category_source_badges": set(),
                "examples": [],
                "transactions": [],
            },
        )
        row_dict = review_transaction_row(row, transaction_tags.get(row["id"], []))
        group["count"] += 1
        group["review_count"] += 1 if row["needs_review"] else 0
        group["unknown_count"] += 1 if is_unknown_category(row["category"], unknown_category) else 0
        amount = money_to_float(row["amount"])
        group["total_amount"] += amount
        group["absolute_amount"] += abs(amount)
        group["first_date"] = min(group["first_date"], row["tx_date"])
        group["last_date"] = max(group["last_date"], row["tx_date"])
        group["categories"].add(row["category"] or unknown_category)
        group["category_sources"].add(row_dict["category_source_label"])
        group["category_source_badges"].add(
            (row_dict["category_source_label"], row_dict["category_source_badge_class"])
        )
        group["transactions"].append(row_dict)

        if len(group["examples"]) < 3:
            group["examples"].append(row_dict)

    groups = []
    for group in groups_by_key.values():
        group["selected_category"] = selected_review_category(group["categories"], unknown_category)
        group["selected_tags"] = selected_review_tags(group["transactions"])
        group["categories"] = sorted(group["categories"], key=str.casefold)
        group["category_sources"] = sorted(group["category_sources"], key=str.casefold)
        group["category_source_badges"] = [
            {
                "label": label,
                "class": badge_class,
            }
            for label, badge_class in sorted(group["category_source_badges"], key=lambda item: item[0].casefold())
        ]
        groups.append(group)

    groups.sort(
        key=lambda group: (
            group["count"],
            group["absolute_amount"],
            group["last_date"],
            group["merchant_key"],
        ),
        reverse=True,
    )
    return groups


def selected_review_category(categories: Iterable[str], unknown_category: str) -> str:
    """Return the category that should prefill the review modal."""
    known_categories = {category for category in categories if not is_unknown_category(category, unknown_category)}
    if len(known_categories) == 1:
        return next(iter(known_categories))
    return unknown_category


def selected_review_tags(transactions: list[Mapping[str, Any]]) -> list[str]:
    """Return tags that should prefill the review modal.

    A grouped review action applies the selected tags to every transaction in
    the group, so only tags shared by all transactions are preselected.
    """
    if not transactions:
        return []

    common_tags = set(transactions[0].get("tags", []))
    for tx in transactions[1:]:
        common_tags &= set(tx.get("tags", []))

    return [tag for tag in transactions[0].get("tags", []) if tag in common_tags]


def review_display_rows(
    groups: Iterable[dict[str, Any]],
    ungrouped_keys: Iterable[str],
    unknown_category: str,
) -> list[dict[str, Any]]:
    """Render display rows."""
    ungrouped = set(ungrouped_keys)
    rows: list[dict[str, Any]] = []

    for group in groups:
        if group["merchant_key"] in ungrouped and group["count"] > 1:
            rows.extend(review_transaction_display_row(group, tx, unknown_category) for tx in group["transactions"])
            continue

        rows.append(review_group_display_row(group))

    return rows


def sort_review_groups(groups: list[dict[str, Any]], sort: str, direction: str) -> None:
    """Sort review groups."""
    if sort == "merchant":
        groups.sort(key=review_group_default_sort_key)
        groups.sort(
            key=lambda group: sortable_text(group["merchant_key"]),
            reverse=direction == "desc",
        )
        return

    groups.sort(key=lambda group: sortable_text(group["merchant_key"]))
    groups.sort(
        key=lambda group: group["count"],
        reverse=direction == "desc",
    )


def review_group_default_sort_key(group: Mapping[str, Any]) -> tuple[int, float, str, str]:
    """Render group default sort key."""
    return (
        -group["count"],
        -group["absolute_amount"],
        str(group["last_date"] or ""),
        sortable_text(group["merchant_key"]),
    )


def sortable_text(value: object) -> str:
    """Return sortable text."""
    return str(value or "").casefold()


def review_transaction_row(row: Mapping[str, Any], tags: Iterable[str] | None = None) -> dict[str, Any]:
    """Return a review transaction row with category source display fields."""
    row_dict = dict(row)
    row_dict["amount"] = money_to_float(row["amount"])
    row_dict.update(
        {
            "category_source_label": category_source_label(row["category_source"]),
            "category_source_badge_class": category_source_badge_class(row["category_source"]),
            "category_confidence_label": category_confidence_label(row["category_confidence"]),
            "tags": list(tags or []),
        }
    )
    return row_dict


def review_group_display_row(group: Mapping[str, Any]) -> dict[str, Any]:
    """Render group display row."""
    row = {key: value for key, value in group.items()}
    row.update(
        {
            "is_ungrouped": False,
            "transaction_id": None,
            "display_label": group["merchant_key"],
            "rule_keyword": group["merchant_key"],
            "rule_save_default": "1",
            "default_amount_min": None,
            "default_amount_max": None,
            "modal_title": "Review group",
            "submit_label": "Categorize group",
        }
    )
    return row


def review_transaction_display_row(
    group: Mapping[str, Any],
    tx: Mapping[str, Any],
    unknown_category: str,
) -> dict[str, Any]:
    """Render transaction display row."""
    category = tx["category"] or unknown_category
    amount = tx["amount"]
    return {
        "merchant_key": group["merchant_key"],
        "count": 1,
        "unknown_count": 1 if is_unknown_category(tx["category"], unknown_category) else 0,
        "review_count": 1 if tx["needs_review"] else 0,
        "total_amount": amount,
        "absolute_amount": abs(amount),
        "first_date": tx["tx_date"],
        "last_date": tx["tx_date"],
        "categories": [category],
        "category_sources": [tx["category_source_label"]],
        "category_source_badges": [
            {
                "label": tx["category_source_label"],
                "class": tx["category_source_badge_class"],
            }
        ],
        "selected_category": category,
        "selected_tags": list(tx.get("tags", [])),
        "examples": [tx],
        "transactions": [tx],
        "is_ungrouped": True,
        "transaction_id": tx["id"],
        "display_label": tx["description"],
        "rule_keyword": group["merchant_key"],
        "rule_save_default": "0",
        "default_amount_min": amount,
        "default_amount_max": amount,
        "modal_title": "Review transaction",
        "submit_label": "Categorize transaction",
    }


def attach_review_row_urls(
    rows: Iterable[dict[str, Any]],
    page: int,
    ungrouped_keys: Iterable[str],
    sort: str,
    direction: str,
    merchant_search: str = "",
) -> None:
    """Attach review row URLs."""
    for row in rows:
        if row["is_ungrouped"]:
            row["regroup_url"] = build_review_url(
                page,
                without_ungroup_key(ungrouped_keys, row["merchant_key"]),
                sort,
                direction,
                merchant_search,
            )
            row["ungroup_url"] = ""
        else:
            row["regroup_url"] = ""
            row["ungroup_url"] = (
                build_review_url(
                    page,
                    with_ungroup_key(ungrouped_keys, row["merchant_key"]),
                    sort,
                    direction,
                    merchant_search,
                )
                if row["count"] > 1
                else ""
            )


def selected_ungroup_keys(values: Iterable[object]) -> list[str]:
    """Handle selected ungroup keys."""
    keys: list[str] = []
    seen: set[str] = set()

    for value in values:
        key = review_merchant_key(value)
        if key and key not in seen:
            keys.append(key)
            seen.add(key)

    return keys


def active_ungroup_keys(ungrouped_keys: Iterable[str], groups: Iterable[Mapping[str, Any]]) -> list[str]:
    """Handle active ungroup keys."""
    splitable_keys = {group["merchant_key"] for group in groups if group["count"] > 1}
    return [key for key in ungrouped_keys if key in splitable_keys]


def with_ungroup_key(ungrouped_keys: Iterable[str], merchant_key: str) -> list[str]:
    """Return ungroup key."""
    if merchant_key in ungrouped_keys:
        return list(ungrouped_keys)
    return list(ungrouped_keys) + [merchant_key]


def without_ungroup_key(ungrouped_keys: Iterable[str], merchant_key: str) -> list[str]:
    """Return ungroup key."""
    return [key for key in ungrouped_keys if key != merchant_key]


def review_summary(groups: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Render summary."""
    largest_group = groups[0] if groups else None

    return {
        "group_count": len(groups),
        "transaction_count": sum(group["count"] for group in groups),
        "largest_group_count": largest_group["count"] if largest_group else 0,
        "largest_group_key": largest_group["merchant_key"] if largest_group else "",
        "review_amount": sum(group["absolute_amount"] for group in groups),
    }


def is_unknown_category(category: object, unknown_category: str) -> bool:
    """Return whether unknown category."""
    return category is None or category == unknown_category


def short_label(value: object, limit: int = 48) -> str:
    """Return a shortened label."""
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."
