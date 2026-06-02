"""Transaction categorization workflow helpers."""

from finance_app.core.constants import UNKNOWN_CATEGORY
from finance_app.core.money import optional_money_to_float
from finance_app.database.engine import db_core_transaction
from finance_app.modules.categories.decision import (
    DECISION_SOURCE_RULE,
    DECISION_SOURCE_UNKNOWN,
    HIGH_CONFIDENCE_THRESHOLD,
    MEDIUM_CONFIDENCE_THRESHOLD,
    apply_review_policy,
    combine_confidence,
    evidence_decision_source,
)
from finance_app.modules.categories.llm import classify_unknowns_with_llm
from finance_app.modules.categories.repository import (
    get_category_options,
    get_category_rules,
    normalize_category,
    resolve_category_id,
)
from finance_app.modules.categories.history import (
    retrieve_historical_decision,
)
from finance_app.modules.categories.rules_matching import (
    merchant_category_cache_key,
    score_category_rule_match,
)
from finance_app.modules.categories.sources import (
    CATEGORY_SOURCE_HISTORY,
    CATEGORY_SOURCE_RULE,
    CATEGORY_SOURCE_UNKNOWN,
    TransactionCategoryState,
    category_assignment,
)
from finance_app.modules.merchants.normalization import normalize_merchant
from finance_app.modules.settings.runtime import get_unknown_category


def categorize_transactions(transactions, conn=None, use_llm=True):
    """Categorize transactions.

    The workflow applies high-confidence rules directly, then consults
    similar previously categorized transactions before optional LLM fallback.
    Medium-confidence rule or historical decisions are assigned with review
    when they are strong enough to be useful but not strong enough to finalize.
    """
    if conn is None:
        with db_core_transaction() as owned_conn:
            return categorize_transactions(transactions, conn=owned_conn, use_llm=use_llm)

    unknown_category = get_unknown_category(conn) or UNKNOWN_CATEGORY
    category_options = get_category_options(conn)
    rules = get_category_rules(conn)
    merchant_categorizations = {}

    for tx in transactions:
        tx["amount"] = optional_money_to_float(tx.get("amount"))
        normalized_merchant = normalize_merchant(tx.get("description", ""), conn=conn)
        merchant_key = normalized_merchant.cleaned_key
        scored_rule = score_category_rule_match(
            merchant_key,
            tx.get("amount"),
            rules,
            merchant_candidate=merchant_key,
            raw_description=tx.get("description"),
            merchant_id=tx.get("merchant_id"),
            account_id=tx.get("account_id"),
            transaction_kind=tx.get("transaction_kind"),
        )
        cache_key = merchant_category_cache_key(merchant_key, tx.get("amount"), tx.get("merchant_id"))
        tx["merchant_key"] = merchant_key

        state = category_state_from_evidence(
            conn,
            tx,
            scored_rule,
            category_options,
            unknown_category,
            use_llm=use_llm,
        )
        state.apply_to(tx)

        if state.category != unknown_category:
            merchant_categorizations[cache_key] = state

    for tx in transactions:
        categorized = merchant_categorizations.get(
            merchant_category_cache_key(
                tx["merchant_key"],
                tx.get("amount"),
                tx.get("merchant_id"),
            )
        )
        if categorized:
            categorized.apply_to(tx)

    if use_llm and any(tx.get("category") == unknown_category for tx in transactions):
        classify_unknowns_with_llm(conn, transactions, rules, unknown_category)

    for tx in transactions:
        tx["category_id"] = resolve_category_id(conn, tx.get("category"))

    return transactions


