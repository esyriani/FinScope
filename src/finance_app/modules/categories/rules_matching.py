"""Category rule matching helpers."""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from difflib import SequenceMatcher
from typing import Any

from finance_app.core.constants import (
    CATEGORY_RULE_DIRECTION_ANY,
    CATEGORY_RULE_DIRECTION_CREDIT,
    CATEGORY_RULE_DIRECTION_DEBIT,
    CATEGORY_RULE_SOURCE_AUTOMATIC,
    CATEGORY_RULE_SOURCE_MANUAL,
)
from finance_app.core.money import MoneyValue, quantize_money, rounded_money_decimal
from finance_app.core.text import strip_accents
from finance_app.modules.categories.builtins import is_income_category_name
from finance_app.modules.merchants.normalization import normalize_merchant_description


@dataclass(frozen=True)
class ScoredRuleMatch:
    """Represent a matching category rule with deterministic confidence metadata.

    Attributes:
        rule: The matched category rule mapping returned by the repository.
        match_score: Text or merchant identity match strength between 0 and 1.
        confidence: Assignment confidence between 0 and 1 after source and
            amount-specificity adjustments.
        category: Rule category label.
        tags: Rule tag labels.
    """

    rule: dict[str, Any]
    match_score: float
    confidence: float
    category: str
    tags: tuple[str, ...]

    @property
    def specificity(self) -> tuple[int, int, int, int, int]:
        """Return the rule specificity tuple used to break equivalent matches."""
        return rule_specificity(self.rule)


HIGH_CONFIDENCE_THRESHOLD = 0.95
MEDIUM_CONFIDENCE_THRESHOLD = 0.85
FUZZY_RULE_MATCH_THRESHOLD = 0.86


def match_category_rule(
    merchant_key: object,
    amount: MoneyValue | None,
    rules: Iterable[Mapping[str, Any]],
    merchant_candidate: object | None = None,
    raw_description: object | None = None,
    merchant_id: object | None = None,
    account_id: object | None = None,
    transaction_kind: object | None = None,
) -> dict[str, Any] | None:
    """Return the deterministic rule match for a transaction, if any."""
    scored = score_category_rule_match(
        merchant_key,
        amount,
        rules,
        merchant_candidate=merchant_candidate,
        raw_description=raw_description,
        merchant_id=merchant_id,
        account_id=account_id,
        transaction_kind=transaction_kind,
        include_fuzzy=False,
    )
    return scored.rule if scored else None


def score_category_rule_match(
    merchant_key: object,
    amount: MoneyValue | None,
    rules: Iterable[Mapping[str, Any]],
    merchant_candidate: object | None = None,
    raw_description: object | None = None,
    merchant_id: object | None = None,
    account_id: object | None = None,
    transaction_kind: object | None = None,
    include_fuzzy: bool = True,
) -> ScoredRuleMatch | None:
    """Return the best matching rule with a confidence score.

    Existing deterministic matching semantics are preserved for callers of
    `match_category_rule`. New categorization workflows can opt into fuzzy
    medium-confidence evidence by leaving `include_fuzzy` enabled.
    """
    return select_winning_rule_match(
        score_category_rule_matches(
            merchant_key,
            amount,
            rules,
            merchant_candidate=merchant_candidate,
            raw_description=raw_description,
            merchant_id=merchant_id,
            account_id=account_id,
            transaction_kind=transaction_kind,
            include_fuzzy=include_fuzzy,
        )
    )


