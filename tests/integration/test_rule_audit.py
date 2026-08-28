"""Integration tests for read-only rule audit analysis."""

from types import SimpleNamespace

from sqlalchemy import text
from tests.support.database import insert_rule
from tests.support.database import insert_transaction as insert_test_transaction

from finance_app.modules.rules.audit import (
    OVERLAP_CATEGORY_CONFLICT,
    OVERLAP_CRITICAL_CONFLICT,
    OVERLAP_HARMLESS,
    OVERLAP_TAG_DIFFERENCE,
    STALE_UNUSED,
    RuleAuditData,
    TransactionRuleAudit,
    analyze_rule_overlaps,
    analyze_shadowed_rules,
    analyze_specificity_warnings,
    analyze_stale_rules,
    compute_rule_match_sets,
    get_rule_audit_summary,
    shared_rule_pair_audits,
)
from finance_app.modules.rules.audit_preview import preview_rule_change


def insert_audit_transaction(
    conn,
    description,
    amount,
    fingerprint,
    category="Food",
    category_source="rule",
    category_rule_id=None,
    reviewed_at=None,
    tags=None,
    tx_date="2026-01-02",
):
    """Insert an audit-test transaction and optional current tags."""
    return insert_test_transaction(
        conn,
        description=description,
        amount=amount,
        category=category,
        fingerprint=fingerprint,
        category_source=category_source,
        category_rule_id=category_rule_id,
        reviewed_at=reviewed_at,
        needs_review=0 if category != "UNKNOWN" else 1,
        tags=tags or [],
        tag_source=category_source,
        tx_date=tx_date,
    )


def overlap_by_rule_ids(overlaps, rule_a_id, rule_b_id):
    """Return an overlap finding for a pair of rule IDs."""
    expected = {rule_a_id, rule_b_id}
    for overlap in overlaps:
        if {overlap.rule_a["id"], overlap.rule_b["id"]} == expected:
            return overlap
    return None


def test_rule_overlap_analysis_indexes_only_actual_co_matches():
    """Verify overlap analysis builds rule pairs from transaction matches."""
    rule_a = {"id": 1, "keyword": "A", "category": "Food", "tags": []}
    rule_b = {"id": 2, "keyword": "B", "category": "Food", "tags": []}
    rule_c = {"id": 3, "keyword": "C", "category": "Travel", "tags": []}
    match_a = SimpleNamespace(rule=rule_a, category="Food", tags=(), specificity=1)
    match_b = SimpleNamespace(rule=rule_b, category="Food", tags=(), specificity=1)
    audit_data = RuleAuditData(
        rules=(rule_a, rule_b, rule_c),
        transactions=({"id": 10},),
        transaction_audits=(
            TransactionRuleAudit(
                transaction={"id": 10},
                matches=(match_a, match_b),
                winning_match=match_a,
                losing_matches=(match_b,),
            ),
        ),
        rule_by_id={1: rule_a, 2: rule_b, 3: rule_c},
        matches_by_rule_id={1: (), 2: (), 3: ()},
        wins_by_rule_id={},
        losses_by_rule_id={},
        stored_applied_by_rule_id={},
    )

    assert set(shared_rule_pair_audits(audit_data)) == {(1, 2)}
    overlaps = analyze_rule_overlaps(audit_data)
    assert len(overlaps) == 1
    assert {overlaps[0].rule_a["id"], overlaps[0].rule_b["id"]} == {1, 2}


def test_rule_audit_classifies_harmless_overlap(core_conn):
    """Verify equivalent overlapping rules are harmless."""
    broad_id = insert_rule(core_conn, "METRO", "Food", tags=["Tax"])
    specific_id = insert_rule(core_conn, "METRO GROCERY", "Food", tags=["Tax"])
    insert_audit_transaction(core_conn, "Metro Grocery #123", 12.34, "audit-harmless")

    audit_data = compute_rule_match_sets(core_conn)
    overlap = overlap_by_rule_ids(analyze_rule_overlaps(audit_data), broad_id, specific_id)

    assert overlap is not None
    assert overlap.severity == OVERLAP_HARMLESS
    assert overlap.shared_count == 1


