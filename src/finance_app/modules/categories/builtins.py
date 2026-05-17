"""Built-in category definitions.

Defines FinScope-managed categories that are seeded into the database outside
the editable taxonomy seed file. Category services use the stable keys here to
protect built-in rows from user edits and deletes.
"""

from finance_app.core.constants import TRANSFER_CATEGORY, UNKNOWN_CATEGORY


BUILTIN_CATEGORY_UNKNOWN = "unknown"
BUILTIN_CATEGORY_TRANSFERS = "transfers"

BUILTIN_CATEGORIES = (
    {
        "key": BUILTIN_CATEGORY_UNKNOWN,
        "name": UNKNOWN_CATEGORY,
        "description": "Transactions whose category is not known with sufficient confidence.",
        "instruction": (
            "Use when no listed category is clearly supported by the transaction "
            "description and available context."
        ),
    },
    {
        "key": BUILTIN_CATEGORY_TRANSFERS,
        "name": TRANSFER_CATEGORY,
        "description": (
            "Movements of money that affect balances but should often be excluded "
            "from ordinary spending or income analysis."
        ),
        "instruction": (
            "Use for transfers between accounts, credit card payments, cash withdrawals, "
            "refunds, reimbursements, repayments, returned purchases, deposits, and "
            "account adjustments. This category is mainly for cash-flow correction "
            "and balance movement, not ordinary spending. Do not use Income merely "
            "because the transaction amount is positive."
        ),
    },
)


def builtin_category_names():
    """Return the names reserved for built-in category rows."""
    return tuple(category["name"] for category in BUILTIN_CATEGORIES)


def is_builtin_category_name(name):
    """Return whether a submitted category name is reserved by FinScope."""
    normalized = str(name or "").strip().casefold()
    return normalized in {
        category_name.casefold()
        for category_name in builtin_category_names()
    }
