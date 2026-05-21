"""View-model builders for rule audit pages.

Formats read-only audit analysis from the rules audit service for Flask/Jinja
templates. The presenter does not mutate rules or transactions.
"""

from urllib.parse import urlencode

from flask import url_for

from finance_app.core.config import settings
from finance_app.core.query import parse_page, parse_sort_direction
from finance_app.modules.rules.audit import (
    OVERLAP_CATEGORY_CONFLICT,
    OVERLAP_CRITICAL_CONFLICT,
    OVERLAP_HARMLESS,
    OVERLAP_TAG_DIFFERENCE,
    PREVIEW_APPLY_ALL_RULES,
    PREVIEW_APPLY_WHERE_WINS,
    PREVIEW_APPROVE_RULE,
    PREVIEW_CREATE_RULE,
    PREVIEW_DELETE_RULE,
    PREVIEW_EDIT_RULE,
    PREVIEW_FORCE_APPLY_RULE,
    PREVIEW_REMOVE_RULE,
    STALE_STALE,
    STALE_UNUSED,
    analyze_rule_overlaps,
    analyze_shadowed_rules,
    analyze_specificity_warnings,
    analyze_stale_rules,
    compute_rule_match_sets,
    compute_rule_specificity_score,
    get_rule_audit_summary,
    last_matched_date,
    overlap_severity_rank,
    preview_rule_change,
    preview_rule_set_change,
    rule_id_from_match,
    shared_matching_transaction_audits,
    transaction_was_manually_reviewed,
)
from finance_app.modules.categories.service import get_category_rules
from finance_app.core.constants import (
    CATEGORY_RULE_DIRECTION_ANY,
    CATEGORY_RULE_DIRECTION_LABELS,
    CATEGORY_RULE_SOURCE_AUTOMATIC,
    CATEGORY_RULE_SOURCE_MANUAL,
)
from finance_app.core.i18n import gettext
from finance_app.modules.settings.runtime import get_int_setting
from finance_app.modules.rules.import_export import (
    RULE_IMPORT_MODE_ADD,
    RULE_IMPORT_MODE_OVERRIDE,
    preview_rules_import,
)


SEVERITY_LABELS = {
    OVERLAP_HARMLESS: "Harmless",
    OVERLAP_TAG_DIFFERENCE: "Tag difference",
    OVERLAP_CATEGORY_CONFLICT: "Category conflict",
    OVERLAP_CRITICAL_CONFLICT: "Critical conflict",
}
SEVERITY_BADGE_CLASSES = {
    OVERLAP_HARMLESS: "text-bg-success",
    OVERLAP_TAG_DIFFERENCE: "text-bg-info",
    OVERLAP_CATEGORY_CONFLICT: "text-bg-warning",
    OVERLAP_CRITICAL_CONFLICT: "text-bg-danger",
}
STALE_LABELS = {
    STALE_UNUSED: "Unused",
    STALE_STALE: "Stale",
}
STALE_BADGE_CLASSES = {
    STALE_UNUSED: "text-bg-secondary",
    STALE_STALE: "text-bg-warning",
}
PREVIEW_ACTION_LABELS = {
    PREVIEW_REMOVE_RULE: "Preview deleting rule",
    PREVIEW_CREATE_RULE: "Preview creating rule",
    PREVIEW_DELETE_RULE: "Preview deleting rule",
    PREVIEW_EDIT_RULE: "Preview editing rule",
    PREVIEW_APPROVE_RULE: "Preview approving rule",
    PREVIEW_APPLY_WHERE_WINS: "Preview applying where rule wins",
    PREVIEW_FORCE_APPLY_RULE: "Preview force applying rule",
    PREVIEW_APPLY_ALL_RULES: "Preview applying all rules",
}
IMPACT_GROUP_LABELS = {
    "category_change": "Category would change",
    "tags_change": "Tags would change",
    "winning_rule_change": "Winning rule would change",
    "no_material_change": "No material change",
}
IMPACT_GROUP_BADGE_CLASSES = {
    "category_change": "text-bg-warning",
    "tags_change": "text-bg-info",
    "winning_rule_change": "text-bg-secondary",
    "no_material_change": "text-bg-success",
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
    shadowed_rows = [
        present_shadowed_rule(finding, audit_data.rule_by_id)
        for finding in shadowed_rules
    ]
    stale_rows = [present_stale_rule(finding) for finding in stale_rules]
    specificity_warning_rows = [
        present_specificity_warning(warning)
        for warning in specificity_warnings
    ]
    overlap_filter = parse_overlap_filter(request_arg(args, "overlap_filter"))
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
    tag_difference_rows = [
        row
        for row in searched_overlap_rows
        if row["severity"] == OVERLAP_TAG_DIFFERENCE
    ]
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
        "summary": get_rule_audit_summary(audit_data),
        "overlap_filter": overlap_filter,
        "overlap_query": overlap_query,
        "overlap_filter_options": overlap_filter_options(args, searched_overlap_rows),
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
        (
            finding
            for finding in analyze_shadowed_rules(audit_data)
            if finding.rule["id"] == rule_id
        ),
        None,
    )
    stale_finding = next(
        (
            finding
            for finding in analyze_stale_rules(audit_data)
            if finding.rule["id"] == rule_id
        ),
        None,
    )
    matches = audit_data.matches_by_rule_id.get(rule_id, ())
    wins = audit_data.wins_by_rule_id.get(rule_id, ())
    losses = audit_data.losses_by_rule_id.get(rule_id, ())
    total_matches = len(matches)
    total_wins = len(wins)
    total_losses = len(losses)

    return {
        "rule": present_rule(rule),
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
            present_shadowed_rule(shadowed_finding, audit_data.rule_by_id)
            if shadowed_finding
            else None
        ),
        "stale_finding": present_stale_rule(stale_finding) if stale_finding else None,
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
            present_impact_group(key, impacts)
            for key, impacts in preview.grouped_impacts.items()
            if impacts
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
            "has_category_or_tag_changes": (
                summary["category_changes"] > 0 or summary["tag_changes"] > 0
            ),
        },
        "impact_groups": [
            present_impact_group(key, impacts)
            for key, impacts in impact_preview.grouped_impacts.items()
            if impacts
        ],
        "preview_page_size": page_size,
        "limited": impact_preview.limited,
    }