def test_rule_audit_classifies_tag_difference_separately(core_conn):
    """Verify same-category tag differences are not category conflicts."""
    broad_id = insert_rule(core_conn, "METRO", "Food", tags=["Tax"])
    specific_id = insert_rule(core_conn, "METRO GROCERY", "Food", tags=["Shared"])
    insert_audit_transaction(core_conn, "Metro Grocery #123", 12.34, "audit-tag-diff")

    audit_data = compute_rule_match_sets(core_conn)
    overlap = overlap_by_rule_ids(analyze_rule_overlaps(audit_data), broad_id, specific_id)

    assert overlap is not None
    assert overlap.severity == OVERLAP_TAG_DIFFERENCE


def test_rule_audit_classifies_category_conflict(core_conn):
    """Verify one-row category conflicts are flagged without critical severity."""
    broad_id = insert_rule(core_conn, "METRO", "Food")
    specific_id = insert_rule(core_conn, "METRO GROCERY", "Utilities")
    insert_audit_transaction(core_conn, "Metro Grocery #123", 12.34, "audit-category-conflict")

    audit_data = compute_rule_match_sets(core_conn)
    overlap = overlap_by_rule_ids(analyze_rule_overlaps(audit_data), broad_id, specific_id)

    assert overlap is not None
    assert overlap.severity == OVERLAP_CATEGORY_CONFLICT


def test_rule_audit_classifies_critical_conflict_for_multiple_shared_rows(core_conn):
    """Verify multi-row category conflicts are critical."""
    broad_id = insert_rule(core_conn, "METRO", "Food")
    specific_id = insert_rule(core_conn, "METRO GROCERY", "Utilities")
    insert_audit_transaction(core_conn, "Metro Grocery #123", 12.34, "audit-critical-1")
    insert_audit_transaction(core_conn, "Metro Grocery #456", 20.00, "audit-critical-2")

    audit_data = compute_rule_match_sets(core_conn)
    overlap = overlap_by_rule_ids(analyze_rule_overlaps(audit_data), broad_id, specific_id)

    assert overlap is not None
    assert overlap.severity == OVERLAP_CRITICAL_CONFLICT


def test_rule_audit_finds_shadowed_and_unused_rules(core_conn):
    """Verify shadowed and unused rules are reported from match data."""
    broad_id = insert_rule(core_conn, "METRO", "Food")
    specific_id = insert_rule(core_conn, "METRO GROCERY", "Utilities")
    unused_id = insert_rule(core_conn, "UNUSED SHOP", "Food")
    insert_audit_transaction(core_conn, "Metro Grocery #123", 12.34, "audit-shadowed")

    audit_data = compute_rule_match_sets(core_conn)
    shadowed = analyze_shadowed_rules(audit_data)
    stale = analyze_stale_rules(audit_data)

    assert [finding.rule["id"] for finding in shadowed] == [broad_id]
    assert shadowed[0].total_matches == 1
    assert shadowed[0].total_wins == 0
    assert shadowed[0].most_common_shadowing_rule_id == specific_id
    assert any(finding.rule["id"] == unused_id and finding.status == STALE_UNUSED for finding in stale)


def test_rule_audit_excludes_unknown_transactions_by_default(core_conn):
    """Verify UNKNOWN rows do not create default overlap findings."""
    broad_id = insert_rule(core_conn, "METRO", "Food")
    specific_id = insert_rule(core_conn, "METRO GROCERY", "Utilities")
    insert_audit_transaction(
        core_conn,
        "Metro Grocery #123",
        12.34,
        "audit-unknown",
        category="UNKNOWN",
        category_source="unknown",
    )

    default_data = compute_rule_match_sets(core_conn)
    included_data = compute_rule_match_sets(core_conn, include_unknown=True)

    assert overlap_by_rule_ids(analyze_rule_overlaps(default_data), broad_id, specific_id) is None
    assert overlap_by_rule_ids(analyze_rule_overlaps(included_data), broad_id, specific_id) is not None


def test_rule_audit_summary_reports_overlap_and_application_counts(core_conn):
    """Verify summary diagnostics derive from the same match matrix."""
    insert_rule(core_conn, "METRO", "Food")
    specific_id = insert_rule(core_conn, "METRO GROCERY", "Utilities")
    insert_rule(core_conn, "UNUSED SHOP", "Food")
    insert_audit_transaction(
        core_conn,
        "Metro Grocery #123",
        12.34,
        "audit-summary",
        category_rule_id=specific_id,
    )

    summary = get_rule_audit_summary(compute_rule_match_sets(core_conn))

    assert summary["total_active_rules"] == 3
    assert summary["rules_with_zero_historical_matches"] == 1
    assert summary["rules_with_historical_matches_but_zero_applied"] == 1
    assert summary["overlapping_rule_pairs"] == 1
    assert summary["category_conflict_overlaps"] == 1


