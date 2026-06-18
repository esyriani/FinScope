"""Built-in taxonomy semantics for protected categories and tags.

This module defines FinScope-managed taxonomy rows and the behavior policies
attached to them. Persistence layers seed these definitions with stable
``builtin_key`` values, while reporting, rule matching, and taxonomy
administration ask this registry about semantic behavior instead of comparing
display labels throughout the codebase.
"""

from dataclasses import dataclass

from finance_app.core.constants import REIMBURSEMENT_CATEGORY, TRANSFER_CATEGORY, UNKNOWN_CATEGORY

BUILTIN_CATEGORY_UNKNOWN = "unknown"
BUILTIN_CATEGORY_INCOME = "income"
BUILTIN_CATEGORY_REIMBURSEMENT = "reimbursement"
BUILTIN_CATEGORY_RENTAL = "rental"
BUILTIN_CATEGORY_TRANSFERS = "transfers"

BUILTIN_TAG_REIMBURSABLE = "reimbursable"
BUILTIN_TAG_TAX = "tax"


class TaxonomyBehavior:
    """Base behavior policy for a built-in taxonomy row."""

    ordinary_income = False


class UnknownCategoryBehavior(TaxonomyBehavior):
    """Behavior for the category assigned when classification is unresolved."""


class OrdinaryIncomeCategoryBehavior(TaxonomyBehavior):
    """Behavior for ordinary incoming money guidance."""

    ordinary_income = True


class ReimbursementCategoryBehavior(TaxonomyBehavior):
    """Behavior for reimbursement credits that offset allocated expenses."""


class RentalCategoryBehavior(TaxonomyBehavior):
    """Behavior for rental-property taxonomy reserved for future workflows."""


class TransferCategoryBehavior(TaxonomyBehavior):
    """Behavior for internal balance movement and payment rows."""


class ReimbursableTagBehavior(TaxonomyBehavior):
    """Behavior for expenses tracked by the reimbursement workflow."""


class TaxTagBehavior(TaxonomyBehavior):
    """Behavior for tax-oriented review and export workflows."""


@dataclass(frozen=True)
class BuiltinTaxon:
    """Definition for a FinScope-managed taxonomy row."""

    key: str
    name: str
    description: str
    instruction: str
    behavior: TaxonomyBehavior
    color: str = ""

    def as_seed_row(self) -> dict[str, str]:
        """Return a dictionary shape used by taxonomy seed helpers."""
        row = {
            "key": self.key,
            "name": self.name,
            "description": self.description,
            "instruction": self.instruction,
        }
        if self.color:
            row["color"] = self.color
        return row


BUILTIN_CATEGORY_TAXA = (
    BuiltinTaxon(
        key=BUILTIN_CATEGORY_INCOME,
        name="Income",
        description=(
            "Ordinary money received by the household, excluding rental-property cash flow, "
            "refunds, reimbursements, and account transfers."
        ),
        instruction=(
            "Use for clear household income such as salary, payroll deposits, benefits, "
            "pensions, investment income, and other income credits. Do not use for "
            "rental-property transactions, refunds, reimbursements, internal transfers, "
            "account adjustments, cash withdrawals, or credit card payments. Use Rental "
            "for rental-property transactions, Reimbursement for credits that repay "
            "expenses the user paid upfront, and Transfers for balance movements."
        ),
        behavior=OrdinaryIncomeCategoryBehavior(),
    ),
    BuiltinTaxon(
        key=BUILTIN_CATEGORY_RENTAL,
        name="Rental",
        description="Income and expenses related to income-generating rental properties.",
        instruction=(
            "Use whenever the transaction is clearly related to an income-generating "
            "rental property, even if the transaction would otherwise look like Income, "
            "Housing, Utilities, Administrative, or Transfers. Use for rental income, "
            "rental-property repairs, property taxes, utilities, insurance, maintenance, "
            "condo fees, tenant-related expenses, and other rental-property cash flows. "
            "Add the Tax tag when the transaction may be useful for tax preparation or accounting."
        ),
        behavior=RentalCategoryBehavior(),
    ),
    BuiltinTaxon(
        key=BUILTIN_CATEGORY_UNKNOWN,
        name=UNKNOWN_CATEGORY,
        description="Transactions whose category is not known with sufficient confidence.",
        instruction="Use when no listed category is clearly supported by the transaction description and available context.",
        behavior=UnknownCategoryBehavior(),
    ),
    BuiltinTaxon(
        key=BUILTIN_CATEGORY_REIMBURSEMENT,
        name=REIMBURSEMENT_CATEGORY,
        description="Incoming credits that repay user-paid expenses and should be linked to the covered expense transactions.",
        instruction=(
            "Use for reimbursements, repayments, and employer or organization credits "
            "that offset expenses the user paid upfront. Do not use for ordinary payroll "
            "income, transfers between the user's accounts, or merchant refunds for returned purchases."
        ),
        behavior=ReimbursementCategoryBehavior(),
    ),
    BuiltinTaxon(
        key=BUILTIN_CATEGORY_TRANSFERS,
        name=TRANSFER_CATEGORY,
        description="Movements of money that affect balances but should often be excluded from ordinary spending or income analysis.",
        instruction=(
            "Use for transfers between accounts, credit card payments, cash withdrawals, "
            "returned-purchase refunds, deposits, and account adjustments. This category "
            "is mainly for cash-flow correction and balance movement, not ordinary spending. "
            "Use Reimbursement instead for credits that repay expenses the user paid upfront. "
            "Do not use Income merely because the transaction amount is positive."
        ),
        behavior=TransferCategoryBehavior(),
    ),
)