def category_state_from_evidence(conn, transaction, scored_rule, category_options, unknown_category, use_llm=True):
    """Return the category state for one transaction from rule/history evidence."""
    rule_category = (
        normalize_category(scored_rule.category, category_options)
        if scored_rule is not None
        else unknown_category
    )
    rule_evidence = None
    if scored_rule is not None:
        rule_evidence = rule_evidence_payload(scored_rule, rule_category)
        transaction["rule_evidence"] = rule_evidence
    if scored_rule is not None and rule_category != unknown_category and scored_rule.confidence >= HIGH_CONFIDENCE_THRESHOLD:
        return rule_category_state(conn, scored_rule, rule_category, unknown_category, needs_review=False)

    historical = retrieve_historical_decision(conn, transaction, unknown_category)
    historical_evidence = historical_evidence_payload(historical)
    transaction["historical_evidence"] = historical_evidence
    history_category = normalize_category(historical.category, category_options) if historical.category else unknown_category
    if historical.is_medium_confidence and history_category != unknown_category:
        confidence = historical.confidence
        rule_id = None
        agreement_confidences = ()
        disagreement_confidences = ()
        if scored_rule is not None and rule_category == history_category:
            agreement_confidences = (scored_rule.confidence,)
            rule_id = rule_id_from_match(scored_rule)
        elif scored_rule is not None and rule_category != unknown_category:
            disagreement_confidences = (scored_rule.confidence,)
        confidence = combine_confidence(
            historical.confidence,
            agreement_confidences=agreement_confidences,
            disagreement_confidences=disagreement_confidences,
        )

        return historical_category_state(
            conn,
            history_category,
            historical.tags,
            unknown_category,
            confidence=confidence,
            rule_id=rule_id,
            metadata=historical_category_metadata(
                historical,
                history_category,
                historical.tags,
                confidence,
                unknown_category,
                scored_rule=scored_rule,
                rule_category=rule_category,
            ),
        )

    if scored_rule is not None and rule_category != unknown_category and scored_rule.confidence >= MEDIUM_CONFIDENCE_THRESHOLD:
        if use_llm:
            return unknown_category_state(
                conn,
                unknown_category,
                metadata=unknown_category_metadata(
                    "medium_confidence_rule_needs_llm",
                    final_category=unknown_category,
                    rule_evidence=rule_evidence,
                    historical_evidence=historical_evidence,
                ),
            )
        return rule_category_state(conn, scored_rule, rule_category, unknown_category, needs_review=True)

    return unknown_category_state(
        conn,
        unknown_category,
        metadata=unknown_category_metadata(
            "insufficient_rule_or_history_evidence",
            final_category=unknown_category,
            rule_evidence=rule_evidence,
            historical_evidence=historical_evidence,
        ),
    )


def rule_category_state(conn, scored_rule, category, unknown_category, needs_review):
    """Build a transaction category state from scored rule evidence."""
    decision = apply_review_policy(
        category,
        scored_rule.tags,
        scored_rule.confidence,
        unknown_category,
    )
    if needs_review and decision.needs_review == 0:
        decision = apply_review_policy(
            category,
            scored_rule.tags,
            min(scored_rule.confidence, HIGH_CONFIDENCE_THRESHOLD - 0.01),
            unknown_category,
        )
    return TransactionCategoryState(
        category=decision.category,
        category_id=resolve_category_id(conn, decision.category),
        needs_review=decision.needs_review,
        assignment=category_assignment(
            decision.category,
            unknown_category,
            CATEGORY_SOURCE_RULE,
            confidence=decision.confidence,
            rule_id=rule_id_from_match(scored_rule),
            metadata=rule_category_metadata(scored_rule, category, decision),
        ),
        tags=decision.tags,
    )


def historical_category_state(conn, category, tags, unknown_category, confidence, rule_id=None, metadata=None):
    """Build a transaction category state from historical transaction evidence."""
    decision = apply_review_policy(category, tags, confidence, unknown_category)
    return TransactionCategoryState(
        category=decision.category,
        category_id=resolve_category_id(conn, decision.category),
        needs_review=decision.needs_review,
        assignment=category_assignment(
            decision.category,
            unknown_category,
            CATEGORY_SOURCE_HISTORY,
            confidence=decision.confidence,
            rule_id=rule_id,
            metadata=metadata,
        ),
        tags=decision.tags,
    )


def unknown_category_state(conn, unknown_category, metadata=None):
    """Build the default unknown category state for unresolved transactions."""
    return TransactionCategoryState(
        category=unknown_category,
        category_id=resolve_category_id(conn, unknown_category),
        needs_review=1,
        assignment=category_assignment(
            unknown_category,
            unknown_category,
            CATEGORY_SOURCE_UNKNOWN,
            confidence=None,
            rule_id=None,
            metadata=metadata,
        ),
        tags=(),
    )