def test_rule_audit_warns_when_broad_rule_beats_more_specific_rule(core_conn):
    """Verify broad winners over more constrained rules create precedence warnings."""
    broad_id = insert_rule(core_conn, "METRO GROCERY", "Food")
    specific_id = insert_rule(core_conn, "METRO", "Utilities", amount_min=10)
    insert_audit_transaction(
        core_conn,
        "Metro Grocery",
        12.34,
        "audit-specificity-warning",
        category_rule_id=broad_id,
    )

    audit_data = compute_rule_match_sets(core_conn)
    warnings = analyze_specificity_warnings(audit_data)

    assert len(warnings) == 1
    assert warnings[0].broad_rule["id"] == broad_id
    assert warnings[0].specific_rule["id"] == specific_id
    assert warnings[0].shared_count == 1
    assert warnings[0].reason == "Higher confidence"
    assert warnings[0].conflicting_count == 1
    assert get_rule_audit_summary(audit_data)["specificity_warnings"] == 1


def test_rule_audit_preview_remove_rule_is_read_only(core_conn):
    """Verify delete preview compares outcomes without mutating persisted rows."""
    broad_id = insert_rule(core_conn, "METRO", "Food", tags=["Grocery"])
    specific_id = insert_rule(core_conn, "METRO GROCERY", "Utilities", tags=["Tax"])
    transaction_id = insert_audit_transaction(
        core_conn,
        "Metro Grocery #123",
        12.34,
        "audit-preview-read-only",
        category="Utilities",
        category_rule_id=specific_id,
        tags=["Tax"],
    )

    preview = preview_rule_change(core_conn, {"type": "delete_rule", "rule_id": specific_id})
    stored = core_conn.execute(
        text("""
        SELECT category, category_rule_id
        FROM transactions
        WHERE id = :p0
        """),
        {"p0": transaction_id},
    ).fetchone()

    assert preview.rule["id"] == specific_id
    assert preview.summary["total_affected_transactions"] == 1
    assert preview.summary["winning_rule_changes"] == 1
    assert preview.summary["category_changes"] == 1
    assert preview.summary["tag_changes"] == 1
    assert preview.summary["would_become_unknown"] == 0
    assert preview.grouped_impacts["category_change"][0].proposed_winning_match.rule["id"] == broad_id
    assert tuple(stored) == ("Utilities", specific_id)


def test_rule_audit_preview_apply_modes_distinguish_wins_from_force(core_conn):
    """Verify selected-rule preview modes distinguish normal wins from force apply."""
    broad_id = insert_rule(core_conn, "METRO", "Food")
    specific_id = insert_rule(core_conn, "METRO GROCERY", "Utilities")
    insert_audit_transaction(
        core_conn,
        "Metro Pharmacy",
        8.50,
        "audit-apply-preview-win",
        category="UNKNOWN",
        category_source="unknown",
    )
    insert_audit_transaction(
        core_conn,
        "Metro Grocery #123",
        12.34,
        "audit-apply-preview-loss",
        category="Utilities",
        category_rule_id=specific_id,
    )

    where_wins = preview_rule_change(core_conn, {"type": "apply_where_wins", "rule_id": broad_id})
    force_apply = preview_rule_change(core_conn, {"type": "force_apply_rule", "rule_id": broad_id})

    assert where_wins.summary["total_affected_transactions"] == 1
    assert where_wins.impacts[0].transaction["description"] == "Metro Pharmacy"
    assert force_apply.summary["total_affected_transactions"] == 2
    assert force_apply.summary["category_changes"] == 2
    assert any(impact.transaction["description"] == "Metro Grocery #123" for impact in force_apply.impacts)


