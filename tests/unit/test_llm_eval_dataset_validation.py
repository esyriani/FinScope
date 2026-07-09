"""Unit tests for offline LLM categorization dataset validation."""

from __future__ import annotations

import json

from evals.llm_categorization.tools import validate_dataset


def base_example(request_id: str = "req-1") -> dict[str, object]:
    """Return a valid evaluation example using the strict JSONL schema."""
    return {
        "request_id": request_id,
        "transaction": {
            "description": "PAYROLL DEPOSIT",
            "merchant": "Payroll",
            "amount": -1000.0,
            "date": "2026-05-03",
            "account": "Checking",
            "statement_type": "bank_account",
        },
        "candidate_taxonomy": {
            "categories": [
                {
                    "id": "cat_unknown",
                    "name": "UNKNOWN",
                    "description": "Unresolved transactions.",
                    "instruction": None,
                },
                {
                    "id": "cat_income",
                    "name": "Income",
                    "description": "Ordinary income.",
                    "instruction": "Use for payroll and salary credits.",
                },
            ],
            "tags": [
                {
                    "id": "tag_tax",
                    "name": "Tax",
                    "description": "Tax-relevant transaction.",
                    "instruction": None,
                }
            ],
        },
        "similar_transactions": [
            {
                "description": "PAYROLL",
                "amount": -950.0,
                "category_id": "cat_income",
                "tag_ids": ["tag_tax"],
                "evidence_type": "history",
                "confidence": 0.91,
            }
        ],
        "expected": {
            "category_id": "cat_income",
            "tag_ids": ["tag_tax"],
            "needs_review": True,
        },
        "label_source": "reviewed",
        "privacy_level": "redacted_real",
        "coverage": {
            "category": "Income",
            "tags": ["Tax"],
            "direction": "credit",
            "statement_type": "bank_account",
            "confidence_band": "high",
            "ambiguity_type": "income_like",
        },
        "notes": "",
    }


def write_jsonl(path, records):
    """Write JSONL records for validator tests."""
    path.write_text("\n".join(json.dumps(record, sort_keys=True) for record in records), encoding="utf-8")


def test_validate_dataset_reports_required_summary_counts(tmp_path):
    """Verify a valid dataset returns the required coverage summaries."""
    first = base_example("req-1")
    second = base_example("req-2")
    second["transaction"] = {
        "description": "MYSTERY SHOP",
        "merchant": None,
        "amount": 20.0,
        "date": None,
        "account": "Credit card",
        "statement_type": "credit_card",
    }
    second["expected"] = {"category_id": "cat_unknown", "tag_ids": [], "needs_review": True}
    second["coverage"] = {
        "category": "UNKNOWN",
        "tags": [],
        "direction": "debit",
        "statement_type": "credit_card",
        "confidence_band": "low",
        "ambiguity_type": "unknown_correct",
    }
    dataset_path = tmp_path / "development.jsonl"
    write_jsonl(dataset_path, [first, second])

    summary = validate_dataset.validate_dataset(dataset_path)

    assert summary.example_count == 2
    assert summary.unique_request_id_count == 2
    assert summary.category_coverage["Income (cat_income)"] == 1
    assert summary.category_coverage["UNKNOWN (cat_unknown)"] == 1
    assert summary.tag_coverage["Tax (tag_tax)"] == 1
    assert summary.label_source_counts["reviewed"] == 2
    assert summary.privacy_level_counts["redacted_real"] == 2
    assert summary.direction_counts == {"credit": 1, "debit": 1}
    assert summary.needs_review_counts["true"] == 2
    assert summary.expected_unknown_count == 1
    assert summary.ambiguity_type_counts["income_like"] == 1
    assert summary.ambiguity_type_counts["unknown_correct"] == 1
    assert summary.statement_type_counts["bank_account"] == 1
    assert summary.statement_type_counts["credit_card"] == 1


def test_validate_dataset_rejects_duplicate_request_ids(tmp_path):
    """Verify request IDs must be unique within one dataset file."""
    dataset_path = tmp_path / "development.jsonl"
    write_jsonl(dataset_path, [base_example("duplicate"), base_example("duplicate")])

    try:
        validate_dataset.validate_dataset(dataset_path)
    except validate_dataset.DatasetValidationError as exc:
        message = str(exc)
    else:
        raise AssertionError("duplicate request_id should fail validation")

    assert "line 2, request duplicate" in message
    assert "duplicate request_id" in message


