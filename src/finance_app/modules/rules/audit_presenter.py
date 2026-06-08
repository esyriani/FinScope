"""View-model builders for rule audit pages.

Formats read-only audit analysis from the rules audit service for Flask/Jinja
templates. The presenter does not mutate rules or transactions.
"""

from finance_app.core.config import settings
from finance_app.modules.categories.service import get_category_rules
from finance_app.modules.rules.audit import (
    OVERLAP_CATEGORY_CONFLICT,
    OVERLAP_CRITICAL_CONFLICT,
    OVERLAP_TAG_DIFFERENCE,
    PREVIEW_APPLY_ALL_RULES,
    PREVIEW_APPLY_WHERE_WINS,
    PREVIEW_APPROVE_RULE,
    PREVIEW_CREATE_RULE,
    PREVIEW_DELETE_RULE,
    PREVIEW_EDIT_RULE,
    PREVIEW_FORCE_APPLY_RULE,
    PREVIEW_REMOVE_RULE,
    analyze_rule_overlaps,
    analyze_shadowed_rules,
    analyze_specificity_warnings,
    analyze_stale_rules,
    compute_rule_match_sets,
    get_rule_audit_summary,
    last_matched_date,
    preview_rule_change,
    preview_rule_set_change,
    shared_matching_transaction_audits,
)
from finance_app.modules.rules.audit_formatting import (
    attach_rule_action_flags,
    build_rule_assessment,
    build_win_explanation,
    import_mode_label,
    loss_interactions_for_rule,
    overlap_display_rule_ids,
    present_impact_group,
    present_overlap,
    present_rule,
    present_rule_interactions,
    present_rule_with_specificity_comparison,
    present_shadowed_rule,
    present_shared_transaction,
    present_specificity_warning,
    present_stale_rule,
    recommended_next_step,
    win_interactions_for_rule,
    win_rate_label,
)
from finance_app.modules.rules.audit_tables import (
    build_audit_table_context,
    build_overlap_transaction_table_context,
    clean_overlap_search_query,
    filter_overlap_rows,
    overlap_filter_options,
    overlap_sort_options,
    parse_audit_open_section,
    parse_overlap_filter,
    request_arg,
    search_overlap_rows,
    search_shadowed_rows,
    search_specificity_warning_rows,
    search_stale_rows,
    shadowed_rule_sort_options,
    specificity_warning_sort_options,
    stale_rule_sort_options,
    tag_difference_sort_options,
)
from finance_app.modules.rules.import_export import (
    RULE_IMPORT_MODE_ADD,
    preview_rules_import,
)
from finance_app.modules.rules.service import count_rule_transaction_references_by_rule_id
from finance_app.modules.settings.runtime import get_int_setting

PREVIEW_ACTION_LABELS = {
    PREVIEW_REMOVE_RULE: "Preview removal impact",
    PREVIEW_CREATE_RULE: "Preview creating rule",
    PREVIEW_DELETE_RULE: "Preview removal impact",
    PREVIEW_EDIT_RULE: "Preview editing rule",
    PREVIEW_APPROVE_RULE: "Preview approving rule",
    PREVIEW_APPLY_WHERE_WINS: "Preview applying where rule wins",
    PREVIEW_FORCE_APPLY_RULE: "Preview force applying rule",
    PREVIEW_APPLY_ALL_RULES: "Preview applying all rules",
}