def test_rule_audit_preview_create_rule_is_read_only(core_conn):
    """Verify create preview compares proposed rule behavior without saving it."""
    transaction_id = insert_audit_transaction(
        core_conn,
        "Metro Pharmacy",
        12.34,
        "audit-create-preview",
        category="UNKNOWN",
        category_source="unknown",
    )
    proposed_rule = {
        "keyword": "METRO",
        "category": "Food",
        "tags": ["Grocery"],
        "amount_min": None,
        "amount_max": None,
        "merchant_id": None,
        "account_id": None,
        "direction": "any",
    }

    preview = preview_rule_change(
        core_conn,
        {"type": "create_rule", "proposed_rule": proposed_rule},
    )
    stored_rule = core_conn.execute(text("SELECT id FROM category_rules WHERE keyword = 'METRO'")).fetchone()

    assert preview.rule == {}
    assert preview.proposed_rule["keyword"] == "METRO"
    assert preview.summary["total_affected_transactions"] == 1
    assert preview.summary["category_changes"] == 1
    assert preview.impacts[0].transaction["id"] == transaction_id
    assert preview.impacts[0].proposed_category == "Food"
    assert preview.impacts[0].proposed_tags == ("Grocery",)
    assert stored_rule is None


def test_rule_audit_preview_approve_rule_is_read_only(core_conn):
    """Verify approval preview does not mutate automatic rule approval."""
    rule_id = insert_rule(core_conn, "AUTO STORE", "Food", source="automatic")

    preview = preview_rule_change(
        core_conn,
        {"type": "approve_rule", "rule_id": rule_id},
    )
    stored_rule = core_conn.execute(
        text("""
        SELECT source, ai_approved
        FROM category_rules
        WHERE id = :p0
        """),
        {"p0": rule_id},
    ).fetchone()

    assert preview.rule["id"] == rule_id
    assert preview.summary["total_affected_transactions"] == 0
    assert preview.impacts == ()
    assert tuple(stored_rule) == ("automatic", 0)


def test_rule_audit_preview_edit_rule_is_read_only(core_conn):
    """Verify edit preview compares proposed rule behavior without saving it."""
    rule_id = insert_rule(core_conn, "METRO", "Food", tags=["Grocery"])
    included_id = insert_audit_transaction(
        core_conn,
        "Metro Pharmacy",
        12.34,
        "audit-edit-preview-keep",
        category="Food",
        category_rule_id=rule_id,
        tags=["Grocery"],
    )
    excluded_id = insert_audit_transaction(
        core_conn,
        "Metro Cafe",
        8.50,
        "audit-edit-preview-exclude",
        category="Food",
        category_rule_id=rule_id,
        tags=["Grocery"],
    )
    proposed_rule = {
        "keyword": "METRO",
        "category": "Utilities",
        "tags": ["Tax"],
        "amount_min": 10.0,
        "amount_max": 20.0,
        "merchant_id": None,
        "account_id": None,
        "direction": "any",
    }

    preview = preview_rule_change(
        core_conn,
        {"type": "edit_rule", "rule_id": rule_id, "proposed_rule": proposed_rule},
    )
    stored_rule = core_conn.execute(
        text("""
        SELECT category, amount_min, amount_max
        FROM category_rules
        WHERE id = :p0
        """),
        {"p0": rule_id},
    ).fetchone()

    assert preview.rule["category"] == "Food"
    assert preview.proposed_rule["category"] == "Utilities"
    assert preview.summary["total_affected_transactions"] == 2
    assert preview.summary["category_changes"] == 2
    impacts_by_id = {impact.transaction["id"]: impact for impact in preview.impacts}
    assert impacts_by_id[included_id].proposed_category == "Utilities"
    assert impacts_by_id[included_id].proposed_tags == ("Tax",)
    assert impacts_by_id[excluded_id].proposed_category == "UNKNOWN"
    assert tuple(stored_rule) == ("Food", None, None)


def test_rule_audit_preview_apply_all_rules_is_read_only(core_conn):
    """Verify apply-all preview reports normal winners without mutating transactions."""
    rule_id = insert_rule(core_conn, "METRO", "Food")
    transaction_id = insert_audit_transaction(
        core_conn,
        "Metro Pharmacy",
        8.50,
        "audit-apply-all-preview",
        category="UNKNOWN",
        category_source="unknown",
    )
    insert_audit_transaction(
        core_conn,
        "Other Store",
        6.00,
        "audit-apply-all-preview-miss",
        category="UNKNOWN",
        category_source="unknown",
    )

    preview = preview_rule_change(core_conn, {"type": "apply_all_rules"})
    stored = core_conn.execute(
        text("""
        SELECT category, category_rule_id
        FROM transactions
        WHERE id = :p0
        """),
        {"p0": transaction_id},
    ).fetchone()

    assert preview.rule == {}
    assert preview.summary["total_affected_transactions"] == 1
    assert preview.impacts[0].proposed_rule_id == rule_id
    assert tuple(stored) == ("UNKNOWN", None)
