"""Split-phase LLM categorization workflow coordination.

The helpers in this module prepare database-backed categorization context in a
short transaction, release the database connection while an external provider
request runs, and hand the validated outcome back to callers so they can apply
it inside their own short write transaction.
"""

from collections.abc import Mapping, MutableMapping, Sequence
from dataclasses import dataclass
from typing import Any

from finance_app.database.engine import db_core_transaction
from finance_app.modules.categories.categorization import (
    categorize_transactions_from_evidence,
    resolve_transaction_category_ids,
)
from finance_app.modules.categories.llm import (
    LlmCategorizationOutcome,
    LlmCategorizationRequestContext,
    prepare_llm_categorization_request_context,
    request_llm_categorization_outcome,
)


@dataclass(frozen=True)
class PreparedTransactionLlmCategorization:
    """Prepared transaction payloads and prompt context for an LLM request."""

    unknown_category: str
    rules: Sequence[Mapping[str, Any]]
    request_context: LlmCategorizationRequestContext | None


def prepare_transaction_llm_categorization(
    transactions: list[MutableMapping[str, Any]],
    prepare_candidate_taxonomies: Any = None,
) -> PreparedTransactionLlmCategorization:
    """Prepare local categorization evidence and LLM prompt context."""
    with db_core_transaction() as conn:
        evidence = categorize_transactions_from_evidence(
            transactions,
            conn,
            prefer_llm_fallback=True,
        )
        request_context = (
            prepare_llm_categorization_request_context(
                conn,
                transactions,
                evidence.unknown_category,
                prepare_candidate_taxonomies=prepare_candidate_taxonomies,
            )
            if any(tx.get("category") == evidence.unknown_category for tx in transactions)
            else None
        )
        resolve_transaction_category_ids(conn, transactions)

    return PreparedTransactionLlmCategorization(
        unknown_category=evidence.unknown_category,
        rules=evidence.rules,
        request_context=request_context,
    )


def request_prepared_transaction_llm_categorization(
    prepared: PreparedTransactionLlmCategorization,
    request_categories: Any = None,
    batch_size: int | None = None,
) -> LlmCategorizationOutcome:
    """Run the external LLM request for a prepared categorization, if needed."""
    if prepared.request_context is None:
        return LlmCategorizationOutcome(accepted={})

    return request_llm_categorization_outcome(
        prepared.request_context,
        prepared.rules,
        prepared.unknown_category,
        request_categories=request_categories,
        batch_size=batch_size,
    )
