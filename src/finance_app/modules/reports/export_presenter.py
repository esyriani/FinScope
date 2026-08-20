"""Flattened export row builders for Reports views.

These helpers convert prepared Reports view models into the shared row shape
used by CSV and spreadsheet exports.
"""

from collections.abc import Mapping
from typing import Any

from finance_app.modules.reports.entities import REPORT_ENTITY_ACCOUNT, REPORT_ENTITY_MERCHANT


def build_export_rows(context: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Return flattened overview rows for CSV and Excel exports."""
    rows: list[dict[str, Any]] = [
        export_row("Summary", "Spending", context["total_spending"], "", "", context["transaction_count"]),
        export_row("Summary", "Income and credits", "", context["total_income"], "", context["transaction_count"]),
        export_row("Summary", "Net cash flow", "", "", context["net_cashflow"], context["transaction_count"]),
    ]
    for section, key in (
        ("Monthly", "monthly_rows"),
        ("Category", "category_rows"),
        ("Tag", "tag_rows"),
        ("Account", "account_rows"),
        ("Merchant", "merchant_rows"),
    ):
        for row in context[key]:
            rows.append(
                export_row(
                    section,
                    row["label"],
                    row["spending"],
                    row["income"],
                    row["net"],
                    row["transaction_count"],
                    row.get("share", ""),
                )
            )
    return rows


def build_taxonomy_export_rows(context: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Return flattened taxonomy detail rows for CSV and Excel exports."""
    target = context["taxonomy_target"]
    rows: list[dict[str, Any]] = [
        export_row(target.report_label, "Spending", context["total_spending"], "", "", context["transaction_count"]),
        export_row(
            target.report_label,
            "Income and credits",
            "",
            context["total_income"],
            "",
            context["transaction_count"],
        ),
        export_row(target.report_label, "Net cash flow", "", "", context["net_cashflow"], context["transaction_count"]),
    ]
    for section, key in (
        ("Monthly", "monthly_rows"),
        (target.composition_title, "taxonomy_composition_rows"),
        ("Account", "account_rows"),
        ("Merchant", "merchant_rows"),
    ):
        for row in context[key]:
            rows.append(
                export_row(
                    section,
                    row["label"],
                    row["spending"],
                    row["income"],
                    row["net"],
                    row["transaction_count"],
                    row.get("share", ""),
                )
            )
    for row in context["taxonomy_evidence_rows"]:
        rows.append(
            export_row(
                "Evidence",
                row["description"],
                row["spending"],
                row["income"],
                row["net"],
                1,
            )
        )
    return rows


def build_entity_export_rows(context: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Return flattened account or merchant detail rows for CSV and Excel exports."""
    target = context["entity_target"]
    rows: list[dict[str, Any]] = [
        export_row(target.report_label, "Spending", context["total_spending"], "", "", context["transaction_count"]),
        export_row(
            target.report_label,
            "Income and credits",
            "",
            context["total_income"],
            "",
            context["transaction_count"],
        ),
        export_row(target.report_label, "Net cash flow", "", "", context["net_cashflow"], context["transaction_count"]),
    ]
    for section, key in (
        ("Monthly", "monthly_rows"),
        ("Category", "category_rows"),
        ("Tag", "tag_rows"),
        ("Account", "account_rows"),
        ("Merchant", "merchant_rows"),
    ):
        if target.kind == REPORT_ENTITY_ACCOUNT and key == "account_rows":
            continue
        if target.kind == REPORT_ENTITY_MERCHANT and key == "merchant_rows":
            continue
        for row in context[key]:
            rows.append(
                export_row(
                    section,
                    row["label"],
                    row["spending"],
                    row["income"],
                    row["net"],
                    row["transaction_count"],
                    row.get("share", ""),
                )
            )
    for row in context["entity_evidence_rows"]:
        rows.append(
            export_row(
                "Evidence",
                row["description"],
                row["spending"],
                row["income"],
                row["net"],
                1,
            )
        )
    return rows


def build_income_export_rows(context: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Return flattened income and credits rows for CSV and Excel exports."""
    rows: list[dict[str, Any]] = [
        export_row(
            "Income and credits", "Income and credits", "", context["total_income"], "", context["transaction_count"]
        ),
        export_row(
            "Income and credits", "Net cash flow", "", "", context["net_cashflow"], context["transaction_count"]
        ),
        export_row("Income and credits", "Average credit", "", context["average_income_credit"], "", ""),
    ]
    for section, key in (
        ("Monthly", "monthly_rows"),
        ("Category", "category_rows"),
        ("Tag", "tag_rows"),
        ("Account", "account_rows"),
        ("Merchant", "merchant_rows"),
    ):
        for row in context[key]:
            rows.append(
                export_row(
                    section,
                    row["label"],
                    row["spending"],
                    row["income"],
                    row["net"],
                    row["transaction_count"],
                    row.get("share", ""),
                )
            )
    for row in context["income_evidence_rows"]:
        rows.append(
            export_row(
                "Evidence",
                row["description"],
                row["spending"],
                row["income"],
                row["net"],
                1,
            )
        )
    return rows


def export_row(
    section: str,
    label: str,
    spending: object,
    income: object,
    net: object,
    transactions: object,
    share: object = "",
) -> dict[str, Any]:
    """Return one normalized export row."""
    return {
        "section": section,
        "label": label,
        "spending": spending,
        "income_and_credits": income,
        "net_cash_flow": net,
        "transactions": transactions,
        "share_percent": share,
    }
