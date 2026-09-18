"""Tests for the opt-in LLM prompt evaluation harness."""

import json

from evals.llm_categorization import prompt_eval


def test_prompt_eval_dataset_covers_representative_risks():
    """Verify eval examples cover the key prompt-risk areas from the review."""
    cases = prompt_eval.load_prompt_eval_cases()
    risk_tags = {tag for case in cases for tag in case["risk_tags"]}

    assert len(cases) >= 10
    assert {
        "credit",
        "income",
        "judo",
        "payment_processor",
        "reimbursement",
        "rental",
        "service_tag",
        "transfer",
        "travel",
        "unknown",
        "utilities",
        "vehicle",
        "work",
    } <= risk_tags


def test_prompt_eval_messages_reuse_privacy_minimized_prompt_builder():
    """Verify eval cases build the production prompt without leaking raw descriptors."""
    case = next(case for case in prompt_eval.load_prompt_eval_cases() if case["id"] == "utilities-hydro-quebec")
    messages = prompt_eval.build_prompt_eval_messages(case)
    payload = json.loads(messages[1]["content"])

    assert [message["role"] for message in messages] == ["system", "user"]
    assert payload["transactions"][0]["request_id"] == "utilities-hydro-quebec"
    assert payload["transactions"][0]["merchant_key"] == "HYDRO QUEBEC"
    assert payload["transactions"][0]["amount_direction"] == "debit"
    assert "description" not in payload["transactions"][0]
    assert "HYDRO-QUEBEC FAC 554433" not in messages[1]["content"]
    assert "expected" not in payload
    assert "rationale" not in payload


def test_prompt_eval_scores_expected_outputs_as_clean_run():
    """Verify the dry-run expected-output provider validates the harness and dataset."""
    cases = prompt_eval.load_prompt_eval_cases()
    taxonomy = prompt_eval.prompt_eval_taxonomy()

    run = prompt_eval.evaluate_prompt_eval_cases(
        cases,
        lambda case, _messages: prompt_eval.expected_prompt_eval_result(case, taxonomy),
        model="dry-run",
    )

    assert run.summary["case_count"] == len(cases)
    assert run.summary["schema_valid_rate"] == 1.0
    assert run.summary["category_accuracy"] == 1.0
    assert run.summary["exact_taxonomy_match_rate"] == 1.0
    assert run.summary["needs_review_accuracy"] == 1.0
    assert run.summary["unsafe_auto_assignment_rate"] == 0.0
    assert run.summary["failure_mode_counts"] == {}


def test_prompt_eval_flags_unsafe_high_confidence_wrong_output():
    """Verify scoring catches the failure mode the eval harness is meant to expose."""
    case = next(case for case in prompt_eval.load_prompt_eval_cases() if case["id"] == "utilities-hydro-quebec")
    taxonomy = prompt_eval.prompt_eval_taxonomy()
    wrong_result = {
        "request_id": case["id"],
        "category_id": prompt_eval.taxonomy_id(taxonomy.category_rows, "Personal"),
        "tag_ids": [],
        "confidence": 0.99,
        "needs_review": False,
        "supported_by_similar_transactions": False,
        "reason": "Bad confident answer.",
    }

    run = prompt_eval.evaluate_prompt_eval_cases([case], lambda _case, _messages: wrong_result, model="fake")

    assert run.summary["category_accuracy"] == 0.0
    assert run.summary["exact_taxonomy_match_rate"] == 0.0
    assert run.summary["unsafe_auto_assignment_rate"] == 1.0
    assert run.summary["high_confidence_wrong_rate"] == 1.0
    assert run.summary["failure_mode_counts"]["wrong_category"] == 1
    assert run.summary["failure_mode_counts"]["unsafe_auto_assignment"] == 1


def test_prompt_eval_writes_repeatable_report_artifacts(tmp_path):
    """Verify prompt eval runs write raw outputs and a Markdown report."""
    cases = prompt_eval.load_prompt_eval_cases()[:2]
    taxonomy = prompt_eval.prompt_eval_taxonomy()
    run = prompt_eval.evaluate_prompt_eval_cases(
        cases,
        lambda case, _messages: prompt_eval.expected_prompt_eval_result(case, taxonomy),
        model="dry-run",
    )

    prompt_eval.write_prompt_eval_artifacts(tmp_path, run)

    report = (tmp_path / "report.md").read_text(encoding="utf-8")
    raw_outputs = (tmp_path / "raw_outputs.jsonl").read_text(encoding="utf-8").splitlines()
    assert "# LLM Categorization Prompt Eval Report" in report
    assert "exact_taxonomy_match_rate" in report
    assert len(raw_outputs) == 2
    assert json.loads(raw_outputs[0])["case_id"] == cases[0]["id"]
