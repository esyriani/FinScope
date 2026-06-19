"""Request parsing helpers for the Reports feature.

The first increment only preserves query-string state for bookmarkable report
shell routes. Later increments will extend this boundary with period, measure,
basis, account, merchant, and target filters.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol


class QueryStringArgs(Protocol):
    """Represent Flask query arguments used by Reports routes."""

    def to_dict(self, flat: bool = True) -> dict[str, object]:
        """Return query parameters as a dictionary."""
        ...


@dataclass(frozen=True)
class ReportRequest:
    """Represent the shared request state for a Reports route."""

    section_key: str
    query: Mapping[str, tuple[str, ...]]


def parse_report_request(section_key: str, args: QueryStringArgs) -> ReportRequest:
    """Normalize non-empty query-string values for a Reports route."""
    query: dict[str, tuple[str, ...]] = {}
    for key, raw_values in args.to_dict(flat=False).items():
        if isinstance(raw_values, (list, tuple)):
            values = tuple(str(value) for value in raw_values if value not in (None, ""))
        elif raw_values in (None, ""):
            values = ()
        else:
            values = (str(raw_values),)
        if values:
            query[str(key)] = values

    return ReportRequest(section_key=section_key, query=query)