BUILTIN_TAG_TAXA = (
    BuiltinTaxon(
        key=BUILTIN_TAG_REIMBURSABLE,
        name="Reimbursable",
        description="Marks expenses expected to be repaid by work, insurance, a tenant, another person, or another organization.",
        instruction=(
            "Use only when the transaction description, user rule, or context clearly "
            "indicates that the expense is expected to be repaid. This tag can apply "
            "across categories, such as Food, Travel, Work, Health, Education, "
            "Transportation, Housing, or Rental."
        ),
        color="#2563eb",
        behavior=ReimbursableTagBehavior(),
    ),
    BuiltinTaxon(
        key=BUILTIN_TAG_TAX,
        name="Tax",
        description="Marks transactions that may be useful for tax preparation, accounting, or year-end review.",
        instruction=(
            "Use only when the transaction is likely to be needed for tax preparation "
            "or accounting, such as rental-property records, childcare, medical expenses, "
            "professional expenses, tax payments, accounting, legal fees, government "
            "documents, or official fees. Do not apply only because a transaction includes sales tax."
        ),
        color="#b45309",
        behavior=TaxTagBehavior(),
    ),
)

BUILTIN_CATEGORIES = tuple(category.as_seed_row() for category in BUILTIN_CATEGORY_TAXA)
BUILTIN_TAGS = tuple(tag.as_seed_row() for tag in BUILTIN_TAG_TAXA)


def builtin_category_names() -> tuple[str, ...]:
    """Return the names reserved for built-in category rows."""
    return tuple(category.name for category in BUILTIN_CATEGORY_TAXA)


def builtin_tag_names() -> tuple[str, ...]:
    """Return the names reserved for built-in tag rows."""
    return tuple(tag.name for tag in BUILTIN_TAG_TAXA)


def fallback_builtin_category_names() -> tuple[str, ...]:
    """Return built-in category names without requiring database access."""
    return builtin_category_names()


def fallback_builtin_tag_names() -> tuple[str, ...]:
    """Return built-in tag names without requiring database access."""
    return builtin_tag_names()


def is_builtin_category_name(name: object) -> bool:
    """Return whether a submitted category name is reserved by FinScope."""
    normalized = str(name or "").strip().casefold()
    return normalized in {category_name.casefold() for category_name in builtin_category_names()}


def is_builtin_tag_name(name: object) -> bool:
    """Return whether a submitted tag name is reserved by FinScope."""
    normalized = str(name or "").strip().casefold()
    return normalized in {tag_name.casefold() for tag_name in builtin_tag_names()}


def builtin_category_by_key(key: object) -> BuiltinTaxon | None:
    """Return the built-in category definition for a stable key."""
    normalized = str(key or "").strip().casefold()
    return next((category for category in BUILTIN_CATEGORY_TAXA if category.key == normalized), None)


def builtin_tag_by_key(key: object) -> BuiltinTaxon | None:
    """Return the built-in tag definition for a stable key."""
    normalized = str(key or "").strip().casefold()
    return next((tag for tag in BUILTIN_TAG_TAXA if tag.key == normalized), None)


def builtin_category_by_name(name: object) -> BuiltinTaxon | None:
    """Return a built-in category definition by display name."""
    normalized = str(name or "").strip().casefold()
    return next((category for category in BUILTIN_CATEGORY_TAXA if category.name.casefold() == normalized), None)


def builtin_tag_by_name(name: object) -> BuiltinTaxon | None:
    """Return a built-in tag definition by display name."""
    normalized = str(name or "").strip().casefold()
    return next((tag for tag in BUILTIN_TAG_TAXA if tag.name.casefold() == normalized), None)


def builtin_category_name_for_key(key: object) -> str:
    """Return the configured built-in category display name for a key."""
    category = builtin_category_by_key(key)
    return category.name if category else ""


def builtin_tag_name_for_key(key: object) -> str:
    """Return the configured built-in tag display name for a key."""
    tag = builtin_tag_by_key(key)
    return tag.name if tag else ""


def is_category_name_for_builtin_key(name: object, builtin_key: str) -> bool:
    """Return whether a category label belongs to one built-in semantic key."""
    category = builtin_category_by_key(builtin_key)
    return bool(category and str(name or "").strip().casefold() == category.name.casefold())


def is_income_category_name(name: object) -> bool:
    """Return whether a category label has the ordinary-income behavior."""
    category = builtin_category_by_name(name)
    return bool(category and category.behavior.ordinary_income)
