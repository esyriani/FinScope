"""Tests for category rule matching helpers."""

from decimal import Decimal

import pytest

from finance_app.modules.categories.rules_matching import (
    ScoredRuleMatch,
    amount_cache_key,
    amount_specificity_adjustment,
    best_rule_text_score,
    keyword_has_prefix_match_signal,
    match_category_rule,
    merchant_category_cache_key,
    merchant_match_candidates,
    prefix_matches_full_word,
    rule_account_matches,
    rule_amount_matches,
    rule_confidence,
    rule_direction,
    rule_direction_matches,
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


def test_rule_amount_matching_uses_decimal_cent_boundaries():
    """Verify rule bounds use fixed-scale half-up Decimal comparisons."""
    rules = [
        category_rule(
            1,
            "ROUNDING STORE",
            amount_min=Decimal("2.68"),
            amount_max=Decimal("2.68"),
        )
    ]

    winner = match_category_rule("ROUNDING STORE", Decimal("2.675"), rules)

    assert winner["id"] == 1
    assert match_category_rule("ROUNDING STORE", Decimal("2.674"), rules) is None


def test_score_category_rule_matches_returns_all_matches_and_preserves_winner():
    """Verify all matching rules are exposed while the selected winner is unchanged."""
    rules = [
        category_rule(1, "METRO", tags=["Tax"]),
        category_rule(2, "METRO GROCERY", amount_min=10, amount_max=20, tags=["Shared"]),
    ]

    matches = score_category_rule_matches("METRO GROCERY", 12.34, rules)
    winner = score_category_rule_match("METRO GROCERY", 12.34, rules)
    matched_rule = match_category_rule("METRO GROCERY", 12.34, rules)

    assert [match.rule["id"] for match in matches] == [1, 2]
    assert matches[0].category == "Food"
    assert matches[1].tags == ("Shared",)
    assert winner.rule["id"] == 2
    assert matched_rule["id"] == 2
    assert winner.specificity == rule_specificity(winner.rule)


def test_score_category_rule_match_checks_raw_transaction_description():
    """Verify keyword rules can match raw descriptors before cleanup strips tokens."""
    rules = [category_rule(1, "COSMETA", tags=["Tax"])]

    winner = score_category_rule_match(
        "SQ",
        74.73,
        rules,
        merchant_candidate="SQ",
        raw_description="SQ *COSMETA",
    )

    assert winner.rule["id"] == 1
    assert winner.tags == ("Tax",)


def test_merchant_match_candidates_deduplicates_normalized_and_raw_candidates():
    """Verify matcher candidates keep distinct normalized and raw descriptions."""
    assert merchant_match_candidates(
        "SQ",
        "SQ",
        raw_description="SQ *COSMETA",
    ) == ["SQ", "SQ *COSMETA"]


def test_best_rule_text_score_covers_exact_containment_fuzzy_and_empty_candidates():
    """Verify text scoring semantics used by rule precedence."""
    containment = best_rule_text_score("BAR", ["", "FOO BAR"], manual_rule=False)
    fuzzy = best_rule_text_score("COSTCO", ["COSCO"], manual_rule=False, include_fuzzy=True)

    assert best_rule_text_score("METRO", ["", "METRO"]) == 0.94
    assert containment == pytest.approx(0.84 + (3 / 7) * 0.08)
    assert fuzzy == pytest.approx(0.88 * (10 / 11))
    assert best_rule_text_score("COSTCO", ["COSCO"], manual_rule=False, include_fuzzy=False) is None
    assert best_rule_text_score("", ["METRO"]) is None


@pytest.mark.parametrize(
    ("keyword", "candidate", "expected"),
    [
        ("WAL", "WALMART", False),
        ("WAL MART", "WAL MART STORE", True),
        ("COSTCO", "COSTCO", False),
        ("", "ANYTHING", False),
    ],
)
def test_prefix_matches_full_word_boundaries(keyword, candidate, expected):
    """Verify prefix matches do not split words or exact-match the candidate."""
    assert prefix_matches_full_word(keyword, candidate) is expected


@pytest.mark.parametrize(
    ("keyword", "expected"),
    [
        ("COSTCO", False),
        ("HOME DEPOT", True),
        ("ABCDEFGHIJK", False),
        ("ABCDEFGHIJKL", True),
    ],
)
def test_keyword_has_prefix_match_signal_boundaries(keyword, expected):
    """Verify prefix auto-approval requires a multi-token or long keyword."""
    assert keyword_has_prefix_match_signal(keyword) is expected


def test_manual_full_word_prefix_match_scores_like_exact_match():
    """Verify strong manual prefix matches can auto-apply location suffixes."""
    rules = [category_rule(1, "COSTCO WHOLESALE", tags=["Grocery"])]

    winner = score_category_rule_match(
        "COSTCO WHOLESALE W527 MONTREAL QC",
        277.72,
        rules,
    )

    assert winner.rule["id"] == 1
    assert winner.match_score == 0.94
    assert winner.confidence >= 0.95


def test_non_prefix_contains_match_stays_below_auto_apply_confidence():
    """Verify containment away from the merchant prefix remains review-worthy."""
    rules = [category_rule(1, "WHOLESALE")]

    winner = score_category_rule_match(
        "COSTCO WHOLESALE W527 MONTREAL QC",
        277.72,
        rules,
    )

    assert winner.rule["id"] == 1
    assert winner.match_score < 0.94
    assert winner.confidence < 0.95


def test_income_rules_require_credit_amounts():
    """Verify income category rules do not match missing or debit amounts."""
    rules = [category_rule(1, "PAYROLL", category="Income")]

    assert score_category_rule_match("PAYROLL", None, rules) is None
    assert score_category_rule_match("PAYROLL", Decimal("1000.00"), rules) is None
    assert score_category_rule_match("PAYROLL", Decimal("-1000.00"), rules).rule["id"] == 1


def test_merchant_id_rules_only_match_the_same_merchant_id():
    """Verify merchant-scoped rules do not fall back to keyword matching."""
    rules = [category_rule(1, "UNRELATED KEYWORD", merchant_id=7)]

    assert score_category_rule_match("METRO", 12.34, rules, merchant_id=None) is None
    assert score_category_rule_match("METRO", 12.34, rules, merchant_id=8) is None
    winner = score_category_rule_match("METRO", 12.34, rules, merchant_id=7)
    assert winner.rule["id"] == 1
    assert winner.match_score == 1.0


def test_rule_filters_apply_account_direction_and_amount_before_matching():
    """Verify scoped rules must satisfy account, direction, and amount filters."""
    rule = category_rule(
        1,
        "METRO",
        account_id=5,
        direction="debit",
        amount_min=Decimal("10.00"),
        amount_max=Decimal("20.00"),
    )

    assert score_category_rule_match("METRO", Decimal("12.00"), [rule], account_id=5).rule["id"] == 1
    assert score_category_rule_match("METRO", Decimal("12.00"), [rule], account_id=6) is None
    assert score_category_rule_match("METRO", Decimal("-12.00"), [rule], account_id=5) is None
    assert score_category_rule_match("METRO", Decimal("21.00"), [rule], account_id=5) is None


@pytest.mark.parametrize(
    ("rule", "amount", "expected"),
    [
        (category_rule(1, "METRO"), Decimal("12.00"), 0.0),
        (category_rule(1, "METRO", amount_min=10), None, -0.05),
        (category_rule(1, "METRO", amount_min=10), Decimal("12.00"), 0.03),
        (category_rule(1, "METRO", amount_min=12, amount_max=12), Decimal("12.00"), 0.08),
        (category_rule(1, "METRO", amount_min=10, amount_max=20), Decimal("12.00"), 0.08),
        (category_rule(1, "METRO", amount_min=10, amount_max=40), Decimal("12.00"), 0.04),
    ],
)
def test_amount_specificity_adjustment_boundaries(rule, amount, expected):
    """Verify amount-scoped confidence adjustments."""
    assert amount_specificity_adjustment(rule, amount) == expected


def test_rule_confidence_combines_scope_source_income_and_clamp_adjustments():
    """Verify confidence adjustments compose and remain clamped to 0..1."""
    scoped = category_rule(1, "METRO", account_id=5, direction="debit")
    automatic = category_rule(2, "METRO", source="automatic")
    income = category_rule(3, "PAYROLL", category="Income")

    assert rule_confidence(scoped, Decimal("12.00"), 0.85, merchant_id_matched=True, account_id=5) == 0.98
    assert rule_confidence(automatic, Decimal("12.00"), 0.94) == 0.93
    assert rule_confidence(income, Decimal("-1000.00"), 0.94) == 1.0
    assert rule_confidence(category_rule(4, "METRO"), Decimal("12.00"), -0.50) == 0.0


def test_rule_specificity_reports_each_scope_dimension():
    """Verify specificity exposes merchant, account, direction, amount, and keyword length."""
    rule = category_rule(
        1,
        "Home Depot",
        merchant_id=9,
        account_id=5,
        direction="debit",
        amount_min=Decimal("10.00"),
    )

    assert rule_specificity(rule) == (1, 1, 1, 1, len("HOME DEPOT"))


def test_rule_account_direction_amount_helpers_cover_edge_cases():
    """Verify low-level rule predicates stay inclusive and portable."""
    account_rule = category_rule(1, "METRO", account_id=5)
    credit_rule = category_rule(2, "PAYROLL", direction="credit")
    debit_rule = category_rule(3, "METRO", direction="debit")
    invalid_direction_rule = category_rule(4, "METRO", direction="sideways")
    amount_rule = category_rule(5, "METRO", amount_min=10, amount_max=20)

    assert rule_account_matches(account_rule, 5) is True
    assert rule_account_matches(account_rule, None) is False
    assert rule_account_matches(account_rule, 6) is False
    assert rule_direction(invalid_direction_rule) == "any"
    assert rule_direction_matches(credit_rule, Decimal("-1.00")) is True
    assert rule_direction_matches(credit_rule, Decimal("1.00")) is False
    assert rule_direction_matches(debit_rule, Decimal("1.00")) is True
    assert rule_direction_matches(debit_rule, None) is False
    assert rule_amount_matches(amount_rule, Decimal("10.00")) is True
    assert rule_amount_matches(amount_rule, Decimal("20.00")) is True
    assert rule_amount_matches(amount_rule, Decimal("9.99")) is False
    assert rule_amount_matches(amount_rule, Decimal("20.01")) is False
    assert rule_amount_matches(amount_rule, None) is False


@pytest.mark.parametrize(
    ("amount", "expected"),
    [
        (Decimal("12.345"), "12.35"),
        (None, None),
        ("", None),
        ("not-money", None),
    ],
)
def test_amount_cache_key_handles_rounded_missing_and_invalid_values(amount, expected):
    """Verify amount cache keys are stable and tolerate invalid inputs."""
    assert amount_cache_key(amount) == expected


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
