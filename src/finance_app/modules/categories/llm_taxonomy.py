"""LLM candidate taxonomy helpers.

Selects compact category and tag hints for LLM categorization prompts using
rule evidence, historical evidence, merchant history, and taxonomy text.
"""

import re
from collections.abc import Mapping, MutableMapping, Sequence
from typing import Any

from sqlalchemy import func, select

from finance_app.core.constants import UNKNOWN_CATEGORY
from finance_app.database.tables import (
    tags as tags_table,
)
from finance_app.database.tables import (
    transaction_tags as transaction_tags_table,
)
from finance_app.database.tables import (
    transactions as transactions_table,
)
from finance_app.modules.categories.repository import normalize_category
from finance_app.modules.categories.taxonomy import normalize_tag_names
from finance_app.modules.merchants.normalization import normalize_merchant_description

COMMON_CATEGORY_LIMIT = 6
MAX_CANDIDATE_CATEGORIES = 20
MAX_CANDIDATE_TAGS = 16
MIN_SEMANTIC_TOKEN_LENGTH = 3
SEMANTIC_STOPWORDS = frozenset(
    {
        "AND",
        "THE",
        "FOR",
        "WITH",
        "FROM",
        "THIS",
        "THAT",
        "WHEN",
        "USE",
        "NOT",
        "OTHER",
        "ORDINARY",
        "TRANSACTION",
        "TRANSACTIONS",
        "CATEGORY",
        "CATEGORIES",
        "DESCRIPTION",
        "DESCRIPTIONS",
        "PAYMENT",
        "PURCHASE",
    }
)


def prepare_llm_candidate_taxonomies(
    conn: Any,
    unknown_items: Sequence[MutableMapping[str, Any]],
    category_options: Sequence[str],
    tag_options: Sequence[str],
    unknown_category: str,
    category_rows: Sequence[Mapping[str, Any]] | None = None,
    tag_rows: Sequence[Mapping[str, Any]] | None = None,
) -> None:
    """Attach compact candidate category and tag lists to LLM transaction payloads.

    Candidate taxonomies are intentionally transaction-local. They prioritize
    rule evidence, retrieved historical examples, merchant history, semantic
    taxonomy matches, and common local categories. They are prompt hints rather
    than acceptance gates.
    """
    category_rows = category_rows or []
    tag_rows = tag_rows or []
    common_categories = common_category_names(conn, category_options, unknown_category)
    for tx in unknown_items:
        categories: list[Any] = []
        categories.extend(rule_evidence_categories(tx))
        categories.extend(historical_evidence_categories(tx))
        categories.extend(merchant_history_category_names(conn, tx, category_options, unknown_category))
        categories.extend(semantic_taxonomy_names(tx, category_rows, category_options, unknown_category))
        categories.extend(common_categories)
        categories.append(unknown_category)

        candidate_categories = compact_category_candidates(
            categories,
            category_options,
            unknown_category,
        )
        tx["llm_candidate_categories"] = candidate_categories
        tx["llm_candidate_tags"] = compact_tag_candidates(
            conn,
            tx,
            candidate_categories,
            tag_options,
            tag_rows,
        )


def rule_evidence_categories(transaction: Mapping[str, Any]) -> list[Any]:
    """Return category candidates from matched rule evidence."""
    evidence = transaction.get("rule_evidence") or {}
    return [evidence.get("category")]


def historical_evidence_categories(transaction: Mapping[str, Any]) -> list[Any]:
    """Return category candidates from historical retrieval evidence."""
    evidence = transaction.get("historical_evidence") or {}
    categories = [evidence.get("category")]
    categories.extend(example.get("category") for example in evidence.get("examples") or [])
    return categories


