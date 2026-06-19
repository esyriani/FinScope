"""Report section definitions shared by Reports routes and templates.

The module centralizes the first navigation contract for the Reports area so
later increments can attach filters, queries, presenters, and exports without
duplicating section metadata across controllers and templates.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class ReportSection:
    """Describe one top-level Reports destination."""

    key: str
    label: str
    description: str
    endpoint: str
    icon: str


REPORT_OVERVIEW = "overview"
REPORT_TAXONOMY = "taxonomy"
REPORT_ACCOUNTS = "accounts"
REPORT_MERCHANTS = "merchants"
REPORT_INCOME = "income"


REPORT_SECTIONS: tuple[ReportSection, ...] = (
    ReportSection(
        key=REPORT_OVERVIEW,
        label="Overview",
        description="High-level spending, income, and net cash flow analysis.",
        endpoint="reports.overview",
        icon="pie-chart",
    ),
    ReportSection(
        key=REPORT_TAXONOMY,
        label="Category or tag",
        description="Category and tag analysis.",
        endpoint="reports.taxonomy",
        icon="tags",
    ),
    ReportSection(
        key=REPORT_ACCOUNTS,
        label="Accounts",
        description="Account-level cash flow and ledger views.",
        endpoint="reports.accounts",
        icon="bank",
    ),
    ReportSection(
        key=REPORT_MERCHANTS,
        label="Merchants",
        description="Merchant and counterparty analysis.",
        endpoint="reports.merchants",
        icon="shop",
    ),
    ReportSection(
        key=REPORT_INCOME,
        label="Income and credits",
        description="Incoming money, credits, and reimbursement-aware analysis.",
        endpoint="reports.income",
        icon="cash-coin",
    ),
)

REPORT_SECTION_BY_KEY = {section.key: section for section in REPORT_SECTIONS}


def get_report_section(section_key: str) -> ReportSection:
    """Return the configured report section for a route key."""
    return REPORT_SECTION_BY_KEY[section_key]
