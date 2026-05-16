"""Application orchestration for the categories feature."""

from finance_app.modules.categories import llm as _llm
from finance_app.modules.categories.categorization import categorize_transactions
from finance_app.modules.categories.llm import (
    build_llm_prompt,
    build_llm_system_prompt,
    build_rule_examples,
    normalize_llm_category,
    pair_llm_results,
    parse_bool,
    parse_confidence,
    request_llm_categories,
    sanitize_openai_error,
    taxonomy_prompt_line,
)
from finance_app.modules.categories.repository import (
    clean_category_name,
    create_category,
    fetch_category_names,
    get_category_options,
    get_category_rules,
    normalize_category,
    rename_category,
    resolve_category_id,
    save_category_rule,
)
from finance_app.modules.categories.rules_matching import (
    match_category_rule,
    merchant_category_cache_key,
    merchant_match_candidates,
    rule_amount_matches,
    score_category_rule_match,
)
from finance_app.modules.merchants.normalization import normalize_merchant_description


def classify_unknowns_with_llm(conn, transactions, rules, unknown_category):
    """Classify unknowns with LLM."""
    original = _llm.request_llm_categories
    _llm.request_llm_categories = request_llm_categories
    try:
        return _llm.classify_unknowns_with_llm(conn, transactions, rules, unknown_category)
    finally:
        _llm.request_llm_categories = original


__all__ = [
    "build_llm_prompt",
    "build_llm_system_prompt",
    "build_rule_examples",
    "categorize_transactions",
    "classify_unknowns_with_llm",
    "clean_category_name",
    "create_category",
    "fetch_category_names",
    "get_category_options",
    "get_category_rules",
    "match_category_rule",
    "merchant_category_cache_key",
    "merchant_match_candidates",
    "normalize_category",
    "normalize_llm_category",
    "normalize_merchant_description",
    "pair_llm_results",
    "parse_bool",
    "parse_confidence",
    "rename_category",
    "request_llm_categories",
    "resolve_category_id",
    "rule_amount_matches",
    "sanitize_openai_error",
    "save_category_rule",
    "score_category_rule_match",
    "taxonomy_prompt_line",
]