def semantic_taxonomy_names(
    transaction: Mapping[str, Any],
    taxonomy_rows: Sequence[Mapping[str, Any]],
    taxonomy_options: Sequence[str],
    unknown_category: str | None = None,
) -> list[str]:
    """Return taxonomy names whose instructions overlap the merchant text."""
    query_tokens = semantic_tokens(
        " ".join(
            str(value or "")
            for value in (
                transaction.get("merchant_key"),
                transaction.get("description"),
            )
        )
    )
    if not query_tokens:
        return []

    option_order = {name: index for index, name in enumerate(taxonomy_options)}
    rows_by_name = {row["name"]: row for row in taxonomy_rows}
    scored: list[tuple[int, int, str]] = []
    for name in taxonomy_options:
        if unknown_category is not None and name == unknown_category:
            continue
        row = rows_by_name.get(name, {})
        taxonomy_tokens = semantic_tokens(
            " ".join(
                str(value or "")
                for value in (
                    name,
                    row.get("description"),
                    row.get("instruction"),
                )
            )
        )
        overlap = query_tokens & taxonomy_tokens
        if overlap:
            scored.append((len(overlap), option_order.get(name, 0), name))

    scored.sort(key=lambda item: (-item[0], item[1]))
    return [name for _, _, name in scored]


def semantic_tokens(value: object) -> set[str]:
    """Return meaningful normalized tokens for lightweight taxonomy matching."""
    normalized = normalize_merchant_description(value)
    return {
        token
        for token in re.findall(r"[A-Z0-9]+", normalized)
        if len(token) >= MIN_SEMANTIC_TOKEN_LENGTH and token not in SEMANTIC_STOPWORDS
    }


def common_category_names(conn: Any, category_options: Sequence[str], unknown_category: str) -> list[str]:
    """Return commonly used non-unknown categories from persisted transactions."""
    rows = (
        conn.execute(
            select(
                transactions_table.c.category,
                func.count().label("count"),
            )
            .where(
                transactions_table.c.ignored == 0,
                transactions_table.c.category.is_not(None),
                transactions_table.c.category != unknown_category,
                transactions_table.c.category.in_(category_options),
            )
            .group_by(transactions_table.c.category)
            .order_by(func.count().desc(), transactions_table.c.category)
            .limit(COMMON_CATEGORY_LIMIT)
        )
        .mappings()
        .fetchall()
    )
    return [row["category"] for row in rows]


def merchant_history_category_names(
    conn: Any,
    transaction: Mapping[str, Any],
    category_options: Sequence[str],
    unknown_category: str,
) -> list[str]:
    """Return categories historically used for the same durable merchant."""
    merchant_id = transaction.get("merchant_id")
    if merchant_id is None:
        return []

    rows = (
        conn.execute(
            select(
                transactions_table.c.category,
                func.count().label("count"),
            )
            .where(
                transactions_table.c.ignored == 0,
                transactions_table.c.merchant_id == int(merchant_id),
                transactions_table.c.category.is_not(None),
                transactions_table.c.category != unknown_category,
                transactions_table.c.category.in_(category_options),
            )
            .group_by(transactions_table.c.category)
            .order_by(func.count().desc(), transactions_table.c.category)
            .limit(COMMON_CATEGORY_LIMIT)
        )
        .mappings()
        .fetchall()
    )
    return [row["category"] for row in rows]


def compact_category_candidates(
    categories: Sequence[Any],
    category_options: Sequence[str],
    unknown_category: str,
) -> list[str]:
    """Return compact, valid category candidates with an unknown fallback."""
    candidates: list[str] = []
    seen: set[str] = set()
    for category in categories:
        normalized = normalize_candidate_category(category, category_options, unknown_category)
        if not normalized or normalized in seen:
            continue
        if normalized == unknown_category:
            continue
        candidates.append(normalized)
        seen.add(normalized)

    if len(category_options) <= MAX_CANDIDATE_CATEGORIES:
        for category in category_options:
            if category == unknown_category or category in seen:
                continue
            candidates.append(category)
            seen.add(category)

    if unknown_category in category_options and unknown_category not in seen:
        candidates.append(unknown_category)
        seen.add(unknown_category)

    if len(category_options) <= MAX_CANDIDATE_CATEGORIES:
        return candidates

    concrete = [category for category in candidates if category != unknown_category]
    if not concrete:
        return list(category_options)

    limited = concrete[: max(1, MAX_CANDIDATE_CATEGORIES - 1)]
    if unknown_category in seen:
        limited.append(unknown_category)
    return limited


