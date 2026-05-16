"""SQLAlchemy Core query helper utilities.

Provides small parsing and filter helpers shared by route and service layers.
Callers build SQLAlchemy Core expressions directly and pass them into
repository/query functions.
"""

from dataclasses import dataclass, field


@dataclass
class CoreFilters:
    """Collect SQLAlchemy Core filter criteria for query composition."""

    conditions: list = field(default_factory=list)

    def add(self, condition):
        """Add a SQLAlchemy condition when one is present."""
        if condition is not None:
            self.conditions.append(condition)

    def add_in(self, column, values, include=True):
        """Add an IN or NOT IN condition for non-empty values."""
        values = [value for value in values if value not in (None, "")]
        if not values:
            return

        condition = column.in_(values)
        self.add(condition if include else ~condition)

    def clone(self):
        """Return a copy of the current filter set."""
        return CoreFilters(conditions=list(self.conditions))

    def criteria(self):
        """Return filter criteria as a tuple suitable for query.where()."""
        return tuple(self.conditions)


def parse_sort_direction(value, default="asc"):
    """Return a supported sort direction."""
    direction = str(value or default).strip().lower()
    return direction if direction in {"asc", "desc"} else default


def parse_page(value):
    """Return a positive one-based page number."""
    try:
        page = int(value)
    except (TypeError, ValueError):
        return 1

    return max(1, page)


def resolve_sort(sort, allowed_columns, default_sort):
    """Return a sort key and SQLAlchemy expression from an allow-list."""
    sort = str(sort or default_sort).strip()
    if sort not in allowed_columns:
        sort = default_sort

    return sort, allowed_columns[sort]