def build_audit_table_context(args, table_name, rows, page_size, sort_options, default_sort, default_direction):
    """Return paginated and sorted display context for one audit table.

    Args:
        args: Request query parameters whose table-specific keys are preserved
            when building sort and pagination URLs.
        table_name: Prefix used for query parameters, such as ``overlap``.
        rows: Presented finding rows to sort and slice.
        page_size: Maximum number of rows per page.
        sort_options: Mapping of sort keys to key functions.
        default_sort: Sort key used when the query parameter is missing or invalid.
        default_direction: Sort direction used when the query parameter is missing.

    Returns:
        A mapping containing the visible rows, pagination metadata, and URL
        builders used by the Jinja table header and pagination macros.
    """
    page_param = f"{table_name}_page"
    sort_param = f"{table_name}_sort"
    direction_param = f"{table_name}_direction"
    sort = parse_audit_table_sort(request_arg(args, sort_param), sort_options, default_sort)
    direction = parse_sort_direction(request_arg(args, direction_param), default=default_direction)
    ordered_rows = sorted(rows, key=sort_options[sort], reverse=direction == "desc")
    total_count = len(ordered_rows)
    total_pages = max(1, (total_count + page_size - 1) // page_size)
    page = min(parse_page(request_arg(args, page_param)), total_pages)
    offset = (page - 1) * page_size
    visible_rows = ordered_rows[offset:offset + page_size]

    return {
        "rows": visible_rows,
        "sort": sort,
        "direction": direction,
        "page": page,
        "page_size": page_size,
        "total_count": total_count,
        "total_pages": total_pages,
        "page_start": offset + 1 if total_count else 0,
        "page_end": min(offset + page_size, total_count),
        "page_url": lambda page_number: audit_table_url(args, {page_param: page_number}),
        "sort_url": lambda sort_name: audit_table_url(
            args,
            {
                sort_param: parse_audit_table_sort(sort_name, sort_options, default_sort),
                direction_param: next_audit_sort_direction(sort_name, sort, direction, default_direction),
                page_param: 1,
            },
        ),
    }


def build_overlap_transaction_table_context(args, rule_a_id, rule_b_id, rows, page_size):
    """Return paginated and sorted context for overlap detail transactions."""
    sort_options = overlap_transaction_sort_options()
    sort = parse_audit_table_sort(request_arg(args, "shared_sort"), sort_options, "date")
    direction = parse_sort_direction(request_arg(args, "shared_direction"), default="desc")
    ordered_rows = sorted(rows, key=sort_options[sort], reverse=direction == "desc")
    total_count = len(ordered_rows)
    total_pages = max(1, (total_count + page_size - 1) // page_size)
    page = min(parse_page(request_arg(args, "shared_page")), total_pages)
    offset = (page - 1) * page_size
    visible_rows = ordered_rows[offset:offset + page_size]

    return {
        "rows": visible_rows,
        "sort": sort,
        "direction": direction,
        "page": page,
        "page_size": page_size,
        "total_count": total_count,
        "total_pages": total_pages,
        "page_start": offset + 1 if total_count else 0,
        "page_end": min(offset + page_size, total_count),
        "page_url": lambda page_number: overlap_transaction_table_url(
            args,
            rule_a_id,
            rule_b_id,
            {"shared_page": page_number},
        ),
        "sort_url": lambda sort_name: overlap_transaction_table_url(
            args,
            rule_a_id,
            rule_b_id,
            {
                "shared_sort": parse_audit_table_sort(sort_name, sort_options, "date"),
                "shared_direction": next_audit_sort_direction(
                    sort_name,
                    sort,
                    direction,
                    "desc",
                ),
                "shared_page": 1,
            },
        ),
    }


def request_arg(args, name, default=None):
    """Return one query argument value from Flask or plain mappings."""
    if hasattr(args, "get"):
        return args.get(name, default)
    return default


def parse_audit_table_sort(value, sort_options, default):
    """Return a whitelisted audit table sort key."""
    sort = str(value or default).strip()
    return sort if sort in sort_options else default


def parse_overlap_filter(value):
    """Return the selected overlap severity filter for the main audit page."""
    allowed_filters = {
        "all",
        OVERLAP_HARMLESS,
        OVERLAP_TAG_DIFFERENCE,
        OVERLAP_CATEGORY_CONFLICT,
        OVERLAP_CRITICAL_CONFLICT,
    }
    normalized = str(value or "all").strip()
    return normalized if normalized in allowed_filters else "all"


def clean_overlap_search_query(value):
    """Return a normalized rule-audit search query for display and matching."""
    return " ".join(str(value or "").split())


def search_overlap_rows(rows, query):
    """Return overlap rows matching every term in the search query."""
    return search_audit_rows(rows, query, overlap_search_text)


def search_specificity_warning_rows(rows, query):
    """Return specificity warning rows matching every search term."""
    return search_audit_rows(rows, query, specificity_warning_search_text)


def search_shadowed_rows(rows, query):
    """Return shadowed-rule rows matching every search term."""
    return search_audit_rows(rows, query, shadowed_rule_search_text)


def search_stale_rows(rows, query):
    """Return stale or unused rule rows matching every search term."""
    return search_audit_rows(rows, query, stale_rule_search_text)


def search_audit_rows(rows, query, text_builder):
    """Return audit rows whose searchable text contains every query term.

    Args:
        rows: Presented audit rows to filter.
        query: User-entered search query.
        text_builder: Callable returning the searchable text for a row.

    Returns:
        The subset of rows matching every normalized search term.
    """
    terms = [term.casefold() for term in clean_overlap_search_query(query).split()]
    if not terms:
        return rows
    return [
        row
        for row in rows
        if all(term in text_builder(row) for term in terms)
    ]


def overlap_search_text(row):
    """Return searchable text for one overlap row."""
    parts = [
        row.get("severity_label"),
        row.get("winning_rule_side_label"),
        row.get("winning_rule_label"),
        row.get("losing_rule_label"),
        row.get("suggested_action"),
    ]
    for key in ("rule_a", "rule_b"):
        rule = row.get(key) or {}
        parts.extend(
            [
                rule.get("label"),
                rule.get("category"),
                rule.get("tag_label"),
                rule.get("scope_label"),
                rule.get("scope_value"),
                rule.get("direction_label"),
                rule.get("amount_label"),
                rule.get("source_label"),
                rule.get("approval_label"),
            ],
        )
    return " ".join(str(part or "") for part in parts).casefold()


def specificity_warning_search_text(row):
    """Return searchable text for one specificity warning row."""
    parts = [
        rule_search_text(row.get("broad_rule") or {}),
        rule_search_text(row.get("specific_rule") or {}),
        row.get("reason"),
        row.get("suggested_action"),
        row.get("shared_count"),
        row.get("conflicting_count"),
    ]
    return " ".join(str(part or "") for part in parts).casefold()


def shadowed_rule_search_text(row):
    """Return searchable text for one shadowed-rule row."""
    parts = [
        "Shadowed",
        rule_search_text(row.get("rule") or {}),
        rule_search_text(row.get("most_common_shadowing_rule") or {}),
        row.get("suggested_action"),
        row.get("total_matches"),
        row.get("total_wins"),
        row.get("total_losses"),
        row.get("conflicting_loss_count"),
    ]
    return " ".join(str(part or "") for part in parts).casefold()


def stale_rule_search_text(row):
    """Return searchable text for one stale or unused rule row."""
    parts = [
        row.get("status"),
        row.get("status_label"),
        rule_search_text(row.get("rule") or {}),
        row.get("suggested_action"),
        row.get("total_matches"),
        row.get("total_wins"),
        row.get("stored_applied_count"),
        row.get("last_matched_date"),
        row.get("recent_matches"),
    ]
    return " ".join(str(part or "") for part in parts).casefold()


def rule_search_text(rule):
    """Return searchable text for one presented rule mapping."""
    parts = [
        rule.get("label"),
        rule.get("keyword"),
        rule.get("merchant_name"),
        rule.get("category"),
        rule.get("tag_label"),
        rule.get("scope_label"),
        rule.get("scope_value"),
        rule.get("direction_label"),
        rule.get("amount_label"),
        rule.get("source_label"),
        rule.get("approval_label"),
    ]
    for factor in rule.get("specificity_factors") or []:
        parts.extend([factor.get("label"), factor.get("value")])
    return " ".join(str(part or "") for part in parts).casefold()


def filter_overlap_rows(rows, overlap_filter):
    """Return overlap rows matching the selected severity filter."""
    if overlap_filter == "all":
        return rows
    return [row for row in rows if row["severity"] == overlap_filter]


def overlap_filter_options(args, rows):
    """Return filter controls and counts for the main overlap findings table."""
    counts = {
        "all": len(rows),
        OVERLAP_CRITICAL_CONFLICT: 0,
        OVERLAP_CATEGORY_CONFLICT: 0,
        OVERLAP_TAG_DIFFERENCE: 0,
        OVERLAP_HARMLESS: 0,
    }
    for row in rows:
        if row["severity"] in counts:
            counts[row["severity"]] += 1
    return [
        {
            "value": "all",
            "label": "All overlaps",
            "count": counts["all"],
            "url": audit_table_url(args, {"overlap_filter": "all", "overlap_page": 1}),
        },
        {
            "value": OVERLAP_CRITICAL_CONFLICT,
            "label": "Critical conflicts",
            "count": counts[OVERLAP_CRITICAL_CONFLICT],
            "url": audit_table_url(
                args,
                {"overlap_filter": OVERLAP_CRITICAL_CONFLICT, "overlap_page": 1},
            ),
        },
        {
            "value": OVERLAP_CATEGORY_CONFLICT,
            "label": "Category conflicts",
            "count": counts[OVERLAP_CATEGORY_CONFLICT],
            "url": audit_table_url(
                args,
                {"overlap_filter": OVERLAP_CATEGORY_CONFLICT, "overlap_page": 1},
            ),
        },
        {
            "value": OVERLAP_TAG_DIFFERENCE,
            "label": "Tag differences",
            "count": counts[OVERLAP_TAG_DIFFERENCE],
            "url": audit_table_url(
                args,
                {"overlap_filter": OVERLAP_TAG_DIFFERENCE, "overlap_page": 1},
            ),
        },
        {
            "value": OVERLAP_HARMLESS,
            "label": "Harmless overlaps",
            "count": counts[OVERLAP_HARMLESS],
            "url": audit_table_url(
                args,
                {"overlap_filter": OVERLAP_HARMLESS, "overlap_page": 1},
            ),
        },
    ]


def next_audit_sort_direction(sort_name, current_sort, current_direction, default_direction):
    """Return the next direction for an audit table sort link."""
    if sort_name != current_sort:
        return default_direction
    return "desc" if current_direction == "asc" else "asc"


def audit_table_url(args, updates):
    """Build a Rule Audit URL while preserving unrelated query parameters."""
    query_pairs = []
    if hasattr(args, "lists"):
        for key, values in args.lists():
            if key in updates:
                continue
            for value in values:
                query_pairs.append((key, value))
    elif isinstance(args, dict):
        for key, value in args.items():
            if key not in updates:
                query_pairs.append((key, value))

    for key, value in updates.items():
        if value is not None and value != "":
            query_pairs.append((key, str(value)))

    query = urlencode(query_pairs)
    base_url = url_for("rules.audit_rules")
    return f"{base_url}?{query}" if query else base_url


def overlap_transaction_table_url(args, rule_a_id, rule_b_id, updates):
    """Build an overlap detail URL while preserving unrelated query parameters."""
    query_pairs = []
    if hasattr(args, "lists"):
        for key, values in args.lists():
            if key in updates:
                continue
            for value in values:
                query_pairs.append((key, value))
    elif isinstance(args, dict):
        for key, value in args.items():
            if key not in updates:
                query_pairs.append((key, value))

    for key, value in updates.items():
        if value is not None and value != "":
            query_pairs.append((key, str(value)))

    query = urlencode(query_pairs)
    base_url = url_for(
        "rules.audit_rule_overlap",
        rule_a_id=rule_a_id,
        rule_b_id=rule_b_id,
    )
    return f"{base_url}?{query}" if query else base_url


def overlap_sort_options():
    """Return sort functions for overlap-style audit rows."""
    return {
        "rule_a": lambda row: sortable_text(row["rule_a"]["label"]),
        "rule_b": lambda row: sortable_text(row["rule_b"]["label"]),
        "rules": lambda row: sortable_text(f"{row['rule_a']['label']} {row['rule_b']['label']}"),
        "shared": lambda row: sortable_number(row["shared_count"]),
        "severity": lambda row: overlap_severity_rank(row["severity"]),
        "winner": lambda row: sortable_text(
            f"{row['winning_rule_side_label']} {row['winning_rule_label']}",
        ),
        "current_applied": lambda row: sortable_number(
            row["rule_a_applied_count"] + row["rule_b_applied_count"],
        ),
        "suggested": lambda row: sortable_text(row["suggested_action"]),
    }


def tag_difference_sort_options():
    """Return sort functions for tag-difference audit rows."""
    options = overlap_sort_options()
    options["tags"] = lambda row: sortable_text(
        f"{row['rule_a']['tag_label']} {row['rule_b']['tag_label']}",
    )
    return options


def overlap_transaction_sort_options():
    """Return sort functions for overlap detail transaction rows."""
    return {
        "date": lambda row: sortable_text(row["transaction"].get("tx_date")),
        "account": lambda row: sortable_text(row["transaction"].get("account_name")),
        "merchant": lambda row: sortable_text(
            row["transaction"].get("merchant_name")
            or row["transaction"].get("canonical_merchant")
            or row["transaction"].get("merchant_key"),
        ),
        "description": lambda row: sortable_text(row["transaction"].get("description")),
        "amount": lambda row: float(row["transaction"].get("amount") or 0),
        "current_category": lambda row: sortable_text(row["transaction"].get("category")),
        "rule_a": lambda row: sortable_text(row["winning_rule_match"]["category"]),
        "rule_b": lambda row: sortable_text(row["losing_rule_match"]["category"]),
    }


def specificity_warning_sort_options():
    """Return sort functions for specificity warning rows."""
    return {
        "broad_rule": lambda row: sortable_text(row["broad_rule"]["label"]),
        "specific_rule": lambda row: sortable_text(row["specific_rule"]["label"]),
        "shared": lambda row: sortable_number(row["shared_count"]),
        "reason": lambda row: sortable_text(row["reason"]),
        "conflicts": lambda row: sortable_number(row["conflicting_count"]),
        "suggested": lambda row: sortable_text(row["suggested_action"]),
    }


def shadowed_rule_sort_options():
    """Return sort functions for shadowed-rule rows."""
    return {
        "rule": lambda row: sortable_text(row["rule"]["label"]),
        "matches": lambda row: sortable_number(row["total_matches"]),
        "wins": lambda row: sortable_number(row["total_wins"]),
        "losses": lambda row: sortable_number(row["total_losses"]),
        "shadowing_rule": lambda row: sortable_text(
            row["most_common_shadowing_rule"]["label"]
            if row["most_common_shadowing_rule"]
            else "",
        ),
        "conflicts": lambda row: sortable_number(row["conflicting_loss_count"]),
        "suggested": lambda row: sortable_text(row["suggested_action"]),
    }


def stale_rule_sort_options():
    """Return sort functions for stale and unused rule rows."""
    return {
        "rule": lambda row: sortable_text(row["rule"]["label"]),
        "status": lambda row: stale_status_rank(row["status"]),
        "matches": lambda row: sortable_number(row["total_matches"]),
        "current_applied": lambda row: sortable_number(row["stored_applied_count"]),
        "last_matched": lambda row: sortable_text(row["last_matched_date"] or ""),
        "suggested": lambda row: sortable_text(row["suggested_action"]),
    }


def stale_status_rank(status):
    """Return a deterministic sort rank for stale-rule status badges."""
    return {
        STALE_UNUSED: 0,
        STALE_STALE: 1,
    }.get(status, 2)


def sortable_text(value):
    """Return a normalized text value for audit table sorting."""
    return str(value or "").casefold()


def sortable_number(value):
    """Return a numeric value for audit table sorting."""
    return int(value or 0)


def present_overlap(overlap, rule_by_id):
    """Return a display mapping for one overlapping rule pair."""
    rule_a_id = overlap.rule_a["id"]
    rule_b_id = overlap.rule_b["id"]
    winner_ids = [
        rule_id
        for rule_id, _count in sorted(
            overlap.winning_rule_counts.items(),
            key=lambda item: (-item[1], rule_label(rule_by_id.get(item[0], {}))),
        )
    ]
    loser_ids = [
        rule_id
        for rule_id in (rule_a_id, rule_b_id)
        if rule_id not in winner_ids
    ]
    winning_side_label = overlap_winning_side_label(
        overlap.winning_rule_counts,
        rule_a_id,
        rule_b_id,
    )
    return {
        "rule_a": present_rule(overlap.rule_a),
        "rule_b": present_rule(overlap.rule_b),
        "shared_count": overlap.shared_count,
        "severity": overlap.severity,
        "severity_label": SEVERITY_LABELS.get(overlap.severity, "Unknown"),
        "severity_badge_class": SEVERITY_BADGE_CLASSES.get(
            overlap.severity,
            "text-bg-secondary",
        ),
        "winning_rule_label": joined_rule_count_labels(
            overlap.winning_rule_counts,
            rule_by_id,
        ),
        "winning_rule_side_label": winning_side_label,
        "losing_rule_label": joined_rule_labels(loser_ids, rule_by_id) or "Mixed",
        "rule_a_applied_count": overlap.rule_a_applied_count,
        "rule_b_applied_count": overlap.rule_b_applied_count,
        "suggested_action": overlap.suggested_action,
        "detail_url_rule_a_id": rule_a_id,
        "detail_url_rule_b_id": rule_b_id,
    }


def present_shared_transaction(audit, winning_rule_id, losing_rule_id, rule_by_id):
    """Return a display mapping for one shared matching transaction."""
    match_by_rule_id = {
        rule_id_from_match(match): match
        for match in audit.matches
    }
    winning_rule_match = match_by_rule_id[winning_rule_id]
    losing_rule_match = match_by_rule_id[losing_rule_id]
    actual_winning_rule_id = rule_id_from_match(audit.winning_match)
    winner_rule = rule_by_id.get(actual_winning_rule_id, {})
    transaction = audit.transaction
    return {
        "transaction": transaction,
        "current_tag_label": ", ".join(transaction.get("tags") or []) or "-",
        "manual_reviewed": transaction_was_manually_reviewed(transaction),
        "winning_rule_match": present_match(winning_rule_match),
        "losing_rule_match": present_match(losing_rule_match),
        "rule_a_match": present_match(winning_rule_match),
        "rule_b_match": present_match(losing_rule_match),
        "winning_rule_label": rule_label(winner_rule) if winner_rule else "-",
        "winner_agrees_with_current_category": (
            audit.winning_match is not None
            and audit.winning_match.category == transaction.get("category")
        ),
    }


def present_match(match):
    """Return a display mapping for one scored rule match."""
    return {
        "category": match.category,
        "tag_label": ", ".join(match.tags) or "-",
        "confidence": match.confidence,
        "match_score": match.match_score,
        "specificity": match.specificity,
        "specificity_label": specificity_label(match.specificity),
    }


def present_rule_with_specificity_comparison(rule, other_rule):
    """Return a presented rule with a human-readable specificity comparison."""
    presented = present_rule(rule)
    other_specificity = compute_rule_specificity_score(other_rule)
    if presented["specificity"] > other_specificity:
        presented["specificity_comparison_label"] = "More specific"
        presented["specificity_comparison_badge_class"] = "text-bg-success"
    elif presented["specificity"] < other_specificity:
        presented["specificity_comparison_label"] = "Less specific"
        presented["specificity_comparison_badge_class"] = "text-bg-secondary"
    else:
        presented["specificity_comparison_label"] = "Same specificity"
        presented["specificity_comparison_badge_class"] = "text-bg-info"
    return presented


def present_rule_interactions(interactions, rule_by_id):
    """Return display rows for per-rule win/loss interactions."""
    return [
        {
            "rule": present_rule(rule_by_id[rule_id]),
            "shared_count": values["shared_count"],
            "conflicting_count": values["conflicting_count"],
        }
        for rule_id, values in sorted(
            interactions.items(),
            key=lambda item: (-item[1]["shared_count"], rule_label(rule_by_id.get(item[0], {}))),
        )
        if rule_id in rule_by_id
    ]


def present_specificity_warning(warning):
    """Return a display mapping for one specificity or precedence warning."""
    return {
        "broad_rule": present_rule(warning.broad_rule),
        "specific_rule": present_rule(warning.specific_rule),
        "shared_count": warning.shared_count,
        "reason": warning.reason,
        "conflicting_count": warning.conflicting_count,
        "suggested_action": warning.suggested_action,
    }


def present_impact_group(key, impacts):
    """Return a display mapping for a preview impact group."""
    return {
        "key": key,
        "label": IMPACT_GROUP_LABELS.get(key, key),
        "badge_class": IMPACT_GROUP_BADGE_CLASSES.get(key, "text-bg-secondary"),
        "count": len(impacts),
        "impacts": [present_rule_change_impact(impact) for impact in impacts],
    }


def present_rule_change_impact(impact):
    """Return a display mapping for one transaction-level preview impact."""
    return {
        "transaction": impact.transaction,
        "current_winner": present_preview_match(impact.current_winning_match),
        "proposed_winner": present_preview_match(impact.proposed_winning_match),
        "current_category": impact.current_category,
        "proposed_category": impact.proposed_category,
        "current_tags": ", ".join(impact.current_tags) or "-",
        "proposed_tags": ", ".join(impact.proposed_tags) or "-",
    }


def present_preview_match(match):
    """Return a compact display mapping for a preview winner."""
    if match is None:
        return {
            "rule_id": None,
            "label": gettext("No matching rule"),
            "category": "",
            "tags": "-",
        }

    rule = present_rule(match.rule)
    return {
        "rule_id": rule["id"],
        "label": rule["label"],
        "category": match.category,
        "tags": ", ".join(match.tags) or "-",
    }


def present_shadowed_rule(finding, rule_by_id):
    """Return a display mapping for one shadowed-rule finding."""
    shadowing_rule = rule_by_id.get(finding.most_common_shadowing_rule_id, {})
    return {
        "rule": present_rule(finding.rule),
        "total_matches": finding.total_matches,
        "total_wins": finding.total_wins,
        "total_losses": finding.total_losses,
        "most_common_shadowing_rule": present_rule(shadowing_rule) if shadowing_rule else None,
        "conflicting_loss_count": finding.conflicting_loss_count,
        "suggested_action": finding.suggested_action,
    }


def present_stale_rule(finding):
    """Return a display mapping for one stale or unused rule finding."""
    return {
        "rule": present_rule(finding.rule),
        "status": finding.status,
        "status_label": STALE_LABELS.get(finding.status, "Stale"),
        "status_badge_class": STALE_BADGE_CLASSES.get(finding.status, "text-bg-secondary"),
        "total_matches": finding.total_matches,
        "total_wins": finding.total_wins,
        "stored_applied_count": finding.stored_applied_count,
        "last_matched_date": finding.last_matched_date,
        "recent_matches": finding.recent_matches,
        "suggested_action": finding.suggested_action,
    }


def present_rule(rule):
    """Return a display mapping for a category rule."""
    specificity = compute_rule_specificity_score(rule)
    scope_label = rule_scope_label(rule)
    scope_value = rule_scope_value(rule)
    return {
        "id": rule.get("id"),
        "label": rule_label(rule),
        "keyword": rule.get("keyword") or "",
        "merchant_id": rule.get("merchant_id"),
        "account_id": rule.get("account_id"),
        "category": rule.get("category") or "",
        "tags": list(rule.get("tags") or []),
        "tag_label": ", ".join(rule.get("tags") or []) or "-",
        "source": rule.get("source") or "",
        "merchant_name": rule.get("merchant_name") or "",
        "amount_min": rule.get("amount_min"),
        "amount_max": rule.get("amount_max"),
        "amount_label": amount_constraint_label(rule),
        "direction": rule.get("direction") or "any",
        "direction_label": rule_direction_label(rule.get("direction")),
        "scope_label": scope_label,
        "scope_value": scope_value,
        "source_label": rule_source_label(rule.get("source")),
        "source_badge_class": rule_source_badge_class(rule.get("source")),
        "approval_label": rule_approval_label(rule),
        "approval_badge_class": rule_approval_badge_class(rule),
        "status_label": "Active",
        "status_badge_class": "text-bg-success",
        "specificity": specificity,
        "specificity_label": specificity_label(specificity),
        "specificity_factors": specificity_factors(specificity),
    }


def import_mode_label(mode):
    """Return the display label for a rule import mode."""
    return {
        RULE_IMPORT_MODE_ADD: "Add new rules only",
        RULE_IMPORT_MODE_OVERRIDE: "Override all rules",
    }.get(mode, "Import rules")


def rule_label(rule):
    """Return the primary display label for a rule."""
    merchant_name = rule.get("merchant_name")
    if merchant_name:
        return merchant_name
    return rule.get("keyword") or f"Rule {rule.get('id')}"


def rule_scope_label(rule):
    """Return the primary match scope label for a rule."""
    if rule.get("merchant_id"):
        return "Merchant"
    return "Keyword"


def rule_scope_value(rule):
    """Return the display value that explains the rule's primary match scope."""
    if rule.get("merchant_id"):
        return rule.get("merchant_name") or rule_label(rule)
    return rule.get("keyword") or rule_label(rule)


def specificity_label(specificity):
    """Return a compact display label for a matcher specificity tuple."""
    return " / ".join(str(part) for part in specificity)


def specificity_factors(specificity):
    """Return readable rule-level specificity factors for display."""
    return [
        {"label": "Merchant bound", "value": "Yes" if specificity[0] else "No"},
        {"label": "Account bound", "value": "Yes" if specificity[1] else "No"},
        {"label": "Direction bound", "value": "Yes" if specificity[2] else "No"},
        {"label": "Amount bound", "value": "Yes" if specificity[3] else "No"},
        {"label": "Keyword length", "value": specificity[4]},
    ]


def win_interactions_for_rule(rule_id, wins):
    """Return rules that lost on transactions won by the selected rule."""
    interactions = {}
    for audit in wins:
        winning_match = audit.winning_match
        for losing_match in audit.losing_matches:
            losing_rule_id = rule_id_from_match(losing_match)
            if losing_rule_id == rule_id:
                continue
            interaction = interactions.setdefault(
                losing_rule_id,
                {"shared_count": 0, "conflicting_count": 0},
            )
            interaction["shared_count"] += 1
            if winning_match is not None and losing_match.category != winning_match.category:
                interaction["conflicting_count"] += 1
    return interactions


def loss_interactions_for_rule(rule_id, losses):
    """Return rules that beat the selected rule on shared transactions."""
    interactions = {}
    for audit in losses:
        winning_rule_id = rule_id_from_match(audit.winning_match)
        if winning_rule_id is None or winning_rule_id == rule_id:
            continue
        selected_match = next(
            (match for match in audit.matches if rule_id_from_match(match) == rule_id),
            None,
        )
        interaction = interactions.setdefault(
            winning_rule_id,
            {"shared_count": 0, "conflicting_count": 0},
        )
        interaction["shared_count"] += 1
        if (
            selected_match is not None
            and audit.winning_match is not None
            and selected_match.category != audit.winning_match.category
        ):
            interaction["conflicting_count"] += 1
    return interactions


def win_rate_label(wins, matches):
    """Return a percentage label for rule win rate."""
    if not matches:
        return "-"
    return f"{(wins / matches) * 100:.0f}%"


def amount_constraint_label(rule):
    """Return a human-friendly amount constraint label for a rule."""
    amount_min = rule.get("amount_min")
    amount_max = rule.get("amount_max")
    if amount_min is None and amount_max is None:
        return gettext("Any amount")
    if amount_min is not None and amount_max is not None and amount_min == amount_max:
        return gettext("Exact amount: {amount}", amount=f"{amount_min:.2f}")
    if amount_min is None:
        return gettext("Up to {amount}", amount=f"{amount_max:.2f}")
    if amount_max is None:
        return gettext("From {amount}", amount=f"{amount_min:.2f}")
    return gettext(
        "From {minimum} to {maximum}",
        minimum=f"{amount_min:.2f}",
        maximum=f"{amount_max:.2f}",
    )


def rule_direction_label(direction):
    """Return the display label for a rule direction constraint."""
    return CATEGORY_RULE_DIRECTION_LABELS.get(
        direction or CATEGORY_RULE_DIRECTION_ANY,
        CATEGORY_RULE_DIRECTION_LABELS[CATEGORY_RULE_DIRECTION_ANY],
    )


def rule_source_label(source):
    """Return the display label for a rule source."""
    return {
        CATEGORY_RULE_SOURCE_MANUAL: "Manual",
        CATEGORY_RULE_SOURCE_AUTOMATIC: "Automatic",
    }.get(source, str(source or "").strip() or "Unknown")


def rule_source_badge_class(source):
    """Return the Bootstrap badge class for a rule source."""
    return {
        CATEGORY_RULE_SOURCE_MANUAL: "text-bg-primary",
        CATEGORY_RULE_SOURCE_AUTOMATIC: "text-bg-info",
    }.get(source, "text-bg-secondary")


def rule_approval_label(rule):
    """Return the approval label for a rule when approval applies."""
    if rule.get("source") != CATEGORY_RULE_SOURCE_AUTOMATIC:
        return "-"
    return "Approved" if rule.get("ai_approved") else "Suggested"


def rule_approval_badge_class(rule):
    """Return the Bootstrap badge class for an approval label."""
    if rule.get("source") != CATEGORY_RULE_SOURCE_AUTOMATIC:
        return "text-bg-secondary"
    return "text-bg-success" if rule.get("ai_approved") else "text-bg-warning"


def joined_rule_count_labels(rule_counts, rule_by_id):
    """Return comma-separated rule labels with transaction counts."""
    labels = []
    for rule_id, count in sorted(
        rule_counts.items(),
        key=lambda item: (-item[1], rule_label(rule_by_id.get(item[0], {}))),
    ):
        rule = rule_by_id.get(rule_id)
        if rule:
            labels.append(f"{rule_label(rule)} ({count})")
    return ", ".join(labels) or "-"


def overlap_winning_side_label(rule_counts, rule_a_id, rule_b_id):
    """Return whether Rule A, Rule B, or a mixed set wins an overlap."""
    winner_ids = {rule_id for rule_id, count in rule_counts.items() if count > 0}
    if winner_ids == {rule_a_id}:
        return "Rule A"
    if winner_ids == {rule_b_id}:
        return "Rule B"
    return "Mixed"


def overlap_display_rule_ids(overlap, rule_a_id, rule_b_id):
    """Return rule IDs ordered as dominant winner then losing rule for detail pages."""
    winner_ids = [
        rule_id
        for rule_id, count in sorted(
            overlap.winning_rule_counts.items(),
            key=lambda item: (-item[1], item[0]),
        )
        if rule_id in {rule_a_id, rule_b_id} and count > 0
    ]
    winning_rule_id = winner_ids[0] if winner_ids else rule_a_id
    losing_rule_id = rule_b_id if winning_rule_id == rule_a_id else rule_a_id
    return winning_rule_id, losing_rule_id


def joined_rule_labels(rule_ids, rule_by_id):
    """Return comma-separated rule labels for rule IDs."""
    labels = [
        rule_label(rule_by_id[rule_id])
        for rule_id in rule_ids
        if rule_id in rule_by_id
    ]
    return ", ".join(labels)