def score_category_rule_matches(
    merchant_key: object,
    amount: MoneyValue | None,
    rules: Iterable[Mapping[str, Any]],
    merchant_candidate: object | None = None,
    raw_description: object | None = None,
    merchant_id: object | None = None,
    account_id: object | None = None,
    transaction_kind: object | None = None,
    include_fuzzy: bool = True,
) -> list[ScoredRuleMatch]:
    """Return all category rules matching a transaction with deterministic scores."""
    candidates = merchant_match_candidates(
        merchant_key,
        merchant_candidate,
        raw_description=raw_description,
    )
    amount_value = rounded_money_decimal(amount) if amount is not None else None
    matches: list[ScoredRuleMatch] = []
    for rule in rules:
        if is_income_category_name(rule["category"]) and (amount_value is None or amount_value >= 0):
            continue
        if not rule_direction_matches(rule, amount, transaction_kind=transaction_kind):
            continue
        if not rule_account_matches(rule, account_id):
            continue
        if not rule_amount_matches(rule, amount):
            continue

        rule_merchant_id = rule["merchant_id"] if "merchant_id" in rule.keys() else rule.get("merchant_id")
        if rule_merchant_id is not None:
            if merchant_id is not None and int(str(merchant_id)) == int(str(rule_merchant_id)):
                matches.append(
                    scored_rule_match(
                        rule,
                        amount,
                        match_score=1.0,
                        merchant_id_matched=True,
                        account_id=account_id,
                        transaction_kind=transaction_kind,
                    )
                )
            continue

        keyword = normalize_merchant_description(rule["keyword"])
        text_score = best_rule_text_score(
            keyword,
            candidates,
            manual_rule=rule_source(rule) == CATEGORY_RULE_SOURCE_MANUAL,
            include_fuzzy=include_fuzzy,
        )
        if text_score is not None:
            matches.append(
                scored_rule_match(
                    rule,
                    amount,
                    match_score=text_score,
                    account_id=account_id,
                    transaction_kind=transaction_kind,
                )
            )

    return matches


def select_winning_rule_match(matches: Iterable[ScoredRuleMatch]) -> ScoredRuleMatch | None:
    """Return the winning match using the existing rule precedence model."""
    matches = list(matches)
    if not matches:
        return None
    return max(
        matches,
        key=rule_match_precedence_key,
    )


def rule_match_precedence_key(match: ScoredRuleMatch) -> tuple[float, float, tuple[int, int, int, int, int]]:
    """Return the precedence tuple used to select the winning rule match."""
    return (
        match.confidence,
        match.match_score,
        match.specificity,
    )


def merchant_match_candidates(
    merchant_key: object,
    merchant_candidate: object | None = None,
    raw_description: object | None = None,
) -> list[str]:
    """Build normalized and raw-text candidates for keyword matching."""
    candidates: list[str] = []
    for value in (merchant_key, merchant_candidate):
        text = normalize_merchant_description(value)
        if text and text not in candidates:
            candidates.append(text)

    for value in (raw_description_candidate(raw_description),):
        text = str(value or "").strip()
        if text and text not in candidates:
            candidates.append(text)
    return candidates


def raw_description_candidate(raw_description: object) -> str:
    """Return a lossless uppercase transaction-description match candidate."""
    text = str(raw_description or "").strip()
    if not text:
        return ""
    return " ".join(strip_accents(text).upper().split())


def scored_rule_match(
    rule: Mapping[str, Any],
    amount: MoneyValue | None,
    match_score: float,
    merchant_id_matched: bool = False,
    account_id: object | None = None,
    transaction_kind: object | None = None,
) -> ScoredRuleMatch:
    """Build a scored rule match from match quality and rule specificity."""
    confidence = rule_confidence(
        rule,
        amount,
        match_score,
        merchant_id_matched=merchant_id_matched,
        account_id=account_id,
        transaction_kind=transaction_kind,
    )
    return ScoredRuleMatch(
        rule=dict(rule),
        match_score=match_score,
        confidence=confidence,
        category=rule["category"],
        tags=tuple(rule.get("tags") or ()),
    )


