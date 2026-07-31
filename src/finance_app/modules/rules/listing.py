"""List-page orchestration for the rules feature."""

from collections.abc import Sequence
from typing import Any
from urllib.parse import urlencode

from flask import url_for
from sqlalchemy import String, and_, case, cast, func, literal, or_, select

from finance_app.core.category_sql import category_label_expression
from finance_app.core.config import settings
from finance_app.core.constants import (
    CATEGORY_RULE_DIRECTION_ANY,
    CATEGORY_RULE_DIRECTION_LABELS,
    CATEGORY_RULE_DIRECTIONS,
    CATEGORY_RULE_SOURCE_AUTOMATIC,
    CATEGORY_RULE_SOURCE_MANUAL,
    CATEGORY_RULE_SOURCES,
    UNKNOWN_CATEGORY,
)
from finance_app.core.money import money_to_float
from finance_app.core.query import parse_page, parse_sort_direction
from finance_app.database.engine import db_core_transaction
from finance_app.database.tables import (
    accounts as accounts_table,
)
from finance_app.database.tables import (
    category_rules as category_rules_table,
)
from finance_app.database.tables import (
    merchants as merchants_table,
)
from finance_app.modules.categories.service import get_category_options
from finance_app.modules.categories.tag_filters import rule_tag_condition
from finance_app.modules.categories.taxonomy import (
    get_category_description_map,
    get_rule_tags_by_rule_id,
    get_tag_color_map,
    get_tag_option_rows,
)
from finance_app.modules.merchants.sql_filters import escape_like_token
from finance_app.modules.rules.service import count_rule_transaction_references_by_rule_id
from finance_app.modules.settings.runtime import get_int_setting

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
POST_SAVE_RULE_ACTIONS = {"created", "updated"}


