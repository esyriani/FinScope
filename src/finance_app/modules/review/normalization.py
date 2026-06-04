"""Review normalization helpers.

Provides merchant-key normalization shared by review services, repositories,
and presenters. The helpers are deterministic and do not own database access.
"""

from finance_app.modules.merchants.normalization import canonicalize_merchant_key


def review_merchant_key(value, conn=None):
    """Return the normalized merchant key used for review grouping."""
    normalized = canonicalize_merchant_key(value or "", conn=conn)
    if normalized:
        return normalized
    return " ".join(str(value or "").upper().split())