def best_rule_text_score(
    keyword: object,
    candidates: Iterable[object],
    manual_rule: bool = False,
    include_fuzzy: bool = True,
) -> float | None:
    """Return the strongest keyword score for normalized merchant candidates."""
    keyword = str(keyword or "")
    if not keyword:
        return None

    best_score: float | None = None
    for raw_candidate in candidates:
        candidate = str(raw_candidate or "")
        if not candidate:
            continue
        score: float | None
        if candidate == keyword:
            score = 0.94
        elif manual_rule and is_strong_prefix_match(keyword, candidate):
            score = 0.94
        elif keyword in candidate:
            ratio = len(keyword) / max(len(candidate), 1)
            score = 0.84 + min(0.08, ratio * 0.08)
        elif include_fuzzy:
            similarity = SequenceMatcher(None, keyword, candidate).ratio()
            score = similarity * 0.88 if similarity >= FUZZY_RULE_MATCH_THRESHOLD else None
        else:
            score = None

        if score is not None:
            best_score = score if best_score is None else max(best_score, score)

    return best_score


def is_strong_prefix_match(keyword: str, candidate: str) -> bool:
    """Return whether a manual keyword is a strong full-word merchant prefix."""
    if not prefix_matches_full_word(keyword, candidate):
        return False
    return keyword_has_prefix_match_signal(keyword)


def prefix_matches_full_word(keyword: str, candidate: str) -> bool:
    """Return whether the keyword starts the candidate without splitting a token."""
    if not keyword or not candidate.startswith(keyword) or candidate == keyword:
        return False

    next_character = candidate[len(keyword)]
    return not next_character.isalnum()


def keyword_has_prefix_match_signal(keyword: str) -> bool:
    """Return whether a keyword is specific enough for prefix auto-approval."""
    tokens = [token for token in keyword.split() if token]
    return len(tokens) >= 2 or len("".join(tokens)) >= 12


def rule_confidence(
    rule: Mapping[str, Any],
    amount: MoneyValue | None,
    match_score: float,
    merchant_id_matched: bool = False,
    account_id: object | None = None,
    transaction_kind: object | None = None,
) -> float:
    """Return deterministic confidence for a matched category rule."""
    confidence = match_score

    if merchant_id_matched:
        confidence += 0.03
    if rule_account_matches(rule, account_id) and rule_account_id(rule) is not None:
        confidence += 0.04
    if rule_direction(rule) != CATEGORY_RULE_DIRECTION_ANY and rule_direction_matches(
        rule,
        amount,
        transaction_kind=transaction_kind,
    ):
        confidence += 0.04

    confidence += rule_source_adjustment(rule)
    confidence += amount_specificity_adjustment(rule, amount)

    amount_value = rounded_money_decimal(amount) if amount is not None else None
    if is_income_category_name(rule["category"]) and amount_value is not None and amount_value < 0:
        confidence += 0.06

    return max(0.0, min(1.0, round(confidence, 4)))


def rule_source_adjustment(rule: Mapping[str, Any]) -> float:
    """Return confidence adjustment for the rule's origin."""
    return {
        CATEGORY_RULE_SOURCE_MANUAL: 0.02,
        CATEGORY_RULE_SOURCE_AUTOMATIC: -0.01,
    }.get(rule_source(rule), 0.0)


def rule_source(rule: Mapping[str, Any]) -> Any:
    """Return the source value for a category rule mapping."""
    return rule["source"] if "source" in rule.keys() else rule.get("source")


def amount_specificity_adjustment(rule: Mapping[str, Any], amount: MoneyValue | None) -> float:
    """Return confidence adjustment for amount constraints on a rule."""
    amount_min = rule["amount_min"] if "amount_min" in rule.keys() else None
    amount_max = rule["amount_max"] if "amount_max" in rule.keys() else None
    if amount_min is None and amount_max is None:
        return 0.0
    if amount is None:
        return -0.05

    amount_value = abs(rounded_money_decimal(amount))
    parsed_min = rounded_money_decimal(amount_min) if amount_min is not None else None
    parsed_max = rounded_money_decimal(amount_max) if amount_max is not None else None
    if parsed_min is not None and parsed_max is not None:
        if parsed_min == parsed_max:
            return 0.08
        width = abs(parsed_max - parsed_min)
        return 0.08 if width <= max(amount_value, Decimal("10.00")) else 0.04

    return 0.03


