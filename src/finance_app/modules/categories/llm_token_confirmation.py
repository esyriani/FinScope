"""Shared confirmation checks for AI token-estimate guarded actions.

Controllers use this module to decide whether a submitted AI action may proceed
after the user-facing token estimate step. The helper reads the owner-managed
runtime setting and does not estimate tokens or call an external provider.
"""

from collections.abc import Mapping
from typing import Any

from finance_app.database.engine import db_core_transaction
from finance_app.modules.categories.llm_tokens import AI_TOKEN_ESTIMATE_CONFIRMED_FIELD
from finance_app.modules.settings.runtime import confirm_ai_token_usage_enabled


def ai_token_estimate_confirmed(form: Mapping[str, Any]) -> bool:
    """Return whether a token-estimate guarded AI action may proceed."""
    if form.get(AI_TOKEN_ESTIMATE_CONFIRMED_FIELD) == "1":
        return True

    with db_core_transaction() as conn:
        return not confirm_ai_token_usage_enabled(conn)
