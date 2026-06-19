"""Taxonomy report target metadata for Reports detail pages.

This module resolves category and tag targets into a shared read-only model for
Reports services, presenters, and queries. It centralizes built-in taxonomy
semantics so report pages can react to protected categories and tags without
scattered label checks.
"""

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select

from finance_app.database.tables import categories as categories_table
from finance_app.database.tables import tags as tags_table
from finance_app.modules.categories.builtins import (
    BUILTIN_CATEGORY_REIMBURSEMENT,
    BUILTIN_CATEGORY_RENTAL,
    BUILTIN_TAG_REIMBURSABLE,
    BUILTIN_TAG_TAX,
)

TAXONOMY_TARGET_CATEGORY = "category"
TAXONOMY_TARGET_TAG = "tag"


@dataclass(frozen=True)
class TaxonomyReportTarget:
    """Represent a category or tag selected for a Reports detail page."""

    kind: str
    id: int
    name: str
    builtin_key: str
    description: str
    color: str

    @property
    def type_label(self) -> str:
        """Return the UI label for the target kind."""
        return "Category" if self.kind == TAXONOMY_TARGET_CATEGORY else "Tag"

    @property
    def report_label(self) -> str:
        """Return the UI label for the detail report."""
        return "Category report" if self.kind == TAXONOMY_TARGET_CATEGORY else "Tag report"

    @property
    def composition_title(self) -> str:
        """Return the composition table heading for this target."""
        return "Tag composition" if self.kind == TAXONOMY_TARGET_CATEGORY else "Category composition"

    @property
    def composition_label_heading(self) -> str:
        """Return the composition row heading for this target."""
        return "Tag" if self.kind == TAXONOMY_TARGET_CATEGORY else "Category"

    @property
    def composition_row_kind(self) -> str:
        """Return the row-kind key used for transaction handoff links."""
        return "tag" if self.kind == TAXONOMY_TARGET_CATEGORY else "category"

    @property
    def is_tag(self) -> bool:
        """Return whether this target is a tag."""
        return self.kind == TAXONOMY_TARGET_TAG

    @property
    def is_reimbursement_category(self) -> bool:
        """Return whether this target is the built-in Reimbursement category."""
        return self.kind == TAXONOMY_TARGET_CATEGORY and self.builtin_key == BUILTIN_CATEGORY_REIMBURSEMENT

    @property
    def is_rental_category(self) -> bool:
        """Return whether this target is the built-in Rental category."""
        return self.kind == TAXONOMY_TARGET_CATEGORY and self.builtin_key == BUILTIN_CATEGORY_RENTAL

    @property
    def is_reimbursable_tag(self) -> bool:
        """Return whether this target is the built-in Reimbursable tag."""
        return self.kind == TAXONOMY_TARGET_TAG and self.builtin_key == BUILTIN_TAG_REIMBURSABLE

    @property
    def is_tax_tag(self) -> bool:
        """Return whether this target is the built-in Tax tag."""
        return self.kind == TAXONOMY_TARGET_TAG and self.builtin_key == BUILTIN_TAG_TAX

    @property
    def export_stem(self) -> str:
        """Return a stable filename stem for target exports."""
        return "-".join(
            part
            for part in (
                "reports",
                self.kind,
                slugify_taxonomy_name(self.name) or str(self.id),
            )
            if part
        )


def resolve_taxonomy_report_target(conn: Any, kind: str, target_id: int) -> TaxonomyReportTarget | None:
    """Return a category or tag target by id, or ``None`` when missing."""
    if kind == TAXONOMY_TARGET_CATEGORY:
        row = (
            conn.execute(
                select(
                    categories_table.c.id,
                    categories_table.c.name,
                    categories_table.c.builtin_key,
                    categories_table.c.description,
                ).where(categories_table.c.id == target_id)
            )
            .mappings()
            .fetchone()
        )
        if row is None:
            return None
        return TaxonomyReportTarget(
            kind=TAXONOMY_TARGET_CATEGORY,
            id=int(row["id"]),
            name=str(row["name"]),
            builtin_key=str(row["builtin_key"] or ""),
            description=str(row["description"] or ""),
            color="",
        )

    if kind == TAXONOMY_TARGET_TAG:
        row = (
            conn.execute(
                select(
                    tags_table.c.id,
                    tags_table.c.name,
                    tags_table.c.builtin_key,
                    tags_table.c.description,
                    tags_table.c.color,
                ).where(tags_table.c.id == target_id)
            )
            .mappings()
            .fetchone()
        )
        if row is None:
            return None
        return TaxonomyReportTarget(
            kind=TAXONOMY_TARGET_TAG,
            id=int(row["id"]),
            name=str(row["name"]),
            builtin_key=str(row["builtin_key"] or ""),
            description=str(row["description"] or ""),
            color=str(row["color"] or ""),
        )

    return None


def slugify_taxonomy_name(name: object) -> str:
    """Return a conservative ASCII slug for a taxonomy report filename."""
    slug_parts: list[str] = []
    current = []
    for char in str(name or "").lower():
        if char.isascii() and char.isalnum():
            current.append(char)
        elif current:
            slug_parts.append("".join(current))
            current = []
    if current:
        slug_parts.append("".join(current))
    return "-".join(slug_parts)
