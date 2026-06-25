"""Account and merchant report target metadata for Reports pages.

The module resolves durable account and merchant rows into a shared read-only
target model used by Reports services, presenters, routes, and exports.
"""

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select

from finance_app.core.constants import ACCOUNT_TYPES
from finance_app.database.tables import accounts as accounts_table
from finance_app.database.tables import merchants as merchants_table
from finance_app.modules.reports.taxonomy import slugify_taxonomy_name

REPORT_ENTITY_ACCOUNT = "account"
REPORT_ENTITY_MERCHANT = "merchant"


@dataclass(frozen=True)
class ReportEntityTarget:
    """Represent an account or merchant selected for a Reports detail page."""

    kind: str
    id: int
    name: str
    type_label: str
    description: str = ""

    @property
    def report_label(self) -> str:
        """Return the UI label for the detail report."""
        return "Account report" if self.kind == REPORT_ENTITY_ACCOUNT else "Merchant report"

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


def resolve_account_report_target(conn: Any, account_id: int) -> ReportEntityTarget | None:
    """Return an account report target by id, or ``None`` when missing."""
    row = (
        conn.execute(
            select(
                accounts_table.c.id,
                accounts_table.c.name,
                accounts_table.c.account_type,
            ).where(accounts_table.c.id == account_id)
        )
        .mappings()
        .fetchone()
    )
    if row is None:
        return None

    account_type = str(row["account_type"] or "")
    return ReportEntityTarget(
        kind=REPORT_ENTITY_ACCOUNT,
        id=int(row["id"]),
        name=str(row["name"]),
        type_label=ACCOUNT_TYPES.get(account_type, "Account"),
        description=ACCOUNT_TYPES.get(account_type, ""),
    )


def resolve_merchant_report_target(conn: Any, merchant_id: int) -> ReportEntityTarget | None:
    """Return a merchant report target by id, or ``None`` when missing."""
    row = (
        conn.execute(
            select(
                merchants_table.c.id,
                merchants_table.c.merchant_key,
            ).where(merchants_table.c.id == merchant_id)
        )
        .mappings()
        .fetchone()
    )
    if row is None:
        return None

    return ReportEntityTarget(
        kind=REPORT_ENTITY_MERCHANT,
        id=int(row["id"]),
        name=str(row["merchant_key"]),
        type_label="Merchant",
    )
