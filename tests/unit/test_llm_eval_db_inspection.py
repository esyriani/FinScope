"""Unit tests for offline LLM categorization database inspection."""

import sqlite3

from evals.llm_categorization.tools import inspect_db


def create_inspection_database(db_path):
    """Create a small FinScope-like SQLite database for inspection tests."""
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
            name TEXT NOT NULL
        );
        CREATE TABLE statement_types (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            parser_type TEXT
        );
        CREATE TABLE statements (
            id INTEGER PRIMARY KEY,
            statement_type_id INTEGER,
            account_id INTEGER,
            filename TEXT
        );
        CREATE TABLE transactions (
            id INTEGER PRIMARY KEY,
            description TEXT,
            amount REAL NOT NULL,
            category TEXT,
            category_id INTEGER,
            category_source TEXT,
            category_confidence REAL,
            category_metadata TEXT,
            category_rule_id INTEGER,
            needs_review INTEGER,
            account_id INTEGER,
            statement_id INTEGER,
            merchant_id INTEGER
        );
        CREATE TABLE transaction_tags (
            transaction_id INTEGER NOT NULL,
            tag_id INTEGER NOT NULL
        );
        CREATE TABLE category_rules (
            id INTEGER PRIMARY KEY,
            keyword TEXT,
            category TEXT,
            source TEXT
        );
        CREATE TABLE audit_log (
            id INTEGER PRIMARY KEY,
            action TEXT,
            created_at TEXT
        );
        """)
    conn.executemany(
        "INSERT INTO categories (id, name, description, instruction) VALUES (?, ?, '', NULL)",
        [(1, "UNKNOWN"), (2, "Income"), (3, "Transfers"), (4, "Groceries")],
    )
    conn.executemany(
        "INSERT INTO tags (id, name, description, instruction) VALUES (?, ?, '', NULL)",
        [(1, "Tax"), (2, "Reimbursable")],
    )
    conn.executemany("INSERT INTO accounts (id, name) VALUES (?, ?)", [(1, "Private Checking")])
    conn.executemany(
        "INSERT INTO statement_types (id, name, parser_type) VALUES (?, ?, ?)",
        [(1, "Checking", "bank_account")],
    )
    conn.executemany(
        "INSERT INTO statements (id, statement_type_id, account_id, filename) VALUES (?, ?, ?, ?)",
        [(1, 1, 1, "private.csv")],
    )
    conn.executemany(
        """
        INSERT INTO transactions (
            id, description, amount, category, category_id, category_source,
            category_confidence, category_metadata, category_rule_id, needs_review,
            account_id, statement_id, merchant_id
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (1, "PRIVATE PAYROLL", -1000.0, "Income", 2, "manual", 0.99, "{}", None, 0, 1, 1, 10),
            (2, "PRIVATE SHOP", 42.0, "UNKNOWN", 1, "unknown", None, "{}", None, 1, 1, 1, 11),
            (3, "PRIVATE TRANSFER", 100.0, "Transfers", 3, "rule", 0.95, "{}", 1, 0, 1, 1, 12),
        ],
    )
    conn.executemany("INSERT INTO transaction_tags (transaction_id, tag_id) VALUES (?, ?)", [(1, 1), (2, 2)])
    conn.executemany(
        "INSERT INTO category_rules (id, keyword, category, source) VALUES (?, ?, ?, ?)",
        [(1, "PRIVATE", "Transfers", "manual")],
    )
    conn.execute("INSERT INTO audit_log (action, created_at) VALUES ('manual_edit', '2026-05-01')")
    conn.commit()
    conn.close()


def test_inspect_database_writes_readiness_report_without_raw_descriptions(tmp_path):
    """Verify inspection reports inferred schema, aggregates, and no raw transaction text."""
    db_path = tmp_path / "finscope.db"
    out_path = tmp_path / "report.md"
    create_inspection_database(db_path)

    exit_code = inspect_db.main(["--db", str(db_path), "--out", str(out_path)])
    report = out_path.read_text(encoding="utf-8")

    assert exit_code == 0
    assert "# LLM Categorization Database Inspection Report" in report
    assert "Inferred table `transactions`" in report
    assert "## Missing or not found" in report
    assert "## Aggregate Counts" in report
    assert "- Total transactions: 3" in report
    assert "- `Income`: 1" in report
    assert "- `Tax`: 1" in report
    assert "- `credit_negative`: 1" in report
    assert "- `debit_positive`: 2" in report
    assert "- `Checking`: 3" in report
    assert "- `account_id 1`: 3" in report
    assert "- Likely `UNKNOWN` examples exist: yes" in report
    assert "- Likely `needs_review` examples exist: yes" in report
    assert "## Potential evaluation risks" in report
    assert "PRIVATE PAYROLL" not in report
    assert "PRIVATE SHOP" not in report
    assert "Private Checking" not in report


def test_open_readonly_sqlite_rejects_writes(tmp_path):
    """Verify the inspector connection cannot mutate the inspected database."""
    db_path = tmp_path / "finscope.db"
    create_inspection_database(db_path)

    conn = inspect_db.open_readonly_sqlite(db_path)
    try:
        try:
            conn.execute("INSERT INTO categories (id, name) VALUES (99, 'Blocked')")
        except sqlite3.OperationalError as exc:
            message = str(exc).lower()
        else:
            raise AssertionError("read-only inspection connection allowed a write")
    finally:
        conn.close()

    assert "readonly" in message or "query only" in message
