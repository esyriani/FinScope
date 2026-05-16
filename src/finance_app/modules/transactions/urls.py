"""URL builders for the transactions feature."""

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from flask import request, url_for

from finance_app.modules.transactions.constants import TRANSACTION_SORT_DATE


def transactions_url(**overrides):
    """Render url."""
    query = request.args.to_dict(flat=False)
    for key, value in overrides.items():
        if value in (None, ""):
            query.pop(key, None)
        elif isinstance(value, (list, tuple)):
            query[key] = [item for item in value if item not in (None, "")]
        else:
            query[key] = [value]

    encoded_query = urlencode(query, doseq=True)
    return url_for("transactions.transactions") + (f"?{encoded_query}" if encoded_query else "")


def transactions_sort_url(sort_name, current_sort, current_direction):
    """Render sort URL."""
    default_direction = "desc" if sort_name == TRANSACTION_SORT_DATE else "asc"
    next_direction = (
        "desc" if current_direction == "asc" else "asc"
    ) if current_sort == sort_name else default_direction
    return transactions_url(sort=sort_name, direction=next_direction, page=1)


def transactions_redirect_target():
    """Render redirect target."""
    target = request.form.get("next", "").strip()
    if target.startswith("/transactions"):
        return target

    return url_for("transactions.transactions")


def transactions_redirect_with_ignored(target, ignored):
    """Render redirect with ignored."""
    target = target or url_for("transactions.transactions")
    parts = urlsplit(target)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query["ignored"] = ignored
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))
