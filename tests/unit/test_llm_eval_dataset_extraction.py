"""Unit tests for draft LLM categorization dataset extraction."""

import json
import sqlite3

from evals.llm_categorization.tools import build_dataset_from_db, validate_dataset


def create_extraction_database(db_path):
    """Create a FinScope-like SQLite database for draft extraction tests."""
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE categories (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            description TEXT,
            instruction TEXT
        );
        CREATE TABLE tags (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            description TEXT,
            instruction TEXT
        );
        CREATE TABLE accounts (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            account_type TEXT
        );
        CREATE TABLE statement_types (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            parser_type TEXT
        );
        CREATE TABLE statements (
            id INTEGER PRIMARY KEY,
            statement_type_id INTEGER,
            account_id INTEGER
        );
        CREATE TABLE transactions (
            id INTEGER PRIMARY KEY,
            statement_id INTEGER,
            account_id INTEGER,
            merchant_id INTEGER,
            tx_date TEXT,
            description TEXT,
            amount REAL NOT NULL,
            category TEXT,
            category_id INTEGER,
            needs_review INTEGER,
            category_source TEXT,
            category_confidence REAL,
            category_rule_id INTEGER,
            category_metadata TEXT,
            reviewed_at TEXT,
            ignored INTEGER DEFAULT 0
        );
        CREATE TABLE transaction_tags (
            transaction_id INTEGER NOT NULL,
            tag_id INTEGER NOT NULL,
            source TEXT,
            rule_id INTEGER
        );
        CREATE TABLE category_rules (
            id INTEGER PRIMARY KEY,
            keyword TEXT,
            category TEXT,
            category_id INTEGER,
            source TEXT,
            ai_approved INTEGER
        );
        """)
    conn.executemany(
        "INSERT INTO categories (id, name, description, instruction) VALUES (?, ?, ?, ?)",
        [
            (1, "UNKNOWN", "Unresolved transactions.", None),
            (2, "Income", "Incoming payroll and credits.", "Use for salary deposits."),
            (3, "Food", "Food purchases.", None),
            (4, "Transfers", "Account transfers.", None),
        ],
    )
    conn.executemany(
        "INSERT INTO tags (id, name, description, instruction) VALUES (?, ?, ?, ?)",
        [
            (1, "Tax", "Tax-related items.", None),
            (2, "Reimbursable", "Expenses to reimburse.", None),
        ],
    )
    conn.execute("INSERT INTO accounts (id, name, account_type) VALUES (1, 'Private Checking', 'checking')")
    conn.execute("INSERT INTO statement_types (id, name, parser_type) VALUES (1, 'Checking account', 'bank_account')")
    conn.execute("INSERT INTO statements (id, statement_type_id, account_id) VALUES (1, 1, 1)")
    conn.executemany(
        """
        INSERT INTO transactions (
            id, statement_id, account_id, merchant_id, tx_date, description, amount,
            category, category_id, needs_review, category_source, category_confidence,
            category_rule_id, category_metadata, reviewed_at, ignored
        )
        VALUES (?, 1, 1, ?, '2026-05-01', ?, ?, ?, ?, ?, ?, ?, ?, '{}', ?, 0)
        """,
        [
            (1, 10, "PRIVATE GROCERY STORE 1234", 45.50, "Food", 3, 0, "manual", 0.99, None, "2026-05-02"),
            (2, 11, "PRIVATE PAYROLL EMPLOYER", -1200.00, "Income", 2, 0, "rule", 0.98, 1, None),
            (3, 12, "PRIVATE TRANSFER ONLINE", 100.00, "Transfers", 4, 0, "history", 0.91, None, None),
            (4, 12, "PRIVATE TRANSFER ONLINE", 125.00, "Transfers", 4, 0, "history", 0.92, None, None),
            (5, 12, "PRIVATE TRANSFER ONLINE", 150.00, "Transfers", 4, 0, "history", 0.93, None, None),
            (6, 13, "PRIVATE MYSTERY CHARGE", 30.00, "UNKNOWN", 1, 1, "unknown", None, None, None),
            (7, 14, "PRIVATE AI RESTAURANT", 21.00, "Food", 3, 0, "ai", 0.88, None, None),
        ],
    )
    conn.executemany(
        "INSERT INTO transaction_tags (transaction_id, tag_id, source, rule_id) VALUES (?, ?, 'manual', NULL)",
        [(1, 2), (2, 1)],
    )
    conn.execute(
        "INSERT INTO category_rules (id, keyword, category, category_id, source, ai_approved) VALUES (1, 'PAYROLL', 'Income', 2, 'manual', 0)"
    )
    conn.commit()
    conn.close()


def read_jsonl(path):
    """Read JSONL records for assertions."""
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_build_dataset_from_db_writes_redacted_valid_draft_and_adjudication(tmp_path):
    """Verify extractor writes valid redacted draft examples and uncertain examples separately."""
    db_path = tmp_path / "finscope.db"
    out_path = tmp_path / "draft.jsonl"
    coverage_path = tmp_path / "coverage.md"
    adjudication_path = tmp_path / "adjudication.jsonl"
    create_extraction_database(db_path)

    exit_code = build_dataset_from_db.main(
        [
            "--db",
            str(db_path),
            "--out",
            str(out_path),
            "--coverage-report",
            str(coverage_path),
            "--adjudication-out",
            str(adjudication_path),
            "--max-examples",
            "10",
        ]
    )

    draft = read_jsonl(out_path)
    adjudication = read_jsonl(adjudication_path)
    coverage = coverage_path.read_text(encoding="utf-8")
    summary = validate_dataset.validate_dataset(out_path)

    assert exit_code == 0
    assert summary.example_count == len(draft)
    assert len(draft) >= 4
    assert len(adjudication) == 1
    assert adjudication[0]["request_id"] == "db-tx-7"
    assert "AI-only" in adjudication[0]["notes"]
    assert all(record["privacy_level"] == "redacted_real" for record in draft)
    assert all(record["candidate_taxonomy"]["categories"] for record in draft)
    assert all(record["candidate_taxonomy"]["tags"] for record in draft)
    assert any(record["label_source"] == "manual_edit" for record in draft)
    assert any(record["label_source"] == "high_confidence_rule" for record in draft)
    assert any(record["label_source"] == "stable_history" for record in draft)
    assert any(record["coverage"]["category"] == "UNKNOWN" for record in draft)
    assert any(record["similar_transactions"] for record in draft)
    assert "PRIVATE" not in out_path.read_text(encoding="utf-8")
    assert "EMPLOYER" not in out_path.read_text(encoding="utf-8")
    assert "# Draft Dataset Coverage Report" in coverage
    assert "Selected examples by category" in coverage
    assert "Missing benchmark strata" in coverage


def test_build_dataset_from_db_rejects_non_positive_max_examples(tmp_path, capsys):
    """Verify CLI rejects unusable selection limits before opening the database."""
    exit_code = build_dataset_from_db.main(
        [
            "--db",
            str(tmp_path / "missing.db"),
            "--out",
            str(tmp_path / "draft.jsonl"),
            "--coverage-report",
            str(tmp_path / "coverage.md"),
            "--adjudication-out",
            str(tmp_path / "adjudication.jsonl"),
            "--max-examples",
            "0",
        ]
    )

    output = capsys.readouterr()

    assert exit_code == 1
    assert "--max-examples must be positive" in output.err