def build_rule_audit_context(conn, args=None, transaction_limit=None):
    """Return template context for the main Rule Audit page.

    Args:
        conn: Open SQLAlchemy Core connection used for audit reads and settings.
        args: Request query parameters used to sort and paginate audit tables.
        transaction_limit: Optional maximum number of newest transactions to analyze.

    Returns:
        A mapping ready for ``rules_audit.html``. All audit rows are derived from
        the same read-only match-set analysis, while table pagination only slices
        the presented findings for display.
    """
    args = args or {}
    page_size = get_int_setting(conn, "default_table_page_size", settings.default_table_page_size)
    audit_data = compute_rule_match_sets(conn, transaction_limit=transaction_limit)
    overlaps = analyze_rule_overlaps(audit_data)
    shadowed_rules = analyze_shadowed_rules(audit_data)
    stale_rules = analyze_stale_rules(audit_data)
    specificity_warnings = analyze_specificity_warnings(audit_data)
    overlap_rows = [present_overlap(overlap, audit_data.rule_by_id) for overlap in overlaps]
    shadowed_rows = [present_shadowed_rule(finding, audit_data.rule_by_id) for finding in shadowed_rules]
    stale_rows = [present_stale_rule(finding) for finding in stale_rules]
    transaction_reference_counts = count_rule_transaction_references_by_rule_id(
        conn,
        [rule["id"] for rule in audit_data.rules],
    )
    attach_rule_action_flags(
        [row["rule"] for row in shadowed_rows] + [row["rule"] for row in stale_rows],
        transaction_reference_counts,
    )
    specificity_warning_rows = [present_specificity_warning(warning) for warning in specificity_warnings]
    overlap_filter = parse_overlap_filter(request_arg(args, "overlap_filter"))
    open_section = parse_audit_open_section(request_arg(args, "open"))
    overlap_query = clean_overlap_search_query(request_arg(args, "overlap_q"))
    searched_overlap_rows = search_overlap_rows(overlap_rows, overlap_query)
    searched_shadowed_rows = search_shadowed_rows(shadowed_rows, overlap_query)
    searched_stale_rows = search_stale_rows(stale_rows, overlap_query)
    searched_specificity_warning_rows = search_specificity_warning_rows(
        specificity_warning_rows,
        overlap_query,
    )
    filtered_overlap_rows = filter_overlap_rows(searched_overlap_rows, overlap_filter)
    category_conflict_rows = [
        row
        for row in searched_overlap_rows
        if row["severity"] in {OVERLAP_CATEGORY_CONFLICT, OVERLAP_CRITICAL_CONFLICT}
    ]
    tag_difference_rows = [row for row in searched_overlap_rows if row["severity"] == OVERLAP_TAG_DIFFERENCE]
    summary = get_rule_audit_summary(audit_data)
    summary["critical_conflict_overlaps"] = sum(
        1 for row in overlap_rows if row["severity"] == OVERLAP_CRITICAL_CONFLICT
    )
    audit_tables = {
        "overlaps": build_audit_table_context(
            args,
            "overlap",
            filtered_overlap_rows,
            page_size,
            overlap_sort_options(),
            "severity",
            "asc",
        ),
        "category_conflicts": build_audit_table_context(
            args,
            "conflict",
            category_conflict_rows,
            page_size,
            overlap_sort_options(),
            "severity",
            "asc",
        ),
        "tag_differences": build_audit_table_context(
            args,
            "tag",
            tag_difference_rows,
            page_size,
            tag_difference_sort_options(),
            "shared",
            "desc",
        ),
        "specificity_warnings": build_audit_table_context(
            args,
            "warning",
            searched_specificity_warning_rows,
            page_size,
            specificity_warning_sort_options(),
            "conflicts",
            "desc",
        ),
        "shadowed_rules": build_audit_table_context(
            args,
            "shadowed",
            searched_shadowed_rows,
            page_size,
            shadowed_rule_sort_options(),
            "losses",
            "desc",
        ),
        "stale_rules": build_audit_table_context(
            args,
            "stale",
            searched_stale_rows,
            page_size,
            stale_rule_sort_options(),
            "status",
            "asc",
        ),
    }

    return {
        "summary": summary,
        "recommended_next_step": recommended_next_step(
            summary,
            shadowed_rows,
        ),
        "overlap_filter": overlap_filter,
        "overlap_query": overlap_query,
        "overlap_filter_options": overlap_filter_options(args, searched_overlap_rows),
        "open_section": open_section,
        "audit_tables": audit_tables,
        "overlaps": audit_tables["overlaps"]["rows"],
        "category_conflicts": audit_tables["category_conflicts"]["rows"],
        "tag_differences": audit_tables["tag_differences"]["rows"],
        "shadowed_rules": audit_tables["shadowed_rules"]["rows"],
        "stale_rules": audit_tables["stale_rules"]["rows"],
        "specificity_warnings": audit_tables["specificity_warnings"]["rows"],
        "limited": audit_data.limited,
    }


