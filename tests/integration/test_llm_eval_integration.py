"""Integration tests for offline LLM categorization evaluation."""

from sqlalchemy import text
from tests.support.database import insert_merchant
from tests.support.llm import result_payload

from finance_app.core.constants import (
    CATEGORY_RULE_DIRECTION_DEBIT,
    CATEGORY_RULE_SOURCE_MANUAL,
    CATEGORY_SOURCE_MANUAL,
)
from finance_app.modules.categories.llm_eval import (
    LLM_EVAL_CONTEXT_FULL,
    LlmEvalRow,
    evaluate_prepared_llm,
    prepare_llm_eval,
    validate_eval_rows,
)
from finance_app.modules.categories.taxonomy import get_category_rows, get_tag_options


def retained_row(candidate_id, merchant_key, expected_category, *, expected_tags=(), should_unknown=False):
    """Build a retained eval row for integration tests."""
    return LlmEvalRow(
        candidate_id=candidate_id,
        merchant_key=merchant_key,
        amount_direction="debit",
        amount_magnitude="medium",
        transaction_kind="expense",
        expected_category=expected_category,
        expected_tags=tuple(expected_tags),
        should_remain_unknown=should_unknown,
        retain_for_eval=True,
        source_row_number=2,
    )


def test_llm_eval_prepares_validates_and_scores_fake_provider_results(app, core_conn):
    """Verify the offline eval path uses taxonomy IDs and validates provider output."""
    del app
    rows = [
        retained_row("candidate-food", "METRO GROCERY", "Food", expected_tags=("Tax",)),
        retained_row("candidate-unknown", "AMBIGUOUS PERSON", "UNKNOWN", should_unknown=True),
    ]
    category_rows = get_category_rows(core_conn)
    tag_options = get_tag_options(core_conn)
    assert validate_eval_rows(rows, [row["name"] for row in category_rows], tag_options) == []

    prepared = prepare_llm_eval(core_conn, rows, openai_model="test-model")
    captured = {}

    def fake_request(unknown_chunk, *args):
        """Return one accepted category and one explicit UNKNOWN result."""
        captured["unknown_chunk"] = list(unknown_chunk)
        captured["rules"] = list(args[0])
        captured["model"] = args[5]
        request_category_rows = args[3]
        request_tag_rows = args[4]
        return [
            result_payload(
                request_category_rows,
                request_tag_rows,
                unknown_chunk[0]["llm_request_id"],
                "Food",
                0.99,
                tags=["Tax"],
            ),
            result_payload(
                request_category_rows,
                request_tag_rows,
                unknown_chunk[1]["llm_request_id"],
                "UNKNOWN",
                0.99,
            ),
        ]

    report = evaluate_prepared_llm(prepared, request_categories=fake_request, batch_size=2)

    assert captured["model"] == "test-model"
    assert captured["rules"] == []
    assert captured["unknown_chunk"][0]["description"] == "METRO GROCERY"
    assert "rule_evidence" not in captured["unknown_chunk"][0]
    assert "amount_direction" not in captured["unknown_chunk"][0]
    assert report.metrics.total == 2
    assert report.metrics.category_correct == 2
    assert report.metrics.exact_match_correct == 2
    assert report.mismatches == ()


def test_llm_eval_full_context_uses_db_rules_and_history_without_writes(app, core_conn, data_factory):
    """Verify full-context eval builds realistic evidence while keeping DB rows unchanged."""
    del app
    merchant_id = insert_merchant(core_conn, "HYDRO QUEBEC")
    history_id = data_factory.transactions.create(
        description="HYDRO QUEBEC BILL",
        amount="125.00",
        category="Utilities",
        category_source=CATEGORY_SOURCE_MANUAL,
        reviewed_at="2026-03-02T00:00:00+00:00",
        merchant_id=merchant_id,
        tags=("Service",),
        tag_source=CATEGORY_SOURCE_MANUAL,
        fingerprint="llm-eval-history",
    )
    target_id = data_factory.transactions.create(
        description="HYDRO QUEBEC PAYMENT",
        amount="120.00",
        category="UNKNOWN",
        category_source="unknown",
        merchant_id=merchant_id,
        fingerprint="llm-eval-target",
    )
    del history_id
    data_factory.rules.create(
        keyword="HYDRO QUEBEC",
        category="Utilities",
        direction=CATEGORY_RULE_DIRECTION_DEBIT,
        tags=("Service",),
    )
    rows = [
        LlmEvalRow(
            candidate_id="hydro",
            merchant_key="HYDRO QUEBEC",
            amount_direction="debit",
            amount_magnitude="large",
            transaction_kind="expense",
            expected_category="Utilities",
            expected_tags=("Service",),
            should_remain_unknown=False,
            retain_for_eval=True,
            source_row_number=2,
            transaction_id=target_id,
        )
    ]

    prepared = prepare_llm_eval(core_conn, rows, openai_model="test-model", mode=LLM_EVAL_CONTEXT_FULL)
    captured = {}

    def fake_request(unknown_chunk, *args):
        """Capture full-context request inputs and return the expected result."""
        captured["unknown_chunk"] = list(unknown_chunk)
        captured["rules"] = list(args[0])
        request_category_rows = args[3]
        request_tag_rows = args[4]
        return [
            result_payload(
                request_category_rows,
                request_tag_rows,
                unknown_chunk[0]["llm_request_id"],
                "Utilities",
                0.99,
                tags=["Service"],
                supported_by_similar_transactions=True,
            )
        ]

    report = evaluate_prepared_llm(prepared, request_categories=fake_request, batch_size=1)

    request_item = captured["unknown_chunk"][0]
    assert request_item["category"] == "UNKNOWN"
    assert request_item["id"] == target_id
    assert request_item["rule_evidence"]["category"] == "Utilities"
    assert request_item["historical_evidence"]["category"] == "Utilities"
    assert captured["rules"][0]["source"] == CATEGORY_RULE_SOURCE_MANUAL
    assert report.metrics.exact_match_correct == 1
    assert report.predictions[0].context["mode"] == LLM_EVAL_CONTEXT_FULL
    assert report.predictions[0].context["db_transaction_id"] == target_id

    stored_category = core_conn.execute(
        text("SELECT category FROM transactions WHERE id = :id"),
        {"id": target_id},
    ).scalar_one()
    assert stored_category == "UNKNOWN"
