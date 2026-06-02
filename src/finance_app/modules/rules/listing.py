"""List-page orchestration for the rules feature."""

from urllib.parse import urlencode

from flask import url_for
from sqlalchemy import String, case, cast, func, literal, or_, select

from finance_app.core.constants import (
    CATEGORY_RULE_DIRECTION_ANY,
    CATEGORY_RULE_DIRECTION_LABELS,
    CATEGORY_RULE_DIRECTIONS,
    CATEGORY_RULE_SOURCES,
    CATEGORY_RULE_SOURCE_AUTOMATIC,
    CATEGORY_RULE_SOURCE_MANUAL,
)
from finance_app.core.money import money_to_float
from finance_app.modules.categories.taxonomy import (
    get_category_description_map,
    get_rule_tags_by_rule_id,
    get_tag_color_map,
    get_tag_option_rows,
)
from finance_app.modules.categories.tag_filters import rule_tag_condition
from finance_app.modules.categories.service import get_category_options
from finance_app.core.config import settings
from finance_app.database.engine import db_core_transaction
from finance_app.database.tables import (
    accounts as accounts_table,
    category_rules as category_rules_table,
    merchants as merchants_table,
)
from finance_app.modules.settings.runtime import get_int_setting
from finance_app.core.query import parse_page, parse_sort_direction
from finance_app.modules.rules.service import count_rule_transaction_references_by_rule_id


RULE_APPROVAL_FILTER_APPROVED = "approved"
RULE_APPROVAL_FILTER_SUGGESTED = "suggested"
RULE_APPROVAL_FILTERS = (
    "",
    RULE_APPROVAL_FILTER_APPROVED,
    RULE_APPROVAL_FILTER_SUGGESTED,
)
RULE_APPROVAL_FILTER_OPTIONS = (
    ("", "All"),
    (RULE_APPROVAL_FILTER_APPROVED, "Approved"),
    (RULE_APPROVAL_FILTER_SUGGESTED, "Suggested"),
)
RULE_SOURCE_FILTER_OPTIONS = (
    ("", "All sources"),
    (CATEGORY_RULE_SOURCE_MANUAL, "Manual"),
    (CATEGORY_RULE_SOURCE_AUTOMATIC, "Automatic"),
)


