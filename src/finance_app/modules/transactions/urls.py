"""URL builders for the transactions feature."""

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from flask import request, url_for

from finance_app.core.periods import PERIOD_CUSTOM
from finance_app.modules.transactions.constants import IGNORED_FILTER_ACTIVE, TRANSACTION_SORT_DATE


def transactions_url(**overrides: object) -> str:
    """Build a transactions URL preserving current query parameters."""
    query = request.args.to_dict(flat=False)
    for key, value in overrides.items():
        if value in (None, ""):
            query.pop(key, None)
        elif isinstance(value, (list, tuple)):
            query[key] = [str(item) for item in value if item not in (None, "")]
        else:
            query[key] = [str(value)]

    encoded_query = urlencode(query, doseq=True)
    return url_for("transactions.transactions") + (f"?{encoded_query}" if encoded_query else "")


def transactions_date_range_url(
    date_from: str,
    date_to: str,
    account_id: int | None = None,
    merchant_search: str = "",
) -> str:
    """Build a transactions URL for an inclusive date range."""
    params: dict[str, object] = {
        "period": PERIOD_CUSTOM,
        "date_from": date_from,
        "date_to": date_to,
        "ignored": IGNORED_FILTER_ACTIVE,
    }
    if account_id:
        params["account_id"] = account_id
    if merchant_search:
        params["search"] = merchant_search
    query = urlencode(params)
    return f"{url_for('transactions.transactions')}?{query}"


def transactions_sort_url(sort_name: str, current_sort: str, current_direction: str) -> str:
    """Build a transactions URL for toggling one table sort."""
    default_direction = "desc" if sort_name == TRANSACTION_SORT_DATE else "asc"
    next_direction = (
        ("desc" if current_direction == "asc" else "asc") if current_sort == sort_name else default_direction
    )
    return transactions_url(sort=sort_name, direction=next_direction, page=1)


def transactions_redirect_target() -> str:
    """Return the safe post-action transactions redirect target."""
    target = request.form.get("next", "").strip()
    if target.startswith("/transactions"):
        return target

    return url_for("transactions.transactions")


def transactions_redirect_with_ignored(target: str | None, ignored: str) -> str:
    """Return a redirect URL with an updated ignored filter."""
    target = target or url_for("transactions.transactions")
    parts = urlsplit(target)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query["ignored"] = ignored
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))