def rule_id_from_match(scored_rule):
    """Return the matched rule ID from scored rule evidence when available."""
    if scored_rule is None:
        return None
    return scored_rule.rule["id"] if "id" in scored_rule.rule.keys() else scored_rule.rule.get("id")


def rule_evidence_payload(scored_rule, category):
    """Return JSON-ready rule evidence for later LLM fallback prompts."""
    rule = scored_rule.rule
    return {
        "rule_id": rule_id_from_match(scored_rule),
        "keyword": rule.get("keyword"),
        "category": category,
        "tags": list(scored_rule.tags),
        "match_score": scored_rule.match_score,
        "confidence": scored_rule.confidence,
        "amount_min": optional_money_to_float(rule.get("amount_min")),
        "amount_max": optional_money_to_float(rule.get("amount_max")),
        "account_id": rule.get("account_id"),
        "direction": rule.get("direction"),
        "source": rule.get("source"),
    }


def rule_category_metadata(scored_rule, category, decision):
    """Return persisted audit metadata for a rule-only categorization."""
    return {
        "decision_source": DECISION_SOURCE_RULE,
        "final_category": decision.category,
        "final_tags": list(decision.tags),
        "final_confidence": decision.confidence,
        "proposed_category": category,
        "proposed_confidence": decision.proposed_confidence,
        "review_required": bool(decision.needs_review),
        "matched_rule_id": rule_id_from_match(scored_rule),
        "rule_confidence": scored_rule.confidence,
        "rule": rule_evidence_payload(scored_rule, category),
    }


def historical_category_metadata(historical, category, tags, confidence, unknown_category, scored_rule=None, rule_category=None):
    """Return persisted audit metadata for a historical categorization."""
    decision = apply_review_policy(category, tags, confidence, unknown_category)
    metadata = {
        "decision_source": evidence_decision_source(
            rule=scored_rule is not None,
            retrieval=True,
        ),
        "final_category": decision.category,
        "final_tags": list(decision.tags),
        "final_confidence": decision.confidence,
        "proposed_category": category,
        "proposed_confidence": decision.proposed_confidence,
        "review_required": bool(decision.needs_review),
        "retrieval_confidence": historical.confidence,
        "similar_transaction_ids": list(historical.evidence_ids),
        "retrieval": historical_evidence_payload(historical),
    }
    if scored_rule is not None:
        metadata["rule"] = rule_evidence_payload(scored_rule, rule_category)
        metadata["matched_rule_id"] = rule_id_from_match(scored_rule)
        metadata["rule_confidence"] = scored_rule.confidence
        metadata["rule_agreed_with_retrieval"] = rule_category == category
    return metadata


def unknown_category_metadata(reason, final_category=UNKNOWN_CATEGORY, rule_evidence=None, historical_evidence=None):
    """Return persisted audit metadata for unresolved automatic categorization."""
    metadata = {
        "decision_source": DECISION_SOURCE_UNKNOWN,
        "reason": reason,
        "final_category": final_category,
        "final_tags": [],
        "review_required": True,
    }
    if rule_evidence:
        metadata["rule"] = rule_evidence
        metadata["matched_rule_id"] = rule_evidence.get("rule_id")
        metadata["rule_confidence"] = rule_evidence.get("confidence")
    if historical_evidence:
        metadata["retrieval"] = historical_evidence
        metadata["retrieval_confidence"] = historical_evidence.get("confidence")
        metadata["similar_transaction_ids"] = list(historical_evidence.get("evidence_ids") or [])
    return metadata


def historical_evidence_payload(historical):
    """Return JSON-ready historical retrieval evidence for LLM prompts."""
    return {
        "category": historical.category,
        "tags": list(historical.tags),
        "confidence": historical.confidence,
        "evidence_ids": list(historical.evidence_ids),
        "examples": [
            {
                "transaction_id": candidate.transaction_id,
                "date": candidate.tx_date,
                "description": candidate.description,
                "amount": candidate.amount,
                "transaction_kind": candidate.transaction_kind,
                "category": candidate.category,
                "tags": list(candidate.tags),
                "score": candidate.score,
                "source": candidate.source,
            }
            for candidate in historical.candidates
        ],
    }


