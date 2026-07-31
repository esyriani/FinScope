"""Constants for Reports request options and presentation labels."""

REPORT_MEASURE_SPENDING = "spending"
REPORT_MEASURE_INCOME = "income"
REPORT_MEASURE_NET = "net"
REPORT_MEASURES = (
    REPORT_MEASURE_SPENDING,
    REPORT_MEASURE_INCOME,
    REPORT_MEASURE_NET,
)
REPORT_MEASURE_OPTIONS = (
    {"value": REPORT_MEASURE_SPENDING, "label": "Spending"},
    {"value": REPORT_MEASURE_INCOME, "label": "Income and credits"},
    {"value": REPORT_MEASURE_NET, "label": "Net cash flow"},
)

REPORT_BASIS_CASH_FLOW = "cash_flow"
REPORT_BASIS_LEDGER = "ledger"
REPORT_BASES = (
    REPORT_BASIS_CASH_FLOW,
    REPORT_BASIS_LEDGER,
)
REPORT_BASIS_OPTIONS = (
    {
        "value": REPORT_BASIS_CASH_FLOW,
        "label": "Reportable cash flow",
        "description": "Transfers, payments, and reimbursement credits are excluded from ordinary reporting.",
    },
    {
        "value": REPORT_BASIS_LEDGER,
        "label": "Ledger rows",
        "description": "All active ledger rows are included, including payments and transfers.",
    },
)
