"""Filter parsing helpers for the transactions feature."""

from sqlalchemy import String, case, cast, false, func, or_, select

from finance_app.core.constants import (
    CATEGORY_SOURCE_AI,
    CATEGORY_SOURCE_HISTORY,
    CATEGORY_SOURCE_RULE,
    FILTER_MODE_INCLUDE,
    FILTER_MODES,
    TRANSACTION_KIND_INCOME,
    TRANSACTION_KIND_PAYMENT,
    TRANSACTION_KIND_TRANSFER,
)
from finance_app.core.periods import DEFAULT_DATE_PERIOD, normalize_date_period, parse_iso_date, period_start_date
from finance_app.core.reporting import spending_impact_clause
from finance_app.core.query import CoreFilters, parse_page, parse_sort_direction, resolve_sort
from finance_app.database.tables import (
    accounts as accounts_table,
    merchants as merchants_table,
    transactions as transactions_table,
)
from finance_app.modules.categories.tag_filters import transaction_tag_condition
from finance_app.modules.merchants.normalization import canonicalize_merchant_key
from finance_app.modules.merchants.repository import merchant_identity_from_row
from finance_app.modules.merchants.sql_filters import (
    description_matches_any_candidate,
    merchant_identity_candidates,
)
from finance_app.modules.transactions.constants import (
    AMOUNT_TYPE_FILTERS,
    AMOUNT_TYPE_CREDIT,
    AMOUNT_TYPE_INCOME,
    AMOUNT_TYPE_PAYMENT,
    AMOUNT_TYPE_SPENDING,
    AMOUNT_TYPE_TRANSFER,
    CATEGORY_SOURCE_FILTER_MANUAL_REVIEWED,
    CATEGORY_STATUS_CATEGORIZED,
    CATEGORY_STATUS_FILTERS,
    CATEGORY_STATUS_UNKNOWN,
    IGNORED_FILTER_ACTIVE,
    IGNORED_FILTER_IGNORED,
    IGNORED_FILTERS,
    REVIEW_FILTER_NEEDS_REVIEW,
    REVIEW_FILTER_READY_TO_APPROVE,
    REVIEW_FILTERS,
    REVIEW_FILTER_VERIFIED,
    TRANSACTION_SORT_ACCOUNT,
    TRANSACTION_SORT_AMOUNT,
    TRANSACTION_SORT_CATEGORY,
    TRANSACTION_SORT_DATE,
    TRANSACTION_SORT_DESCRIPTION,
    TRANSACTION_SORT_IGNORED,
    TRANSACTION_SORT_REVIEW,
)


def parse_transaction_filters(args, conn):
    """Parse transaction filters."""
    selected_categories = [
        value.strip()
        for value in args.getlist("categories")
        if value.strip()
    ]
    selected_tags = [
        value.strip()
        for value in args.getlist("tags")
        if value.strip()
    ]
    filter_mode = args.get("filter_mode", FILTER_MODE_INCLUDE).strip()
    if filter_mode not in FILTER_MODES:
        filter_mode = FILTER_MODE_INCLUDE

    category_status = args.get("category_status", "").strip()
    if category_status not in CATEGORY_STATUS_FILTERS:
        category_status = ""

    category_source = args.get("category_source", "").strip()
    if category_source not in {
        "",
        CATEGORY_SOURCE_FILTER_MANUAL_REVIEWED,
        CATEGORY_SOURCE_RULE,
        CATEGORY_SOURCE_HISTORY,
        CATEGORY_SOURCE_AI,
    }:
        category_source = ""

    amount_type = args.get("amount_type", "").strip()
    if amount_type not in AMOUNT_TYPE_FILTERS:
        amount_type = ""

    ignored = args.get("ignored", IGNORED_FILTER_ACTIVE).strip()
    if ignored not in IGNORED_FILTERS:
        ignored = IGNORED_FILTER_ACTIVE

    period = normalize_date_period(args.get("period", DEFAULT_DATE_PERIOD).strip())

    review = args.get("review", "").strip()
    if review not in REVIEW_FILTERS:
        review = ""

    return {
        "search": args.get("search", "").strip(),
        "category": args.get("category", "").strip(),
        "selected_categories": selected_categories,
        "selected_tags": selected_tags,
        "filter_mode": filter_mode,
        "review": review,
        "category_status": category_status,
        "category_source": category_source,
        "amount_type": amount_type,
        "merchant": args.get("merchant", "").strip(),
        "merchant_key": canonicalize_merchant_key(args.get("merchant_key", ""), conn=conn),
        "date_from": parse_iso_date(args.get("date_from")),
        "date_to": parse_iso_date(args.get("date_to")),
        "ignored": ignored,
        "period": period,
        "sort": args.get("sort", "date").strip(),
        "direction": parse_sort_direction(args.get("direction"), default="desc"),
        "page": parse_page(args.get("page")),
    }


