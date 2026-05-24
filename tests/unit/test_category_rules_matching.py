"""Tests for category rule matching helpers."""

from finance_app.modules.categories.rules_matching import (
    ScoredRuleMatch,
    match_category_rule,
    merchant_category_cache_key,
    rule_specificity,
    score_category_rule_match,
    score_category_rule_matches,
    select_winning_rule_match,
)


def category_rule(
    rule_id,
    keyword,
    category="Food",
    amount_min=None,
    amount_max=None,
    tags=None,
    merchant_id=None,
    account_id=None,
    direction="any",
    source="manual",
):
    """Build a category-rule mapping for matcher tests."""
    return {
        "id": rule_id,
        "merchant_id": merchant_id,
        "account_id": account_id,
        "keyword": keyword,
        "category": category,
        "amount_min": amount_min,
        "amount_max": amount_max,
        "direction": direction,
        "source": source,
        "tags": tags or [],
    }


def test_merchant_category_cache_key_includes_signed_amount():
    """Verify category cache keys only collapse exact signed amount matches."""
    assert merchant_category_cache_key("METRO", 12.345) == ("METRO", "12.35")
    assert merchant_category_cache_key("METRO", 12.34) != merchant_category_cache_key(
        "METRO",
        30.00,
    )
    assert merchant_category_cache_key("METRO", 12.34) != merchant_category_cache_key(
        "METRO",
        -12.34,
    )
    assert merchant_category_cache_key("METRO", 12.34, merchant_id=7) == ("merchant:7", "12.34")


def test_score_category_rule_matches_returns_all_matches_and_preserves_winner():
    """Verify all matching rules are exposed while the legacy winner is unchanged."""
    rules = [
        category_rule(1, "METRO", tags=["Tax"]),
        category_rule(2, "METRO GROCERY", amount_min=10, amount_max=20, tags=["Shared"]),
    ]

    matches = score_category_rule_matches("METRO GROCERY", 12.34, rules)
    winner = score_category_rule_match("METRO GROCERY", 12.34, rules)
    legacy_rule = match_category_rule("METRO GROCERY", 12.34, rules)

    assert [match.rule["id"] for match in matches] == [1, 2]
    assert matches[0].category == "Food"
    assert matches[1].tags == ("Shared",)
    assert winner.rule["id"] == 2
    assert legacy_rule["id"] == 2
    assert winner.specificity == rule_specificity(winner.rule)


def test_select_winning_rule_match_uses_confidence_then_match_score_then_specificity():
    """Verify winner selection follows the production precedence tuple."""
    broad = ScoredRuleMatch(
        rule=category_rule(1, "METRO"),
        match_score=0.90,
        confidence=0.90,
        category="Food",
        tags=(),
    )
    higher_confidence = ScoredRuleMatch(
        rule=category_rule(2, "METRO"),
        match_score=0.80,
        confidence=0.91,
        category="Food",
        tags=(),
    )
    higher_match_score = ScoredRuleMatch(
        rule=category_rule(3, "METRO"),
        match_score=0.91,
        confidence=0.90,
        category="Food",
        tags=(),
    )
    more_specific = ScoredRuleMatch(
        rule=category_rule(4, "METRO", amount_min=10, amount_max=10),
        match_score=0.90,
        confidence=0.90,
        category="Food",
        tags=(),
    )

    assert select_winning_rule_match([broad, higher_confidence]) == higher_confidence
    assert select_winning_rule_match([broad, higher_match_score]) == higher_match_score
    assert select_winning_rule_match([broad, more_specific]) == more_specific


def test_select_winning_rule_match_keeps_first_match_on_complete_tie():
    """Verify equivalent matches keep the first rule, matching Python max behavior."""
    first = ScoredRuleMatch(
        rule=category_rule(1, "METRO"),
        match_score=0.90,
        confidence=0.90,
        category="Food",
        tags=(),
    )
    second = ScoredRuleMatch(
        rule=category_rule(2, "METRO"),
        match_score=0.90,
        confidence=0.90,
        category="Food",
        tags=(),
    )

    assert select_winning_rule_match([first, second]) == first