def build_rule_overlap_detail_context(conn, rule_a_id, rule_b_id, args=None, transaction_limit=None):
    """Return template context for a shared matching transactions detail page."""
    args = args or {}
    page_size = get_int_setting(conn, "default_table_page_size", settings.default_table_page_size)
    audit_data = compute_rule_match_sets(conn, transaction_limit=transaction_limit)
    rule_a = audit_data.rule_by_id.get(rule_a_id)
    rule_b = audit_data.rule_by_id.get(rule_b_id)
    if rule_a is None or rule_b is None:
        return None

    overlaps = [
        overlap
        for overlap in analyze_rule_overlaps(audit_data)
        if {overlap.rule_a["id"], overlap.rule_b["id"]} == {rule_a_id, rule_b_id}
    ]
    if not overlaps:
        return None

    overlap = overlaps[0]
    shared_audits = shared_matching_transaction_audits(audit_data, rule_a_id, rule_b_id)
    winning_rule_id, losing_rule_id = overlap_display_rule_ids(overlap, rule_a_id, rule_b_id)
    winning_rule = audit_data.rule_by_id[winning_rule_id]
    losing_rule = audit_data.rule_by_id[losing_rule_id]
    transaction_rows = [
        present_shared_transaction(audit, winning_rule_id, losing_rule_id, audit_data.rule_by_id)
        for audit in shared_audits
    ]
    transaction_table = build_overlap_transaction_table_context(
        args,
        rule_a_id,
        rule_b_id,
        transaction_rows,
        page_size,
    )
    return {
        "overlap": present_overlap(overlap, audit_data.rule_by_id),
        "winning_rule": present_rule_with_specificity_comparison(winning_rule, losing_rule),
        "losing_rule": present_rule_with_specificity_comparison(losing_rule, winning_rule),
        "win_explanation": build_win_explanation(
            shared_audits,
            winning_rule_id,
            losing_rule_id,
            audit_data.rule_by_id,
        ),
        "transactions": transaction_table["rows"],
        "transaction_table": transaction_table,
        "limited": audit_data.limited,
    }


def build_rule_detail_context(conn, rule_id, transaction_limit=None):
    """Return template context for one rule's read-only audit detail page."""
    page_size = get_int_setting(conn, "default_table_page_size", settings.default_table_page_size)
    audit_data = compute_rule_match_sets(conn, transaction_limit=transaction_limit)
    rule = audit_data.rule_by_id.get(rule_id)
    if rule is None:
        return None

    overlaps = [
        overlap
        for overlap in analyze_rule_overlaps(audit_data)
        if rule_id in {overlap.rule_a["id"], overlap.rule_b["id"]}
    ]
    shadowed_finding = next(
        (finding for finding in analyze_shadowed_rules(audit_data) if finding.rule["id"] == rule_id),
        None,
    )
    stale_finding = next(
        (finding for finding in analyze_stale_rules(audit_data) if finding.rule["id"] == rule_id),
        None,
    )
    matches = audit_data.matches_by_rule_id.get(rule_id, ())
    wins = audit_data.wins_by_rule_id.get(rule_id, ())
    losses = audit_data.losses_by_rule_id.get(rule_id, ())
    total_matches = len(matches)
    total_wins = len(wins)
    total_losses = len(losses)
    transaction_reference_counts = count_rule_transaction_references_by_rule_id(conn, [rule_id])

    return {
        "rule": attach_rule_action_flags(
            [present_rule(rule)],
            transaction_reference_counts,
        )[0],
        "metrics": {
            "total_matches": total_matches,
            "total_wins": total_wins,
            "total_losses": total_losses,
            "stored_applied_count": len(audit_data.stored_applied_by_rule_id.get(rule_id, ())),
            "win_rate": win_rate_label(total_wins, total_matches),
            "last_matched_date": last_matched_date(matches),
        },
        "overlaps": [present_overlap(overlap, audit_data.rule_by_id) for overlap in overlaps],
        "category_conflicts": [
            present_overlap(overlap, audit_data.rule_by_id)
            for overlap in overlaps
            if overlap.severity in {OVERLAP_CATEGORY_CONFLICT, OVERLAP_CRITICAL_CONFLICT}
        ],
        "tag_differences": [
            present_overlap(overlap, audit_data.rule_by_id)
            for overlap in overlaps
            if overlap.severity == OVERLAP_TAG_DIFFERENCE
        ],
        "rules_shadowed_by_this": present_rule_interactions(
            win_interactions_for_rule(rule_id, wins),
            audit_data.rule_by_id,
        ),
        "rules_shadowing_this": present_rule_interactions(
            loss_interactions_for_rule(rule_id, losses),
            audit_data.rule_by_id,
        ),
        "shadowed_finding": (
            present_shadowed_rule(shadowed_finding, audit_data.rule_by_id) if shadowed_finding else None
        ),
        "stale_finding": present_stale_rule(stale_finding) if stale_finding else None,
        "assessment": build_rule_assessment(
            total_matches,
            total_wins,
            total_losses,
            shadowed_finding,
            stale_finding,
            overlaps,
        ),
        "table_page_size": page_size,
        "limited": audit_data.limited,
    }