def transaction_sort(filters, unknown_category):
    """Build transaction sort metadata."""
    sort_columns = {
        TRANSACTION_SORT_DATE: transactions_table.c.tx_date,
        TRANSACTION_SORT_ACCOUNT: func.coalesce(accounts_table.c.name, "Personal"),
        TRANSACTION_SORT_DESCRIPTION: transactions_table.c.description,
        TRANSACTION_SORT_AMOUNT: transactions_table.c.amount,
        TRANSACTION_SORT_CATEGORY: func.coalesce(transactions_table.c.category, unknown_category),
        TRANSACTION_SORT_REVIEW: case(
            (transactions_table.c.needs_review == 1, 2),
            (transactions_table.c.reviewed_at.is_(None), 1),
            else_=0,
        ),
        TRANSACTION_SORT_IGNORED: transactions_table.c.ignored,
    }
    return resolve_sort(filters["sort"], sort_columns, TRANSACTION_SORT_DATE)


def build_transaction_core_filters(filters, unknown_category, conn=None):
    """Build transaction SQLAlchemy Core filters."""
    category_value = func.coalesce(transactions_table.c.category, unknown_category)
    core_filters = CoreFilters()

    start_date = period_start_date(filters["period"])
    if start_date:
        core_filters.add(transactions_table.c.tx_date >= start_date)

    core_filters.add(search_condition(filters["search"], unknown_category))
    core_filters.add_in(
        category_value,
        filters["selected_categories"],
        include=filters["filter_mode"] == FILTER_MODE_INCLUDE,
    )
    core_filters.add(
        transaction_tag_condition(
            filters["selected_tags"],
            include=filters["filter_mode"] == FILTER_MODE_INCLUDE,
        )
    )

    if filters["category"]:
        if filters["category"] == unknown_category:
            core_filters.add(category_value == unknown_category)
        else:
            core_filters.add(transactions_table.c.category == filters["category"])

    if filters["category_status"] == CATEGORY_STATUS_UNKNOWN:
        core_filters.add(category_value == unknown_category)
    elif filters["category_status"] == CATEGORY_STATUS_CATEGORIZED:
        core_filters.add(category_value != unknown_category)
        core_filters.add(transactions_table.c.needs_review == 0)

    if filters["review"] == REVIEW_FILTER_NEEDS_REVIEW:
        core_filters.add(transactions_table.c.needs_review == 1)
    elif filters["review"] == REVIEW_FILTER_READY_TO_APPROVE:
        core_filters.add(transactions_table.c.needs_review == 0)
        core_filters.add(transactions_table.c.reviewed_at.is_(None))
    elif filters["review"] == REVIEW_FILTER_VERIFIED:
        core_filters.add(transactions_table.c.reviewed_at.is_not(None))

    if filters["category_source"] == CATEGORY_SOURCE_FILTER_MANUAL_REVIEWED:
        core_filters.add(transactions_table.c.reviewed_at.is_not(None))
    elif filters["category_source"]:
        core_filters.add(transactions_table.c.category_source == category_source_value(filters["category_source"]))

    if filters["amount_type"] == AMOUNT_TYPE_SPENDING:
        core_filters.add(spending_impact_clause())
    elif filters["amount_type"] == AMOUNT_TYPE_INCOME:
        core_filters.add(transactions_table.c.amount < 0)
        core_filters.add(transactions_table.c.transaction_kind == TRANSACTION_KIND_INCOME)
    elif filters["amount_type"] == AMOUNT_TYPE_CREDIT:
        core_filters.add(transactions_table.c.amount < 0)
        core_filters.add(
            transactions_table.c.transaction_kind.in_(
                (TRANSACTION_KIND_INCOME, TRANSACTION_KIND_TRANSFER)
            )
        )
    elif filters["amount_type"] == AMOUNT_TYPE_PAYMENT:
        core_filters.add(transactions_table.c.transaction_kind == TRANSACTION_KIND_PAYMENT)
    elif filters["amount_type"] == AMOUNT_TYPE_TRANSFER:
        core_filters.add(transactions_table.c.transaction_kind == TRANSACTION_KIND_TRANSFER)

    if filters["merchant"]:
        core_filters.add(transactions_table.c.description == filters["merchant"])

    if filters["merchant_key"]:
        core_filters.add(merchant_key_condition(conn, filters["merchant_key"]))

    if filters["date_from"]:
        core_filters.add(transactions_table.c.tx_date >= filters["date_from"])
    if filters["date_to"]:
        core_filters.add(transactions_table.c.tx_date <= filters["date_to"])

    if filters["ignored"] == IGNORED_FILTER_ACTIVE:
        core_filters.add(transactions_table.c.ignored == 0)
    elif filters["ignored"] == IGNORED_FILTER_IGNORED:
        core_filters.add(transactions_table.c.ignored == 1)

    return core_filters