def rule_specificity(rule: Mapping[str, Any]) -> tuple[int, int, int, int, int]:
    """Return a stable specificity score for ordering equivalent rule matches."""
    keyword = normalize_merchant_description(rule["keyword"])
    has_merchant = rule["merchant_id"] if "merchant_id" in rule.keys() else rule.get("merchant_id")
    has_account = rule_account_id(rule) is not None
    has_direction = rule_direction(rule) != CATEGORY_RULE_DIRECTION_ANY
    has_amount = (rule["amount_min"] if "amount_min" in rule.keys() else None) is not None or (
        rule["amount_max"] if "amount_max" in rule.keys() else None
    ) is not None
    return (
        1 if has_merchant else 0,
        1 if has_account else 0,
        1 if has_direction else 0,
        1 if has_amount else 0,
        len(keyword or ""),
    )


def rule_account_id(rule: Mapping[str, Any]) -> Any:
    """Return the optional account constraint for a category rule."""
    return rule["account_id"] if "account_id" in rule.keys() else rule.get("account_id")


def rule_direction(rule: Mapping[str, Any]) -> str:
    """Return the normalized direction constraint for a category rule."""
    direction = rule["direction"] if "direction" in rule.keys() else rule.get("direction")
    direction = str(direction or CATEGORY_RULE_DIRECTION_ANY).strip().lower()
    if direction in {CATEGORY_RULE_DIRECTION_ANY, CATEGORY_RULE_DIRECTION_DEBIT, CATEGORY_RULE_DIRECTION_CREDIT}:
        return direction
    return CATEGORY_RULE_DIRECTION_ANY


def rule_account_matches(rule: Mapping[str, Any], account_id: object | None) -> bool:
    """Return whether a transaction account satisfies a rule constraint."""
    rule_account = rule_account_id(rule)
    if rule_account is None:
        return True
    if account_id is None:
        return False
    return int(str(rule_account)) == int(str(account_id))


def rule_direction_matches(
    rule: Mapping[str, Any],
    amount: MoneyValue | None,
    transaction_kind: object | None = None,
) -> bool:
    """Return whether a transaction direction satisfies a rule constraint."""
    direction = rule_direction(rule)
    if direction == CATEGORY_RULE_DIRECTION_ANY:
        return True
    if amount is None:
        return False
    amount_value = rounded_money_decimal(amount)
    is_credit = amount_value < 0
    if direction == CATEGORY_RULE_DIRECTION_CREDIT:
        return is_credit
    if direction == CATEGORY_RULE_DIRECTION_DEBIT:
        return not is_credit
    return True


def rule_amount_matches(rule: Mapping[str, Any], amount: MoneyValue | None) -> bool:
    """Build amount matches."""
    amount_min = rule["amount_min"] if "amount_min" in rule.keys() else None
    amount_max = rule["amount_max"] if "amount_max" in rule.keys() else None

    if amount_min is None and amount_max is None:
        return True

    if amount is None:
        return False

    amount = rounded_money_decimal(amount)
    if amount_min is not None:
        amount_min = rounded_money_decimal(amount_min)
    if amount_max is not None:
        amount_max = rounded_money_decimal(amount_max)

    if amount_min is not None and amount < amount_min:
        return False
    if amount_max is not None and amount > amount_max:
        return False
    return True


def merchant_category_cache_key(
    merchant_key: object,
    amount: MoneyValue | None,
    merchant_id: object | None = None,
) -> tuple[Any, str | None]:
    """Build an amount-aware category cache key for equivalent transactions."""
    merchant_part = f"merchant:{merchant_id}" if merchant_id else merchant_key
    return merchant_part, amount_cache_key(amount)


def amount_cache_key(amount: MoneyValue | None) -> str | None:
    """Return a stable signed cents key for category cache comparisons."""
    try:
        normalized = quantize_money(amount)
    except (InvalidOperation, TypeError, ValueError):
        return None
    return str(normalized) if normalized is not None else None
