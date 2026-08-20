"""View-model helpers for uploaded statement pages.

The upload service fetches statement rows and owns workflow decisions; this
module shapes those rows for templates without opening database connections.
"""

from typing import Any

from finance_app.core.constants import DATE_ORDER_AUTO, DATE_ORDERS


def present_statement(statement: Any) -> dict[str, Any]:
    """Return a template-friendly representation of an uploaded statement row.

    The statement list only needs a bounded preview of the stored text. The full
    statement text can be large, so the query intentionally selects only a
    prefix and the total size.
    """
    row = dict(statement)
    raw_text_preview = row.get("raw_text_preview") or ""
    raw_text_size = row.get("raw_text_size") or 0
    row["raw_text_preview"] = raw_text_preview
    row["raw_text_truncated"] = raw_text_size > len(raw_text_preview)
    row["extension_label"] = (row.get("extension") or "").upper() or "n/a"
    row["date_order_label"] = DATE_ORDERS.get(row.get("date_order") or DATE_ORDER_AUTO, DATE_ORDERS[DATE_ORDER_AUTO])
    return row