def search_condition(search, unknown_category):
    """Return a case-insensitive search condition."""
    text = str(search or "").strip().casefold()
    if not text:
        return None

    pattern = f"%{text}%"
    account_name = func.coalesce(accounts_table.c.name, "Personal")
    category_value = func.coalesce(transactions_table.c.category, unknown_category)
    review_state = case(
        (transactions_table.c.needs_review == 1, "needs review"),
        (transactions_table.c.reviewed_at.is_not(None), "verified"),
        else_="ready to approve",
    )
    ignored_state = case(
        (transactions_table.c.ignored == 1, "ignored"),
        else_="active",
    )
    expressions = (
        transactions_table.c.description,
        account_name,
        category_value,
        review_state,
        ignored_state,
        transactions_table.c.tx_date,
        cast(transactions_table.c.amount, String),
    )
    return or_(
        *[
            func.lower(cast(expression, String)).like(pattern)
            for expression in expressions
        ]
    )


def merchant_key_condition(conn, merchant_key):
    """Return a condition that matches transactions for a canonical merchant key."""
    if conn is None:
        return false()

    transaction_ids = matching_transaction_ids_for_merchant_key(conn, merchant_key)
    if not transaction_ids:
        return false()
    return transactions_table.c.id.in_(transaction_ids)


def matching_transaction_ids_for_merchant_key(conn, merchant_key):
    """Return transaction ids whose resolved merchant name matches a key."""
    merchant_ids, description_candidates = merchant_identity_candidates(conn, merchant_key)
    candidate_conditions = []
    if merchant_ids:
        candidate_conditions.append(transactions_table.c.merchant_id.in_(merchant_ids))
    candidate_conditions.append(
        description_matches_any_candidate(transactions_table.c.description, description_candidates)
    )

    rows = conn.execute(
        select(
            transactions_table.c.id,
            transactions_table.c.description,
            transactions_table.c.merchant_id,
            merchants_table.c.display_name.label("merchant_name"),
            merchants_table.c.canonical_key.label("merchant_canonical_key"),
        )
        .select_from(
            transactions_table.outerjoin(
                merchants_table,
                merchants_table.c.id == transactions_table.c.merchant_id,
            )
        )
        .where(or_(*candidate_conditions))
    ).mappings().fetchall()
    return [
        row["id"]
        for row in rows
        if merchant_identity_from_row(row, conn=conn)["name"] == merchant_key
    ]


def category_source_value(source):
    """Build source value."""
    return {
        CATEGORY_SOURCE_RULE: CATEGORY_SOURCE_RULE,
        CATEGORY_SOURCE_HISTORY: CATEGORY_SOURCE_HISTORY,
        CATEGORY_SOURCE_AI: CATEGORY_SOURCE_AI,
    }[source]
