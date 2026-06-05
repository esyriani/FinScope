"""Application orchestration for the categories feature."""

from finance_app.modules.categories import llm as _llm
from finance_app.modules.categories.categorization import categorize_transactions
from finance_app.modules.categories.llm import (
    build_llm_prompt,
    build_llm_system_prompt,
    normalize_llm_category,
    pair_llm_results,
    parse_bool,
    parse_confidence,
    request_llm_categories,
    sanitize_openai_error,
)
from finance_app.modules.categories.llm_prompts import (
    build_rule_examples,
    taxonomy_prompt_line,
)
from finance_app.modules.categories.repository import (
    clean_category_name,
    create_category,
    get_builtin_category_names,
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
    rule_match_precedence_key,
    rule_specificity,
    score_category_rule_match,
    score_category_rule_matches,
    select_winning_rule_match,
)
from finance_app.modules.merchants.normalization import normalize_merchant_description


def classify_unknowns_with_llm(
    conn,
    transactions,
    rules,
    unknown_category,
    save_automatic_rules=True,
    request_categories=None,
):
    """Classify unknowns with LLM.

    ``save_automatic_rules`` controls whether no-review AI decisions should
    also create future matching rules. ``request_categories`` can inject an LLM
    requester without replacing module globals.
    """
    return _llm.classify_unknowns_with_llm(
        conn,
        transactions,
        rules,
        unknown_category,
        save_automatic_rules=save_automatic_rules,
        request_categories=request_categories or request_llm_categories,
    )


__all__ = [
    "build_llm_prompt",
    "build_llm_system_prompt",
    "build_rule_examples",
    "categorize_transactions",
    "classify_unknowns_with_llm",
    "clean_category_name",
    "create_category",
    "fetch_category_names",
    "get_builtin_category_names",
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
    "rule_match_precedence_key",
    "rule_specificity",
    "sanitize_openai_error",
    "save_category_rule",
    "score_category_rule_match",
    "score_category_rule_matches",
    "select_winning_rule_match",
    "taxonomy_prompt_line",
]
