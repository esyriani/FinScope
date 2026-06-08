"""Rule audit table helpers.

Builds paginated, searchable, and sortable table contexts for rule audit pages.
The helpers depend on Flask URL generation and read-only presented audit rows.
"""

from urllib.parse import urlencode

from flask import url_for

from finance_app.core.query import parse_page, parse_sort_direction
from finance_app.modules.rules.audit import (
    OVERLAP_CATEGORY_CONFLICT,
    OVERLAP_CRITICAL_CONFLICT,
    OVERLAP_HARMLESS,
    OVERLAP_TAG_DIFFERENCE,
    STALE_STALE,
    STALE_UNUSED,
    overlap_severity_rank,
)

AUDIT_TABLE_OPEN_SECTIONS = {
    "overlap": "rule-overlap-findings",
    "conflict": "rule-overlap-findings",
    "tag": "rule-overlap-findings",
    "warning": "specificity-warning-findings",
    "shadowed": "shadowed-rule-findings",
    "stale": "stale-rule-findings",
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
    open_section = audit_table_open_section_id(table_name)
    ordered_rows = sorted(rows, key=sort_options[sort], reverse=direction == "desc")
    total_count = len(ordered_rows)
    total_pages = max(1, (total_count + page_size - 1) // page_size)
    page = min(parse_page(request_arg(args, page_param)), total_pages)
    offset = (page - 1) * page_size
    visible_rows = ordered_rows[offset : offset + page_size]

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
        "page_url": lambda page_number: audit_table_url(
            args,
            {
                page_param: page_number,
                "open": open_section,
            },
        ),
        "sort_url": lambda sort_name: audit_table_url(
            args,
            {
                sort_param: parse_audit_table_sort(sort_name, sort_options, default_sort),
                direction_param: next_audit_sort_direction(sort_name, sort, direction, default_direction),
                page_param: 1,
                "open": open_section,
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
    visible_rows = ordered_rows[offset : offset + page_size]

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


def parse_audit_open_section(value):
    """Return a whitelisted Rule Audit section id to expand after navigation."""
    allowed = set(audit_table_open_section_id(name) for name in AUDIT_TABLE_OPEN_SECTIONS)
    normalized = str(value or "").strip()
    return normalized if normalized in allowed else ""


def audit_table_open_section_id(table_name):
    """Return the collapse id associated with one audit table prefix."""
    return AUDIT_TABLE_OPEN_SECTIONS.get(table_name, "")


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
    return [row for row in rows if all(term in text_builder(row) for term in terms)]


def overlap_search_text(row):
    """Return searchable text for one overlap row."""
    parts = [
        row.get("severity_label"),
        row.get("winning_rule_side_label"),
        row.get("winning_rule_label"),
        row.get("losing_rule_label"),
        row.get("suggested_action"),
        row.get("suggested_action_label"),
        row.get("suggested_action_reason"),
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
        row.get("suggested_action_label"),
        row.get("suggested_action_reason"),
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
        row.get("suggested_action_label"),
        row.get("suggested_action_reason"),
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
        row.get("suggested_action_label"),
        row.get("suggested_action_reason"),
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
    if overlap_filter == OVERLAP_CATEGORY_CONFLICT:
        return [row for row in rows if row["severity"] in {OVERLAP_CATEGORY_CONFLICT, OVERLAP_CRITICAL_CONFLICT}]
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
    category_conflict_count = counts[OVERLAP_CATEGORY_CONFLICT] + counts[OVERLAP_CRITICAL_CONFLICT]
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
            "count": category_conflict_count,
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
            row["transaction"].get("merchant_name") or row["transaction"].get("merchant_key"),
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
            row["most_common_shadowing_rule"]["label"] if row["most_common_shadowing_rule"] else "",
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