def normalize_candidate_category(
    category: object, category_options: Sequence[str], unknown_category: str
) -> str | None:
    """Normalize a candidate category against the active taxonomy."""
    text = str(category or "").strip()
    if not text:
        return None
    if text == unknown_category or text.upper() == UNKNOWN_CATEGORY:
        return unknown_category if unknown_category in category_options else None
    normalized = normalize_category(text, category_options)
    return normalized if normalized in category_options else None


def compact_tag_candidates(
    conn: Any,
    transaction: Mapping[str, Any],
    candidate_categories: Sequence[str],
    tag_options: Sequence[str],
    tag_rows: Sequence[Mapping[str, Any]] | None = None,
) -> list[str]:
    """Return compact, valid tag candidates for one LLM transaction."""
    tags: list[Any] = []
    evidence = transaction.get("rule_evidence") or {}
    tags.extend(evidence.get("tags") or [])
    historical = transaction.get("historical_evidence") or {}
    tags.extend(historical.get("tags") or [])
    for example in historical.get("examples") or []:
        tags.extend(example.get("tags") or [])
    tags.extend(semantic_taxonomy_names(transaction, tag_rows or [], tag_options))
    tags.extend(tags_for_candidate_categories(conn, candidate_categories, tag_options))

    normalized = normalize_tag_names(tags, tag_options)
    if normalized:
        return normalized[:MAX_CANDIDATE_TAGS]

    return common_tag_names(conn, tag_options)


def tags_for_candidate_categories(
    conn: Any,
    candidate_categories: Sequence[str],
    tag_options: Sequence[str],
) -> list[str]:
    """Return tags commonly associated with candidate categories."""
    concrete_categories = [category for category in candidate_categories if category != UNKNOWN_CATEGORY]
    if not concrete_categories or not tag_options:
        return []

    rows = (
        conn.execute(
            select(
                tags_table.c.name,
                func.count().label("count"),
            )
            .select_from(
                transaction_tags_table.join(tags_table, tags_table.c.id == transaction_tags_table.c.tag_id).join(
                    transactions_table, transactions_table.c.id == transaction_tags_table.c.transaction_id
                )
            )
            .where(
                transactions_table.c.category.in_(concrete_categories),
                tags_table.c.name.in_(tag_options),
            )
            .group_by(tags_table.c.name)
            .order_by(func.count().desc(), tags_table.c.name)
            .limit(MAX_CANDIDATE_TAGS)
        )
        .mappings()
        .fetchall()
    )
    return [row["name"] for row in rows]


def common_tag_names(conn: Any, tag_options: Sequence[str]) -> list[str]:
    """Return commonly used tags as a compact fallback."""
    if not tag_options:
        return []

    rows = (
        conn.execute(
            select(
                tags_table.c.name,
                func.count().label("count"),
            )
            .select_from(transaction_tags_table.join(tags_table, tags_table.c.id == transaction_tags_table.c.tag_id))
            .where(tags_table.c.name.in_(tag_options))
            .group_by(tags_table.c.name)
            .order_by(func.count().desc(), tags_table.c.name)
            .limit(MAX_CANDIDATE_TAGS)
        )
        .mappings()
        .fetchall()
    )
    names = [row["name"] for row in rows]
    return names or list(tag_options[:MAX_CANDIDATE_TAGS])


def taxonomy_rows_for_names(rows: Sequence[Mapping[str, Any]], names: Sequence[str]) -> list[Mapping[str, Any]]:
    """Return taxonomy rows ordered by a compact name list."""
    rows_by_name = {row["name"]: row for row in rows}
    return [rows_by_name.get(name, {"id": None, "name": name, "description": "", "instruction": ""}) for name in names]


def taxonomy_ids_for_names(rows: Sequence[Mapping[str, Any]], names: Sequence[str]) -> list[int]:
    """Return taxonomy row IDs ordered by a compact name list."""
    return [row["id"] for row in taxonomy_rows_for_names(rows, names) if row.get("id") is not None]