def build_rules_context(args):
    """Build rules context."""
    search = args.get("search", "").strip()
    selected_categories = [
        category.strip()
        for category in args.getlist("categories")
        if category.strip()
    ]
    legacy_category = args.get("category", "").strip()
    if legacy_category and legacy_category not in selected_categories:
        selected_categories.append(legacy_category)
    selected_source = args.get("source", "").strip()
    if selected_source not in CATEGORY_RULE_SOURCES:
        selected_source = ""
    selected_tags = [
        tag.strip()
        for tag in args.getlist("tags")
        if tag.strip()
    ]
    approval = args.get("approval", "").strip()
    if approval not in RULE_APPROVAL_FILTERS:
        approval = ""
    sort = args.get("sort", "source").strip()
    direction = parse_sort_direction(args.get("direction"), default="asc")
    page = parse_page(args.get("page"))

    with db_core_transaction() as conn:
        page_size = get_int_setting(conn, "default_table_page_size", settings.default_table_page_size)
        category_options = get_category_options(conn)
        account_options = account_option_rows(conn)
        category_descriptions = get_category_description_map(conn)
        selected_categories = [
            category for category in selected_categories
            if category in category_options
        ]
        selected_category = selected_categories[0] if len(selected_categories) == 1 else ""

        sort, sort_expression = resolve_rules_sort(sort)
        filters = build_rule_filters(
            search,
            approval,
            selected_categories,
            selected_source,
            selected_tags,
        )
        total_count = rules_count(conn, filters)
        total_pages = max(1, (total_count + page_size - 1) // page_size)
        page = min(page, total_pages)
        offset = (page - 1) * page_size

        rows = rule_rows(conn, filters, sort_expression, direction, page_size, offset)
        decorate_rule_rows(conn, rows)
        tag_options = get_tag_option_rows(conn)

    return {
        "rules": rows,
        "account_options": account_options,
        "direction_options": [
            (direction, CATEGORY_RULE_DIRECTION_LABELS[direction])
            for direction in CATEGORY_RULE_DIRECTIONS
        ],
        "category_options": category_options,
        "category_descriptions": category_descriptions,
        "tag_options": tag_options,
        "search": search,
        "selected_category": selected_category,
        "selected_categories": selected_categories,
        "selected_source": selected_source,
        "selected_tags": selected_tags,
        "selected_approval": approval,
        "approval_filter_options": RULE_APPROVAL_FILTER_OPTIONS,
        "source_filter_options": RULE_SOURCE_FILTER_OPTIONS,
        "sort": sort,
        "direction": direction,
        "page_url": lambda page_number: rules_list_url(
            search,
            selected_categories,
            selected_source,
            approval,
            selected_tags,
            sort,
            direction,
            page_number,
        ),
        "sort_url": lambda sort_name: rules_list_url(
            search,
            selected_categories,
            selected_source,
            approval,
            selected_tags,
            sort_name,
            "desc" if sort == sort_name and direction == "asc" else "asc",
            1,
        ),
        "page": page,
        "page_size": page_size,
        "total_count": total_count,
        "total_pages": total_pages,
        "page_start": offset + 1 if total_count else 0,
        "page_end": min(offset + page_size, total_count),
    }


def rules_select_base(*columns):
    """Return the base rule listing selectable with merchant labels joined."""
    return select(*columns).select_from(
        category_rules_table
        .outerjoin(
            accounts_table,
            accounts_table.c.id == category_rules_table.c.account_id,
        )
        .outerjoin(
            merchants_table,
            merchants_table.c.id == category_rules_table.c.merchant_id,
        )
    )


def resolve_rules_sort(sort):
    """Return the normalized sort name and Core expression for rule listings."""
    source_priority = case(
        (category_rules_table.c.source == CATEGORY_RULE_SOURCE_MANUAL, 0),
        (category_rules_table.c.source == CATEGORY_RULE_SOURCE_AUTOMATIC, 1),
        else_=2,
    )
    sort_columns = {
        "keyword": category_rules_table.c.keyword,
        "category": category_rules_table.c.category,
        "amount_min": category_rules_table.c.amount_min,
        "amount_max": category_rules_table.c.amount_max,
        "direction": category_rules_table.c.direction,
        "source": source_priority,
        "created_at": category_rules_table.c.created_at,
    }
    sort = str(sort or "source").strip()
    if sort not in sort_columns:
        sort = "source"

    return sort, sort_columns[sort]


def build_rule_filters(search, approval, selected_categories, selected_source, selected_tags):
    """Build Core conditions for the rule listing filters."""
    filters = []
    if search:
        filters.append(rule_search_filter(search))

    if approval == RULE_APPROVAL_FILTER_APPROVED:
        filters.append(category_rules_table.c.source == CATEGORY_RULE_SOURCE_AUTOMATIC)
        filters.append(category_rules_table.c.ai_approved == 1)
    elif approval == RULE_APPROVAL_FILTER_SUGGESTED:
        filters.append(category_rules_table.c.source == CATEGORY_RULE_SOURCE_AUTOMATIC)
        filters.append(category_rules_table.c.ai_approved == 0)

    if selected_categories:
        filters.append(category_rules_table.c.category.in_(selected_categories))
    if selected_source:
        filters.append(category_rules_table.c.source == selected_source)
    if selected_tags:
        filters.append(rule_tag_filter(selected_tags))

    return filters


def rule_search_filter(search):
    """Return a case-insensitive Core search condition for rule rows."""
    pattern = f"%{search.lower()}%"
    approval_text = case(
        (category_rules_table.c.ai_approved == 1, literal("approved")),
        else_=literal("not approved"),
    )
    expressions = (
        category_rules_table.c.keyword,
        func.coalesce(merchants_table.c.merchant_key, ""),
        category_rules_table.c.category,
        category_rules_table.c.source,
        approval_text,
        category_rules_table.c.created_at,
        cast(category_rules_table.c.amount_min, String),
        cast(category_rules_table.c.amount_max, String),
        category_rules_table.c.direction,
        func.coalesce(accounts_table.c.name, ""),
    )
    return or_(*[func.lower(expression).like(pattern) for expression in expressions])


def rule_tag_filter(selected_tags):
    """Return a Core EXISTS condition for selected rule tags."""
    return rule_tag_condition(selected_tags, category_rules_table.c.id)


def rules_count(conn, filters):
    """Return the count of rules matching the listing filters."""
    query = rules_select_base(func.count().label("count"))
    query = apply_rule_filters(query, filters)
    return conn.execute(query).scalar_one()


def rule_rows(conn, filters, sort_expression, direction, page_size, offset):
    """Return paginated rule rows matching the listing filters."""
    query = rules_select_base(
        category_rules_table.c.id,
        category_rules_table.c.account_id,
        accounts_table.c.name.label("account_name"),
        category_rules_table.c.merchant_id,
        merchants_table.c.merchant_key.label("merchant_name"),
        category_rules_table.c.keyword,
        category_rules_table.c.category,
        category_rules_table.c.amount_min,
        category_rules_table.c.amount_max,
        category_rules_table.c.direction,
        category_rules_table.c.source,
        category_rules_table.c.ai_approved,
        category_rules_table.c.created_at,
    )
    query = apply_rule_filters(query, filters)
    query = query.order_by(
        sort_expression.desc() if direction == "desc" else sort_expression.asc(),
        func.lower(category_rules_table.c.category),
        category_rules_table.c.category,
        func.lower(category_rules_table.c.keyword),
        category_rules_table.c.keyword,
    ).limit(page_size).offset(offset)

    rows = [dict(row) for row in conn.execute(query).mappings().fetchall()]
    for row in rows:
        if row["amount_min"] is not None:
            row["amount_min"] = money_to_float(row["amount_min"])
        if row["amount_max"] is not None:
            row["amount_max"] = money_to_float(row["amount_max"])
    return rows


def apply_rule_filters(query, filters):
    """Apply non-empty Core rule listing filters to a query."""
    return query.where(*[condition for condition in filters if condition is not None])


def decorate_rule_rows(conn, rows):
    """Attach tag and display metadata expected by the rules template."""
    tags_by_rule_id = get_rule_tags_by_rule_id(conn, [row["id"] for row in rows])
    tag_colors = get_tag_color_map(conn)
    transaction_reference_counts = count_rule_transaction_references_by_rule_id(
        conn,
        [row["id"] for row in rows],
    )
    for row in rows:
        row["tags"] = tags_by_rule_id.get(row["id"], [])
        row["tag_label"] = ", ".join(row["tags"])
        row["tag_pills"] = [
            {
                "name": tag,
                "color": tag_colors.get(tag, "#64748b"),
            }
            for tag in row["tags"]
        ]
        row["source_label"] = rule_source_label(row["source"])
        row["source_badge_class"] = rule_source_badge_class(row["source"])
        row["direction_label"] = rule_direction_label(row["direction"])
        row["requires_approval"] = (
            row["source"] == CATEGORY_RULE_SOURCE_AUTOMATIC
            and row["ai_approved"] == 0
        )
        row["approval_label"] = "Approved" if row["ai_approved"] else "Suggested"
        row["approval_badge_class"] = "text-bg-success" if row["ai_approved"] else "text-bg-warning"
        row["transaction_reference_count"] = transaction_reference_counts.get(row["id"], 0)
        row["can_delete_without_preview"] = row["transaction_reference_count"] == 0


def rules_list_url(search, selected_categories, selected_source, approval, selected_tags, sort, direction, page):
    """Build a rules list URL while preserving filter state."""
    params = {
        "search": search,
        "categories": selected_categories,
        "source": selected_source,
        "approval": approval,
        "tags": selected_tags,
        "sort": sort,
        "direction": direction,
        "page": page,
    }
    cleaned = {}
    for key, value in params.items():
        if isinstance(value, (list, tuple)):
            values = [item for item in value if item not in (None, "")]
            if values:
                cleaned[key] = values
        elif value not in (None, ""):
            cleaned[key] = value

    query = urlencode(cleaned, doseq=True)
    return url_for("rules.rules") + (f"?{query}" if query else "")


def rule_source_label(source):
    """Return the display label for a rule source."""
    return {
        CATEGORY_RULE_SOURCE_MANUAL: "Manual",
        CATEGORY_RULE_SOURCE_AUTOMATIC: "Auto",
    }.get(source, str(source or "").strip() or "Unknown")


def rule_source_badge_class(source):
    """Return the Bootstrap badge class for a rule source."""
    return {
        CATEGORY_RULE_SOURCE_MANUAL: "text-bg-primary",
        CATEGORY_RULE_SOURCE_AUTOMATIC: "text-bg-info",
    }.get(source, "text-bg-secondary")


def rule_direction_label(direction):
    """Return the display label for a rule direction constraint."""
    return CATEGORY_RULE_DIRECTION_LABELS.get(
        direction or CATEGORY_RULE_DIRECTION_ANY,
        CATEGORY_RULE_DIRECTION_LABELS[CATEGORY_RULE_DIRECTION_ANY],
    )


def account_option_rows(conn):
    """Return available account constraints for rule forms."""
    return [
        dict(row)
        for row in conn.execute(
            select(accounts_table.c.id, accounts_table.c.name)
            .order_by(func.lower(accounts_table.c.name), accounts_table.c.name)
        ).mappings().fetchall()
    ]
