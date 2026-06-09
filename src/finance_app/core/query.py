"""SQLAlchemy Core query helper utilities.

Provides small parsing and filter helpers shared by route and service layers.
Callers build SQLAlchemy Core expressions directly and pass them into
repository/query functions.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol


class QueryArgs(Protocol):
    """Represent the request-argument methods used by parser helpers."""

    def get(self, key: str, default: object | None = None) -> object:
        """Return one query value for a key."""
        ...

    def getlist(self, key: str) -> list[object]:
        """Return all query values for a repeated key."""
        ...


def query_value(args: QueryArgs, key: str, default: str = "") -> str:
    """Return one query argument as normalized text."""
    value = args.get(key, default)
    return default if value in (None, "") else str(value)


def query_values(args: QueryArgs, key: str) -> list[str]:
    """Return repeated query arguments as normalized text values."""
    return [str(value) for value in args.getlist(key)]


@dataclass
class CoreFilters:
    """Collect SQLAlchemy Core filter criteria for query composition."""

    conditions: list[Any] = field(default_factory=list)

    def add(self, condition: Any | None) -> None:
        """Add a SQLAlchemy condition when one is present."""
        if condition is not None:
            self.conditions.append(condition)

    def add_in(self, column: Any, values: Iterable[Any], include: bool = True) -> None:
        """Add an IN or NOT IN condition for non-empty values."""
        values = [value for value in values if value not in (None, "")]
        if not values:
            return

        condition = column.in_(values)
        self.add(condition if include else ~condition)

    def clone(self) -> "CoreFilters":
        """Return a copy of the current filter set."""
        return CoreFilters(conditions=list(self.conditions))

    def criteria(self) -> tuple[Any, ...]:
        """Return filter criteria as a tuple suitable for query.where()."""
        return tuple(self.conditions)


def parse_sort_direction(value: object, default: str = "asc") -> str:
    """Return a supported sort direction."""
    direction = str(value or default).strip().lower()
    return direction if direction in {"asc", "desc"} else default


def parse_page(value: Any) -> int:
    """Return a positive one-based page number."""
    try:
        page = int(value)
    except (TypeError, ValueError):
        return 1

    return max(1, page)


def resolve_sort(sort: object, allowed_columns: Mapping[str, Any], default_sort: str) -> tuple[str, Any]:
    """Return a sort key and SQLAlchemy expression from an allow-list."""
    sort = str(sort or default_sort).strip()
    if sort not in allowed_columns:
        sort = default_sort

    return sort, allowed_columns[sort]
