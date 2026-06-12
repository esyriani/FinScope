"""Constants for the comparison feature."""

ANALYSIS_MODE_SPENDING = "spending"
ANALYSIS_MODE_INCOME = "income"
ANALYSIS_MODE_NET = "net"
ANALYSIS_MODE_OPTIONS = {
    ANALYSIS_MODE_SPENDING: {
        "value": ANALYSIS_MODE_SPENDING,
        "label": "Spending",
        "noun": "spending",
        "positive_tone": "danger",
    },
    ANALYSIS_MODE_INCOME: {
        "value": ANALYSIS_MODE_INCOME,
        "label": "Income and credits",
        "noun": "income and credits",
        "positive_tone": "success",
    },
    ANALYSIS_MODE_NET: {
        "value": ANALYSIS_MODE_NET,
        "label": "Net cash flow",
        "noun": "net cash flow",
        "positive_tone": "success",
    },
}
PERIOD_COMPARISON_OPTIONS = {
    "month_previous": "This month vs last month",
    "month_last_year": "This month vs same month last year",
    "ytd_last_year": "Year to date vs same period last year",
}
MONTH_LABELS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
UNKNOWN_WARNING_THRESHOLD = 20