def test_validate_dataset_rejects_invalid_expected_taxonomy_reference(tmp_path):
    """Verify expected taxonomy IDs must exist in candidate taxonomy."""
    example = base_example()
    example["expected"] = {"category_id": "cat_missing", "tag_ids": ["tag_tax"], "needs_review": True}
    dataset_path = tmp_path / "development.jsonl"
    write_jsonl(dataset_path, [example])

    try:
        validate_dataset.validate_dataset(dataset_path)
    except validate_dataset.DatasetValidationError as exc:
        message = str(exc)
    else:
        raise AssertionError("missing expected category should fail validation")

    assert "line 1, request req-1" in message
    assert "expected.category_id 'cat_missing' is not in candidate taxonomy" in message


def test_validate_dataset_rejects_direction_inconsistent_with_amount(tmp_path):
    """Verify coverage direction is checked against signed amount."""
    example = base_example()
    example["coverage"] = {
        "category": "Income",
        "tags": ["Tax"],
        "direction": "debit",
        "statement_type": "bank_account",
        "confidence_band": "high",
        "ambiguity_type": "income_like",
    }
    dataset_path = tmp_path / "development.jsonl"
    write_jsonl(dataset_path, [example])

    try:
        validate_dataset.validate_dataset(dataset_path)
    except validate_dataset.DatasetValidationError as exc:
        message = str(exc)
    else:
        raise AssertionError("direction mismatch should fail validation")

    assert "coverage.direction 'debit' is inconsistent with signed amount" in message
    assert "expected 'credit'" in message


def test_validate_dataset_cli_prints_warnings_without_failing(tmp_path, capsys):
    """Verify methodology risks are warnings, not validation failures."""
    dataset_path = tmp_path / "validation.jsonl"
    write_jsonl(dataset_path, [base_example()])

    exit_code = validate_dataset.main([str(dataset_path)])
    output = capsys.readouterr()

    assert exit_code == 0
    assert "Validated 1 example(s)" in output.out
    assert "Warnings:" in output.out
    assert "no expected UNKNOWN examples" in output.out
    assert "fewer than 80 examples: 1" in output.out
    assert "no examples with empty expected tags" in output.out
    assert "no debit examples" in output.out
    assert "no transfer-like cases" in output.out
    assert "no tax-like cases" in output.out
    assert "too few examples for a validation set" in output.out
    assert output.err == ""


def test_validate_dataset_warns_about_benchmark_quality_gates(tmp_path):
    """Verify benchmark-level coverage gates are reported as warnings."""
    example = base_example()
    example["candidate_taxonomy"] = {
        "categories": [
            *example["candidate_taxonomy"]["categories"],  # type: ignore[index]
            {
                "id": "cat_transfers",
                "name": "Transfers",
                "description": "Transfers.",
                "instruction": None,
            },
            {
                "id": "cat_reimbursement",
                "name": "Reimbursement",
                "description": "Reimbursements.",
                "instruction": None,
            },
            {
                "id": "cat_rental",
                "name": "Rental",
                "description": "Rental.",
                "instruction": None,
            },
        ],
        "tags": [
            *example["candidate_taxonomy"]["tags"],  # type: ignore[index]
            {
                "id": "tag_reimbursable",
                "name": "Reimbursable",
                "description": "Reimbursable.",
                "instruction": None,
            },
        ],
    }
    dataset_path = tmp_path / "curated.jsonl"
    write_jsonl(dataset_path, [example])

    summary = validate_dataset.validate_dataset(dataset_path)

    assert "fewer than 80 examples: 1" in summary.warnings
    assert "no transfer-like cases" in summary.warnings
    assert "no reimbursement-like or reimbursable-like cases" in summary.warnings
    assert "no rental-like cases" in summary.warnings
    assert "no tax-like cases" in summary.warnings
    assert any(warning.startswith("categories with no examples:") for warning in summary.warnings)
    assert any(warning.startswith("tags with no examples:") for warning in summary.warnings)
