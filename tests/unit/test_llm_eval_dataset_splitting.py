"""Unit tests for LLM categorization dataset splitting and summaries."""

from __future__ import annotations

import json
from pathlib import Path

from evals.llm_categorization.tools import split_dataset, summarize_dataset, validate_dataset


def taxonomy() -> dict[str, object]:
    """Return a candidate taxonomy with built-in concepts used by split tests."""
    return {
        "categories": [
            {"id": "cat_unknown", "name": "UNKNOWN", "description": "Unresolved.", "instruction": None},
            {"id": "cat_food", "name": "Food", "description": "Food purchases.", "instruction": None},
            {"id": "cat_income", "name": "Income", "description": "Income and credits.", "instruction": None},
            {"id": "cat_transfers", "name": "Transfers", "description": "Account transfers.", "instruction": None},
            {"id": "cat_rental", "name": "Rental", "description": "Rental-related spending.", "instruction": None},
            {
                "id": "cat_reimbursement",
                "name": "Reimbursement",
                "description": "Incoming reimbursements.",
                "instruction": None,
            },
        ],
        "tags": [
            {"id": "tag_tax", "name": "Tax", "description": "Tax-relevant.", "instruction": None},
            {
                "id": "tag_reimbursable",
                "name": "Reimbursable",
                "description": "Expense to reimburse.",
                "instruction": None,
            },
        ],
    }


def example(
    request_id: str,
    *,
    amount: float,
    category_id: str,
    category: str,
    tag_ids: list[str] | None = None,
    tags: list[str] | None = None,
    merchant: str | None = "Example merchant",
    description: str = "EXAMPLE TRANSACTION",
    needs_review: bool = False,
    label_source: str = "reviewed",
    ambiguity_type: str = "straightforward",
    statement_type: str | None = "bank_account",
    notes: str = "",
) -> dict[str, object]:
    """Return one valid synthetic evaluation example."""
    direction = "debit" if amount > 0 else "credit" if amount < 0 else "zero"
    return {
        "request_id": request_id,
        "transaction": {
            "description": description,
            "merchant": merchant,
            "amount": amount,
            "date": "2026-05-03",
            "account": "Checking",
            "statement_type": statement_type,
        },
        "candidate_taxonomy": taxonomy(),
        "similar_transactions": [],
        "expected": {"category_id": category_id, "tag_ids": tag_ids or [], "needs_review": needs_review},
        "label_source": label_source,
        "privacy_level": "synthetic",
        "coverage": {
            "category": category,
            "tags": tags or [],
            "direction": direction,
            "statement_type": statement_type,
            "confidence_band": "high" if not needs_review else "low",
            "ambiguity_type": ambiguity_type,
        },
        "notes": notes,
    }


def write_jsonl(path: Path, records: list[dict[str, object]]) -> None:
    """Write JSONL records for tests."""
    path.write_text("\n".join(json.dumps(record, sort_keys=True) for record in records), encoding="utf-8")


def read_jsonl(path: Path) -> list[dict[str, object]]:
    """Read JSONL records for assertions."""
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def split_locations(out_dir: Path) -> dict[str, str]:
    """Return the split name for each request ID."""
    locations = {}
    for split_name in split_dataset.SPLIT_NAMES:
        for record in read_jsonl(out_dir / f"{split_name}.jsonl"):
            locations[str(record["request_id"])] = split_name
    return locations