def build_rules_context(args: Any) -> dict[str, Any]:
    """Build rules context."""
    search = args.get("search", "").strip()
    selected_categories = [category.strip() for category in args.getlist("categories") if category.strip()]
    legacy_category = args.get("category", "").strip()
    if legacy_category and legacy_category not in selected_categories:
        selected_categories.append(legacy_category)
    selected_source = args.get("source", "").strip()
    if selected_source not in CATEGORY_RULE_SOURCES:
        selected_source = ""
    selected_tags = [tag.strip() for tag in args.getlist("tags") if tag.strip()]
    approval = args.get("approval", "").strip()
    if approval not in RULE_APPROVAL_FILTERS:
        approval = ""
    sort = args.get("sort", "source").strip()
    direction = parse_sort_direction(args.get("direction"), default="asc")
    page = parse_page(args.get("page"))
    saved_rule_id = parse_optional_int(args.get("saved_rule_id"))
    saved_rule_action = args.get("saved_rule_action", "").strip()

    with db_core_transaction() as conn:
        page_size = get_int_setting(conn, "default_table_page_size", settings.default_table_page_size)
        category_options = get_category_options(conn)
        account_options = account_option_rows(conn)
        category_descriptions = get_category_description_map(conn)
        selected_categories = [category for category in selected_categories if category in category_options]
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
        post_save_rule = post_save_rule_followup(conn, saved_rule_id, saved_rule_action)

    current_rules_url = rules_list_url(
        search,
        selected_categories,
        selected_source,
        approval,
        selected_tags,
        sort,
        direction,
        page,
    )

    return {
        "rules": rows,
        "account_options": account_options,
        "direction_options": [
            (direction, CATEGORY_RULE_DIRECTION_LABELS[direction]) for direction in CATEGORY_RULE_DIRECTIONS
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
        "current_rules_url": current_rules_url,
        "post_save_rule": post_save_rule,
    }


def rules_select_base(*columns: Any) -> Any:
    """Return the base rule listing selectable with merchant labels joined."""
    return select(*columns).select_from(
        category_rules_table.outerjoin(
            accounts_table,
            accounts_table.c.id == category_rules_table.c.account_id,
        ).outerjoin(
            merchants_table,
            merchants_table.c.id == category_rules_table.c.merchant_id,
        )
    )


def resolve_rules_sort(sort: object) -> tuple[str, Any]:
    """Return the normalized sort name and Core expression for rule listings."""
    source_priority = case(
        (category_rules_table.c.source == CATEGORY_RULE_SOURCE_MANUAL, 0),
        (category_rules_table.c.source == CATEGORY_RULE_SOURCE_AUTOMATIC, 1),
        else_=2,
    )
    sort_columns = {
        "keyword": category_rules_table.c.keyword,
        "category": rule_category_label_expression(),
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


def build_rule_filters(
    search: str,
    approval: str,
    selected_categories: Sequence[str],
    selected_source: str,
    selected_tags: Sequence[str],
) -> list[Any]:
    """Build Core conditions for the rule listing filters."""
    filters: list[Any] = []
    if search:
        filters.append(rule_search_filter(search))

    if approval == RULE_APPROVAL_FILTER_APPROVED:
        filters.append(category_rules_table.c.source == CATEGORY_RULE_SOURCE_AUTOMATIC)
        filters.append(category_rules_table.c.ai_approved == 1)
    elif approval == RULE_APPROVAL_FILTER_SUGGESTED:
        filters.append(category_rules_table.c.source == CATEGORY_RULE_SOURCE_AUTOMATIC)
        filters.append(category_rules_table.c.ai_approved == 0)

    if selected_categories:
        filters.append(rule_category_label_expression().in_(selected_categories))
    if selected_source:
        filters.append(category_rules_table.c.source == selected_source)
    if selected_tags:
        filters.append(rule_tag_filter(selected_tags))

    return filters


def rule_search_filter(search: str) -> Any:
    """Return a case-insensitive Core search condition for rule rows."""
    terms = [term for term in search.lower().split() if term]
    approval_text = case(
        (category_rules_table.c.ai_approved == 1, literal("approved")),
        else_=literal("not approved"),
    )
    expressions = (
        category_rules_table.c.keyword,
        func.coalesce(merchants_table.c.merchant_key, ""),
        rule_category_label_expression(),
        category_rules_table.c.source,
        approval_text,
        category_rules_table.c.created_at,
        cast(category_rules_table.c.amount_min, String),
        cast(category_rules_table.c.amount_max, String),
        category_rules_table.c.direction,
        func.coalesce(accounts_table.c.name, ""),
    )
    return and_(
        *[
            or_(
                *[
                    func.lower(cast(expression, String)).like(f"%{escape_like_token(term)}%", escape="\\")
                    for expression in expressions
                ]
            )
            for term in terms
        ]
    )


def rule_tag_filter(selected_tags: Sequence[str]) -> Any:
    """Return a Core EXISTS condition for selected rule tags."""
    return rule_tag_condition(selected_tags, category_rules_table.c.id)


def rules_count(conn: Any, filters: Sequence[Any]) -> int:
    """Return the count of rules matching the listing filters."""
    query = rules_select_base(func.count().label("count"))
    query = apply_rule_filters(query, filters)
    return conn.execute(query).scalar_one()


def rule_rows(
    conn: Any,
    filters: Sequence[Any],
    sort_expression: Any,
    direction: str,
    page_size: int,
    offset: int,
) -> list[dict[str, Any]]:
    """Return paginated rule rows matching the listing filters."""
    query = rules_select_base(
        category_rules_table.c.id,
        category_rules_table.c.account_id,
        accounts_table.c.name.label("account_name"),
        category_rules_table.c.merchant_id,
        merchants_table.c.merchant_key.label("merchant_name"),
        category_rules_table.c.keyword,
        rule_category_label_expression().label("category"),
        category_rules_table.c.amount_min,
        category_rules_table.c.amount_max,
        category_rules_table.c.direction,
        category_rules_table.c.source,
        category_rules_table.c.ai_approved,
        category_rules_table.c.created_at,
    )
    query = apply_rule_filters(query, filters)
    query = (
        query.order_by(
            sort_expression.desc() if direction == "desc" else sort_expression.asc(),
            func.lower(rule_category_label_expression()),
            rule_category_label_expression(),
            func.lower(category_rules_table.c.keyword),
            category_rules_table.c.keyword,
        )
        .limit(page_size)
        .offset(offset)
    )

    rows = [dict(row) for row in conn.execute(query).mappings().fetchall()]
    for row in rows:
        if row["amount_min"] is not None:
            row["amount_min"] = money_to_float(row["amount_min"])
        if row["amount_max"] is not None:
            row["amount_max"] = money_to_float(row["amount_max"])
    return rows


def apply_rule_filters(query: Any, filters: Sequence[Any]) -> Any:
    """Apply non-empty Core rule listing filters to a query."""
    return query.where(*[condition for condition in filters if condition is not None])


def decorate_rule_rows(conn: Any, rows: list[dict[str, Any]]) -> None:
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
        row["requires_approval"] = row["source"] == CATEGORY_RULE_SOURCE_AUTOMATIC and row["ai_approved"] == 0
        row["approval_label"] = "Approved" if row["ai_approved"] else "Suggested"
        row["approval_badge_class"] = "text-bg-success" if row["ai_approved"] else "text-bg-warning"
        row["transaction_reference_count"] = transaction_reference_counts.get(row["id"], 0)
        row["can_delete_without_preview"] = row["transaction_reference_count"] == 0


def post_save_rule_followup(conn: Any, rule_id: int | None, action: str) -> dict[str, Any] | None:
    """Return the rule summary used by the post-save follow-up panel."""
    if rule_id is None:
        return None

    row = (
        conn.execute(
            rules_select_base(
                category_rules_table.c.id,
                category_rules_table.c.keyword,
                rule_category_label_expression().label("category"),
                category_rules_table.c.source,
                category_rules_table.c.ai_approved,
            ).where(category_rules_table.c.id == rule_id)
        )
        .mappings()
        .fetchone()
    )
    if row is None:
        return None

    followup = dict(row)
    followup["action"] = action if action in POST_SAVE_RULE_ACTIONS else "updated"
    followup["source_label"] = rule_source_label(followup["source"])
    followup["source_badge_class"] = rule_source_badge_class(followup["source"])
    followup["approval_label"] = "Approved" if followup["ai_approved"] else "Suggested"
    followup["approval_badge_class"] = "text-bg-success" if followup["ai_approved"] else "text-bg-warning"
    return followup


def rule_category_label_expression() -> Any:
    """Return the canonical category label for category rule rows."""
    return category_label_expression(category_rules_table, UNKNOWN_CATEGORY)


def parse_optional_int(value: object) -> int | None:
    """Return a positive integer from a request value when available."""
    if value in (None, ""):
        return None
    try:
        parsed = int(str(value))
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def rules_list_url(
    search: str,
    selected_categories: Sequence[str],
    selected_source: str,
    approval: str,
    selected_tags: Sequence[str],
    sort: str,
    direction: str,
    page: int,
) -> str:
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
    cleaned: dict[str, Any] = {}
    for key, value in params.items():
        if isinstance(value, (list, tuple)):
            values = [item for item in value if item not in (None, "")]
            if values:
                cleaned[key] = values
        elif value not in (None, ""):
            cleaned[key] = value

    query = urlencode(cleaned, doseq=True)
    return url_for("rules.rules") + (f"?{query}" if query else "")


def rule_source_label(source: object) -> str:
    """Return the display label for a rule source."""
    source_key = str(source or "")
    return {
        CATEGORY_RULE_SOURCE_MANUAL: "Manual",
        CATEGORY_RULE_SOURCE_AUTOMATIC: "Auto",
    }.get(source_key, source_key.strip() or "Unknown")


def rule_source_badge_class(source: object) -> str:
    """Return the Bootstrap badge class for a rule source."""
    source_key = str(source or "")
    return {
        CATEGORY_RULE_SOURCE_MANUAL: "text-bg-primary",
        CATEGORY_RULE_SOURCE_AUTOMATIC: "text-bg-info",
    }.get(source_key, "text-bg-secondary")


def rule_direction_label(direction: object) -> str:
    """Return the display label for a rule direction constraint."""
    direction_key = str(direction or CATEGORY_RULE_DIRECTION_ANY)
    return CATEGORY_RULE_DIRECTION_LABELS.get(
        direction_key,
        CATEGORY_RULE_DIRECTION_LABELS[CATEGORY_RULE_DIRECTION_ANY],
    )


def account_option_rows(conn: Any) -> list[dict[str, Any]]:
    """Return available account constraints for rule forms."""
    return [
        dict(row)
        for row in conn.execute(
            select(accounts_table.c.id, accounts_table.c.name).order_by(
                func.lower(accounts_table.c.name), accounts_table.c.name
            )
        )
        .mappings()
        .fetchall()
    ]
