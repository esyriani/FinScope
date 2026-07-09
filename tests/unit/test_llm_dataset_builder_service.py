"""Unit tests for coverage-driven LLM eval dataset build previews."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from evals.llm_categorization.services import dataset_builder_service as builder
from evals.llm_categorization.services import labeling_queue_service
from evals.llm_categorization.tools import validate_dataset
from evals.llm_categorization.tools.io_utils import load_jsonl, write_jsonl


def write_json(path: Path, payload: dict[str, object]) -> None:
    """Write a JSON object fixture."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")


def create_preview_db(path: Path, *, include_duplicates: bool = False, include_ai_corrected: bool = False) -> None:
    """Create a small synthetic FinScope-like SQLite database."""
    conn = sqlite3.connect(path)
    try:
        conn.executescript("""
            CREATE TABLE categories (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT,
                instruction TEXT
            );
            CREATE TABLE tags (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT,
                instruction TEXT
            );
            CREATE TABLE transactions (
                id INTEGER PRIMARY KEY,
                description TEXT,
                merchant_id INTEGER,
                amount REAL,
                tx_date TEXT,
                category_id TEXT,
                category_source TEXT,
                category_confidence REAL,
                needs_review INTEGER,
                reviewed_at TEXT,
                ai_called INTEGER,
                ai_category_id TEXT,
                ai_tag_ids TEXT,
                ai_confidence REAL,
                ai_reason TEXT,
                ai_needs_review INTEGER,
                ai_corrected_later INTEGER
            );
            CREATE TABLE transaction_tags (
                transaction_id INTEGER NOT NULL,
                tag_id TEXT NOT NULL
            );
            """)
        conn.executemany(
            "INSERT INTO categories (id, name, description, instruction) VALUES (?, ?, ?, ?)",
            [
                ("cat_unknown", "UNKNOWN", "Unresolved.", None),
                ("cat_food", "Food", "Food purchases.", None),
                ("cat_income", "Income", "Income and credits.", None),
            ],
        )
        conn.executemany(
            "INSERT INTO tags (id, name, description, instruction) VALUES (?, ?, ?, ?)",
            [("tag_reimbursable", "Reimbursable", "Expense to reimburse.", None)],
        )
        conn.executemany(
            """
            INSERT INTO transactions (
                id,
                description,
                merchant_id,
                amount,
                tx_date,
                category_id,
                category_source,
                category_confidence,
                needs_review,
                reviewed_at,
                ai_called,
                ai_category_id,
                ai_tag_ids,
                ai_confidence,
                ai_reason,
                ai_needs_review,
                ai_corrected_later
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    1,
                    "CAFE RESTAURANT",
                    101,
                    12.25,
                    "2026-01-01",
                    "cat_food",
                    "manual",
                    0.99,
                    0,
                    None,
                    0,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                ),
                (
                    2,
                    "GROCERY STORE",
                    102,
                    45.10,
                    "2026-01-02",
                    "cat_food",
                    "rule",
                    0.98,
                    0,
                    None,
                    0,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                ),
                (
                    3,
                    "AI UNCERTAIN CAFE",
                    103,
                    15.00,
                    "2026-01-03",
                    "cat_food",
                    "ai",
                    0.60,
                    1,
                    None,
                    1,
                    "cat_food",
                    "[]",
                    0.60,
                    "AI was uncertain.",
                    1,
                    0,
                ),
                (
                    4,
                    "PAYROLL DEPOSIT",
                    104,
                    -100.00,
                    "2026-01-04",
                    "cat_income",
                    "manual",
                    0.99,
                    0,
                    None,
                    0,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                ),
                (
                    5,
                    "AI UNKNOWN TRANSFER",
                    105,
                    -25.00,
                    "2026-01-05",
                    "cat_unknown",
                    "ai",
                    0.50,
                    1,
                    None,
                    1,
                    "cat_unknown",
                    "[]",
                    0.50,
                    "Insufficient merchant information.",
                    1,
                    0,
                ),
            ],
        )
        if include_ai_corrected:
            conn.execute(
                """
                INSERT INTO transactions (
                    id,
                    description,
                    merchant_id,
                    amount,
                    tx_date,
                    category_id,
                    category_source,
                    category_confidence,
                    needs_review,
                    reviewed_at,
                    ai_called,
                    ai_category_id,
                    ai_tag_ids,
                    ai_confidence,
                    ai_reason,
                    ai_needs_review,
                    ai_corrected_later
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    8,
                    "CORRECTED CAFE",
                    106,
                    18.00,
                    "2026-01-08",
                    "cat_food",
                    "manual",
                    0.99,
                    0,
                    "2026-01-09",
                    1,
                    "cat_unknown",
                    "[]",
                    0.42,
                    "AI returned unknown before manual correction.",
                    1,
                    1,
                ),
            )
        if include_duplicates:
            conn.executemany(
                """
                INSERT INTO transactions (
                    id,
                    description,
                    merchant_id,
                    amount,
                    tx_date,
                    category_id,
                    category_source,
                    category_confidence,
                    needs_review,
                    reviewed_at,
                    ai_called,
                    ai_category_id,
                    ai_tag_ids,
                    ai_confidence,
                    ai_reason,
                    ai_needs_review,
                    ai_corrected_later
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        6,
                        "CAFE RESTAURANT",
                        101,
                        12.25,
                        "2026-01-06",
                        "cat_food",
                        "manual",
                        0.99,
                        0,
                        None,
                        0,
                        None,
                        None,
                        None,
                        None,
                        None,
                        None,
                    ),
                    (
                        7,
                        "CAFE RESTAURANT",
                        101,
                        12.25,
                        "2026-01-07",
                        "cat_food",
                        "manual",
                        0.99,
                        0,
                        None,
                        0,
                        None,
                        None,
                        None,
                        None,
                        None,
                        None,
                    ),
                ],
            )
        conn.execute("INSERT INTO transaction_tags (transaction_id, tag_id) VALUES (?, ?)", (1, "tag_reimbursable"))
        if include_duplicates:
            conn.executemany(
                "INSERT INTO transaction_tags (transaction_id, tag_id) VALUES (?, ?)",
                [(6, "tag_reimbursable"), (7, "tag_reimbursable")],
            )
        conn.commit()
    finally:
        conn.close()


def create_no_ai_evidence_db(path: Path) -> None:
    """Create a synthetic database without AI source/evidence columns."""
    conn = sqlite3.connect(path)
    try:
        conn.executescript("""
            CREATE TABLE categories (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT,
                instruction TEXT
            );
            CREATE TABLE tags (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT,
                instruction TEXT
            );
            CREATE TABLE transactions (
                id INTEGER PRIMARY KEY,
                description TEXT,
                amount REAL,
                tx_date TEXT,
                category_id TEXT
            );
            CREATE TABLE transaction_tags (
                transaction_id INTEGER NOT NULL,
                tag_id TEXT NOT NULL
            );
            """)
        conn.execute(
            "INSERT INTO categories (id, name, description, instruction) VALUES (?, ?, ?, ?)",
            ("cat_food", "Food", "Food purchases.", None),
        )
        conn.execute(
            "INSERT INTO transactions (id, description, amount, tx_date, category_id) VALUES (?, ?, ?, ?, ?)",
            (1, "CAFE", 10.0, "2026-01-01", "cat_food"),
        )
        conn.commit()
    finally:
        conn.close()


def preview_spec_payload() -> dict[str, object]:
    """Return a valid preview spec payload."""
    return {
        "name": "curated_v1",
        "description": "Synthetic preview spec",
        "max_examples": 10,
        "seed": 7,
        "redact": True,
        "ai_problem_cases": {
            "include": True,
            "include_ai_unknown": True,
            "include_ai_needs_review": True,
            "include_low_confidence": True,
            "low_confidence_threshold": 0.85,
            "require_manual_label_before_export": True,
        },
        "targets": {
            "categories": {"Food": 2},
            "tags": {"Reimbursable": 1},
            "directions": {"debit": 2, "credit": 1},
            "review": {"needs_review_true": 1, "needs_review_false": 2},
            "tag_shape": {"no_tags": 1, "one_or_more_tags": 1},
            "ambiguity_types": {"straightforward": 1, "ai_unknown": 1},
        },
    }


def test_dataset_builder_spec_defaults_and_validation():
    """Verify minimal specs receive safe defaults and invalid specs fail."""
    spec = builder.normalize_dataset_spec({"name": "curated_v1"})

    assert spec.name == "curated_v1"
    assert spec.max_examples == 100
    assert spec.seed == 42
    assert spec.redact is True
    assert spec.label_sources["ai"] == "candidate_only"
    assert spec.ai_problem_cases["include"] is False
    assert spec.targets["directions"] == {"debit": 0, "credit": 0}
    assert spec.selection["max_per_near_duplicate_group"] == 2

    invalid_specs = [
        {"name": "../bad"},
        {"name": "ok", "max_examples": 0},
        {"name": "ok", "label_sources": {"ai": "trust"}},
        {"name": "ok", "targets": {"directions": {"cash": 1}}},
        {"name": "ok", "targets": {"categories": {"Food": -1}}},
    ]
    for payload in invalid_specs:
        with pytest.raises(builder.DatasetSpecError):
            builder.normalize_dataset_spec(payload)


def test_dataset_builder_spec_path_safety(tmp_path):
    """Verify dataset spec paths stay inside the spec artifact directory."""
    specs_dir = tmp_path / "dataset_specs"
    spec_path = specs_dir / "curated_v1.json"
    write_json(spec_path, {"name": "curated_v1"})

    resolved = builder.resolve_dataset_spec_path("curated_v1.json", specs_dir)
    loaded = builder.load_dataset_spec(resolved)

    assert resolved == spec_path.resolve()
    assert loaded.name == "curated_v1"
    with pytest.raises(builder.DatasetSpecError):
        builder.resolve_dataset_spec_path("../secret.json", specs_dir)
    with pytest.raises(builder.DatasetSpecError):
        builder.resolve_dataset_spec_path("curated_v1.yml", specs_dir)


def test_dataset_builder_opens_sqlite_read_only(tmp_path):
    """Verify preview database connections reject writes."""
    db_path = tmp_path / "finscope.db"
    create_preview_db(db_path)

    conn = builder.open_dataset_builder_database(db_path)
    try:
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("CREATE TABLE should_not_write (id INTEGER)")
    finally:
        conn.close()


def test_dataset_builder_preview_target_accounting_is_deterministic(tmp_path):
    """Verify preview accounting reports found, eligible, and short targets deterministically."""
    db_path = tmp_path / "finscope.db"
    create_preview_db(db_path)
    spec = builder.normalize_dataset_spec(preview_spec_payload())

    first = builder.preview_dataset_build(db_path, spec)
    second = builder.preview_dataset_build(db_path, spec)
    first_report = builder.render_preview_report(first)
    second_report = builder.render_preview_report(second)
    rows = {f"{row.target_type}.{row.name}": row for row in first.target_previews}

    assert first_report == second_report
    assert first.found_candidate_count == 5
    assert first.eligible_candidate_count == 3
    assert first.possible_selected_count == 3
    assert rows["categories.Food"].requested == 2
    assert rows["categories.Food"].found_candidates == 3
    assert rows["categories.Food"].eligible_candidates == 2
    assert rows["categories.Food"].status == "OK"
    assert rows["tags.Reimbursable"].status == "OK"
    assert rows["directions.credit"].status == "OK"
    assert rows["ambiguity_types.ai_unknown"].found_candidates == 1
    assert rows["ambiguity_types.ai_unknown"].eligible_candidates == 0
    assert rows["ambiguity_types.ai_unknown"].status == "short"
    assert "Target preview" in first_report


def test_dataset_builder_builds_valid_draft_artifacts_deterministically(tmp_path):
    """Verify draft builds write valid JSONL and deterministic reports."""
    db_path = tmp_path / "finscope.db"
    datasets_dir = tmp_path / "datasets"
    create_preview_db(db_path)
    spec = builder.normalize_dataset_spec(preview_spec_payload())

    first = builder.build_draft_dataset_from_spec(db_path, spec, datasets_dir=datasets_dir)
    first_records = load_jsonl(first.artifacts.dataset_path)
    first_report = first.artifacts.coverage_report_path.read_text(encoding="utf-8")
    second = builder.build_draft_dataset_from_spec(db_path, spec, datasets_dir=datasets_dir)
    second_records = load_jsonl(second.artifacts.dataset_path)

    summary = validate_dataset.validate_dataset(first.artifacts.dataset_path)

    assert first_records == second_records
    assert first.coverage_report == second.coverage_report
    assert summary.example_count == len(first_records)
    assert summary.privacy_level_counts["redacted_real"] == len(first_records)
    assert first.artifacts.adjudication_path.exists()
    assert first.artifacts.labeling_queue_path.exists()
    assert first.artifacts.spec_used_path.exists()
    labeling_queue = load_jsonl(first.artifacts.labeling_queue_path)
    assert (
        "Draft dataset. Manual review is recommended before using this file as validation or test data." in first_report
    )
    assert "| categories.Food | 2 | 3 | 2 | OK |" in first_report
    assert "Selected examples by category" in first_report
    assert "No-tag versus tagged counts" in first_report
    assert "Labeling queue count: 2" in first_report
    assert "Recommendations" in first_report
    assert all(record["privacy_level"] == "redacted_real" for record in first_records)
    assert all("CAFE RESTAURANT" not in record["transaction"]["description"] for record in first_records)
    assert any(record["transaction"]["description"] == "redacted transaction: restaurant" for record in first_records)
    assert {item["label_status"] for item in labeling_queue} == {"pending"}
    assert any(item["ai_observation"]["failure_type"] == "ai_unknown" for item in labeling_queue)
    assert all(item["expected"] is None for item in labeling_queue)


def test_dataset_builder_selects_manually_corrected_ai_problem_as_draft(tmp_path):
    """Verify AI-corrected-later cases can be drafted only with trusted current labels."""
    db_path = tmp_path / "finscope.db"
    create_preview_db(db_path, include_ai_corrected=True)
    spec_payload = preview_spec_payload()
    spec_payload["targets"] = {"ambiguity_types": {"ai_corrected_later": 1}}
    spec = builder.normalize_dataset_spec(spec_payload)

    result = builder.build_draft_dataset_from_spec(db_path, spec, datasets_dir=tmp_path / "datasets")
    records = load_jsonl(result.artifacts.dataset_path)

    corrected = [record for record in records if record["request_id"] == "db-tx-8"]
    assert corrected
    assert corrected[0]["expected"]["category_id"] == "cat_food"
    assert corrected[0]["coverage"]["ambiguity_type"] == "other"
    assert "AI observation preserved" in corrected[0]["notes"]
    assert not any(item["request_id"] == "ai-problem-8" for item in load_jsonl(result.artifacts.labeling_queue_path))


def test_dataset_builder_reports_missing_ai_evidence(tmp_path):
    """Verify missing AI evidence is reported without failing the build."""
    db_path = tmp_path / "finscope.db"
    create_no_ai_evidence_db(db_path)
    spec = builder.normalize_dataset_spec(preview_spec_payload())

    preview = builder.preview_dataset_build(db_path, spec)
    result = builder.build_draft_dataset_from_spec(db_path, spec, datasets_dir=tmp_path / "datasets")

    assert "ai_evidence" in preview.candidate_pool.unavailable_fields
    assert "AI evidence could not be found in the inferred schema." in result.coverage_report


def test_labeling_queue_validation_labeling_unusable_and_export(tmp_path):
    """Verify queue items can be labeled, marked unusable, and exported safely."""
    db_path = tmp_path / "finscope.db"
    create_preview_db(db_path)
    spec = builder.normalize_dataset_spec(preview_spec_payload())
    result = builder.build_draft_dataset_from_spec(db_path, spec, datasets_dir=tmp_path / "datasets")
    queue_path = result.artifacts.labeling_queue_path
    queue_items = load_jsonl(queue_path)
    label_request_id = queue_items[0]["request_id"]
    unusable_request_id = queue_items[1]["request_id"]

    initial_validation = labeling_queue_service.validate_labeling_queue(queue_path)
    labeling_queue_service.save_manual_label(
        queue_path,
        label_request_id,
        category_id="cat_food",
        tag_ids=[],
        needs_review=True,
    )
    labeling_queue_service.mark_queue_item_unusable(queue_path, unusable_request_id, reason="Insufficient context.")
    updated_validation = labeling_queue_service.validate_labeling_queue(queue_path)
    out_path = tmp_path / "datasets" / "labeled_export.jsonl"
    exported = labeling_queue_service.export_labeled_queue(queue_path, out_path)
    export_summary = validate_dataset.validate_dataset(out_path)
    exported_record = load_jsonl(out_path)[0]

    assert initial_validation.valid is True
    assert initial_validation.pending_count == 2
    assert updated_validation.valid is True
    assert updated_validation.labeled_count == 1
    assert updated_validation.unusable_count == 1
    assert len(exported) == 1
    assert export_summary.example_count == 1
    assert exported_record["expected"]["category_id"] == "cat_food"
    assert "ai_observation" not in exported_record
    assert "Original AI observation" in exported_record["notes"]


def test_labeling_queue_prevents_pending_and_unusable_export(tmp_path):
    """Verify pending or unusable queue items are not exported."""
    queue_path = tmp_path / "queue.jsonl"
    write_jsonl(
        queue_path,
        [
            {
                "request_id": "ai-problem-1",
                "transaction": {
                    "description": "redacted transaction",
                    "merchant": None,
                    "amount": 1.0,
                    "date": "2026-01-01",
                    "account": None,
                    "statement_type": None,
                },
                "candidate_taxonomy": {"categories": [], "tags": []},
                "similar_transactions": [],
                "ai_observation": {
                    "category_id": "cat_unknown",
                    "tag_ids": [],
                    "confidence": 0.2,
                    "needs_review": True,
                    "reason": "Unknown.",
                    "failure_type": "ai_unknown",
                },
                "label_status": "pending",
                "expected": None,
                "label_source": "pending_manual_label",
                "privacy_level": "redacted_real",
                "coverage": {
                    "category": None,
                    "tags": [],
                    "direction": "debit",
                    "statement_type": None,
                    "confidence_band": "low",
                    "ambiguity_type": "ai_unknown",
                },
                "notes": "Pending.",
            }
        ],
    )

    with pytest.raises(ValueError, match="no labeled queue items"):
        labeling_queue_service.export_labeled_queue(queue_path, tmp_path / "out.jsonl")


def test_dataset_builder_reports_shortages_and_suppresses_near_duplicates(tmp_path):
    """Verify shortages and duplicate suppression are reflected in outputs."""
    db_path = tmp_path / "finscope.db"
    create_preview_db(db_path, include_duplicates=True)
    spec_payload = preview_spec_payload()
    spec_payload["targets"] = {
        "categories": {"Food": 4, "Missing": 1},
        "tags": {"Reimbursable": 3},
        "directions": {"debit": 4},
    }
    spec_payload["selection"] = {"max_per_near_duplicate_group": 1}
    spec = builder.normalize_dataset_spec(spec_payload)

    result = builder.build_draft_dataset_from_spec(db_path, spec, datasets_dir=tmp_path / "datasets")
    records = load_jsonl(result.artifacts.dataset_path)
    report = result.artifacts.coverage_report_path.read_text(encoding="utf-8")

    selected_ids = {record["request_id"] for record in records}
    target_rows = {f"{row.target_type}.{row.name}": row for row in result.target_previews}

    assert result.suppressed_duplicate_count > 0
    assert len({"db-tx-1", "db-tx-6", "db-tx-7"} & selected_ids) == 1
    assert target_rows["categories.Missing"].status == "missing"
    assert target_rows["tags.Reimbursable"].status == "short"
    assert "Suppressed duplicate groups" in report
    assert "categories.Missing: missing" in report
    assert "Add manually labeled or clearly synthetic examples" in report


def test_dataset_builder_raw_redaction_toggle_and_database_read_only(tmp_path):
    """Verify raw output is opt-in and the source database is not modified."""
    db_path = tmp_path / "finscope.db"
    create_preview_db(db_path)
    before_tables = table_names(db_path)
    spec_payload = preview_spec_payload()
    spec_payload["redact"] = False
    spec = builder.normalize_dataset_spec(spec_payload)

    result = builder.build_draft_dataset_from_spec(db_path, spec, datasets_dir=tmp_path / "datasets")
    records = load_jsonl(result.artifacts.dataset_path)
    after_tables = table_names(db_path)

    assert before_tables == after_tables
    assert any(record["transaction"]["description"] == "CAFE RESTAURANT" for record in records)
    assert all(record["privacy_level"] == "raw_real" for record in records)


def table_names(path: Path) -> tuple[str, ...]:
    """Return user table names from a SQLite database."""
    conn = sqlite3.connect(path)
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
        return tuple(str(row[0]) for row in rows)
    finally:
        conn.close()