def split_test_records() -> list[dict[str, object]]:
    """Return synthetic examples with enough strata to exercise splitting."""
    return [
        example(
            "food-duplicate-1",
            amount=23.45,
            category_id="cat_food",
            category="Food",
            tag_ids=["tag_reimbursable"],
            tags=["Reimbursable"],
            merchant="Corner cafe",
            description="CORNER CAFE",
        ),
        example(
            "food-duplicate-2",
            amount=23.45,
            category_id="cat_food",
            category="Food",
            merchant="Corner cafe",
            description="CORNER CAFE",
            label_source="manual_edit",
        ),
        example(
            "income-1",
            amount=-1200.0,
            category_id="cat_income",
            category="Income",
            tag_ids=["tag_tax"],
            tags=["Tax"],
            merchant="Payroll",
            description="PAYROLL DEPOSIT",
            label_source="high_confidence_rule",
            ambiguity_type="income_like",
        ),
        example(
            "transfer-1",
            amount=300.0,
            category_id="cat_transfers",
            category="Transfers",
            merchant="Online transfer",
            description="TRANSFER ONLINE",
            label_source="stable_history",
            ambiguity_type="transfer_like",
        ),
        example(
            "reimbursement-1",
            amount=-45.0,
            category_id="cat_reimbursement",
            category="Reimbursement",
            merchant="Employer",
            description="EXPENSE REIMBURSEMENT",
            ambiguity_type="reimbursement_like",
        ),
        example(
            "rental-1",
            amount=800.0,
            category_id="cat_rental",
            category="Rental",
            merchant="Property manager",
            description="RENTAL PAYMENT",
            label_source="manual_edit",
            ambiguity_type="rental_like",
        ),
        example(
            "unknown-1",
            amount=19.0,
            category_id="cat_unknown",
            category="UNKNOWN",
            merchant=None,
            description="UNKNOWN COUNTERPARTY",
            needs_review=True,
            label_source="unknown",
            ambiguity_type="unknown_correct",
        ),
        example(
            "source-child-1",
            amount=24.0,
            category_id="cat_food",
            category="Food",
            tag_ids=["tag_reimbursable"],
            tags=["Reimbursable"],
            merchant="Corner cafe",
            description="CORNER CAFE VARIANT",
            label_source="synthetic",
            ambiguity_type="reimbursable_like",
            notes="source_request_id=food-duplicate-1",
        ),
        example(
            "tax-1",
            amount=-300.0,
            category_id="cat_income",
            category="Income",
            tag_ids=["tag_tax"],
            tags=["Tax"],
            merchant="Tax agency",
            description="TAX CREDIT",
            ambiguity_type="tax_like",
        ),
        example(
            "transfer-2",
            amount=-300.0,
            category_id="cat_transfers",
            category="Transfers",
            merchant="Online transfer",
            description="TRANSFER ONLINE",
            label_source="stable_history",
            ambiguity_type="transfer_like",
        ),
        example(
            "food-2",
            amount=12.0,
            category_id="cat_food",
            category="Food",
            merchant="Snack shop",
            description="SNACK SHOP",
            label_source="curated_by_researcher",
        ),
        example(
            "unknown-2",
            amount=-5.0,
            category_id="cat_unknown",
            category="UNKNOWN",
            merchant=None,
            description="UNRESOLVED CREDIT",
            needs_review=True,
            label_source="unknown",
            ambiguity_type="unknown_correct",
            statement_type=None,
        ),
    ]


def test_split_dataset_writes_deterministic_valid_splits_and_keeps_leakage_groups_together(tmp_path):
    """Verify split outputs are valid and leakage-linked examples remain together."""
    input_path = tmp_path / "curated.jsonl"
    out_dir = tmp_path / "splits"
    write_jsonl(input_path, split_test_records())

    first_exit_code = split_dataset.main(
        [
            "--input",
            str(input_path),
            "--out-dir",
            str(out_dir),
            "--dev-ratio",
            "0.5",
            "--validation-ratio",
            "0.3",
            "--test-ratio",
            "0.2",
            "--seed",
            "42",
        ]
    )
    first_locations = split_locations(out_dir)
    first_dev = (out_dir / "dev.jsonl").read_text(encoding="utf-8")

    second_exit_code = split_dataset.main(
        [
            "--input",
            str(input_path),
            "--out-dir",
            str(out_dir),
            "--dev-ratio",
            "0.5",
            "--validation-ratio",
            "0.3",
            "--test-ratio",
            "0.2",
            "--seed",
            "42",
        ]
    )

    assert first_exit_code == 0
    assert second_exit_code == 0
    assert (out_dir / "dev.jsonl").read_text(encoding="utf-8") == first_dev
    assert first_locations["food-duplicate-1"] == first_locations["food-duplicate-2"]
    assert first_locations["food-duplicate-1"] == first_locations["source-child-1"]
    for split_name in split_dataset.SPLIT_NAMES:
        assert validate_dataset.validate_dataset(out_dir / f"{split_name}.jsonl").example_count > 0

    report = (out_dir / "split_report.md").read_text(encoding="utf-8")
    assert "Development set: prompt design and iteration." in report
    assert "Held-out test set: final estimate after prompt selection only." in report
    assert "No leakage grouping keys cross splits." in report
    assert "High-trust labels" in report


def test_summarize_dataset_cli_writes_curation_report(tmp_path):
    """Verify the summary utility reports curation and methodology counts."""
    input_path = tmp_path / "curated.jsonl"
    out_path = tmp_path / "summary.md"
    write_jsonl(input_path, split_test_records())

    exit_code = summarize_dataset.main(["--input", str(input_path), "--out", str(out_path)])

    report = out_path.read_text(encoding="utf-8")
    assert exit_code == 0
    assert "Examples needing review: 2" in report
    assert "High-trust labels: 9" in report
    assert "Low-trust labels: 3" in report
    assert "Expected UNKNOWN: 2" in report
    assert "Missing categories" in report