def build_rule_change_preview_context(conn, action, rule_id, proposed_rule=None, transaction_limit=None):
    """Return template context for a read-only rule change impact preview."""
    preview = preview_rule_change(
        conn,
        {"type": action, "rule_id": rule_id, "proposed_rule": proposed_rule},
        transaction_limit=transaction_limit,
    )
    if preview is None:
        return None

    summary = preview.summary
    has_transaction_impacts = summary["total_affected_transactions"] > 0
    has_category_or_tag_changes = summary["category_changes"] > 0 or summary["tag_changes"] > 0
    page_size = get_int_setting(conn, "default_table_page_size", settings.default_table_page_size)
    return {
        "preview": {
            "action": preview.action,
            "action_label": PREVIEW_ACTION_LABELS.get(preview.action, "Preview impact"),
            "rule": present_rule(preview.rule) if preview.rule else None,
            "proposed_rule": present_rule(preview.proposed_rule) if preview.proposed_rule else None,
            "summary": summary,
            "has_transaction_impacts": has_transaction_impacts,
            "has_category_or_tag_changes": has_category_or_tag_changes,
        },
        "impact_groups": [
            present_impact_group(key, impacts) for key, impacts in preview.grouped_impacts.items() if impacts
        ],
        "preview_page_size": page_size,
        "limited": preview.limited,
    }


def build_rule_import_preview_context(conn, raw_text, mode, filename, transaction_limit=None):
    """Return template context for a read-only rule import preview.

    Args:
        conn: Open SQLAlchemy Core connection.
        raw_text: Uploaded CSV contents.
        mode: Requested import mode.
        filename: Original uploaded filename used for confirmation metadata.
        transaction_limit: Optional maximum number of newest transactions.

    Returns:
        A mapping for ``rules_import_preview.html``. The context combines the
        parsed import plan with a historical impact preview and does not write
        rules, categories, merchants, or transactions.
    """
    import_plan = preview_rules_import(conn, raw_text, mode)
    current_rules = tuple(get_category_rules(conn))
    proposed_rule_set = (
        current_rules + import_plan.proposed_rules
        if import_plan.mode == RULE_IMPORT_MODE_ADD
        else import_plan.proposed_rules
    )
    impact_preview = preview_rule_set_change(
        conn,
        "import_rules",
        proposed_rule_set,
        transaction_limit=transaction_limit,
    )
    summary = impact_preview.summary
    page_size = get_int_setting(conn, "default_table_page_size", settings.default_table_page_size)
    return {
        "import_preview": {
            "filename": filename,
            "raw_text": raw_text,
            "mode": import_plan.mode,
            "mode_label": import_mode_label(import_plan.mode),
            "total_rows": import_plan.total_rows,
            "rules_to_import": len(import_plan.proposed_rules),
            "skipped_existing": import_plan.skipped_existing,
            "skipped_duplicate": import_plan.skipped_duplicate,
            "replaced_rules": import_plan.replaced_rules,
            "cleared_transaction_rule_refs": import_plan.cleared_transaction_rule_refs,
            "rules": [present_rule(rule) for rule in import_plan.proposed_rules],
        },
        "preview": {
            "summary": summary,
            "has_transaction_impacts": summary["total_affected_transactions"] > 0,
            "has_category_or_tag_changes": (summary["category_changes"] > 0 or summary["tag_changes"] > 0),
        },
        "impact_groups": [
            present_impact_group(key, impacts) for key, impacts in impact_preview.grouped_impacts.items() if impacts
        ],
        "preview_page_size": page_size,
        "limited": impact_preview.limited,
    }
