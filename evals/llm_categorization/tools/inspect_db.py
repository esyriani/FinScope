"""Read-only SQLite inspection for LLM categorization evaluation planning.

This offline utility introspects a FinScope-like SQLite database and writes a
human-readable coverage report. It deliberately avoids importing production
application modules, mutating the database, printing raw merchant names, or
extracting a final evaluation dataset.
"""

import argparse
import sqlite3
import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

MIN_EXAMPLES_PER_CATEGORY = 5
MIN_EXAMPLES_PER_TAG = 5
MAX_REPORT_ROWS = 40

CONCEPT_NAMES = (
    "transactions",
    "categories",
    "tags",
    "transaction-category assignment",
    "transaction-tag assignment",
    "categorization rules",
    "review status",
    "AI confidence or evidence",
    "manual edits or audit history",
)
BUILTIN_CONCEPTS = ("transfer", "income", "reimbursement", "reimbursable", "rental", "tax")


@dataclass(frozen=True)
class ColumnInfo:
    """Represent one SQLite table column discovered by introspection."""

    name: str
    declared_type: str
    not_null: bool
    primary_key: bool


@dataclass(frozen=True)
class TableInfo:
    """Represent one SQLite table and its columns."""

    name: str
    columns: tuple[ColumnInfo, ...]

    def column_names(self) -> tuple[str, ...]:
        """Return the table column names in declaration order."""
        return tuple(column.name for column in self.columns)

    def has_column(self, column_name: str) -> bool:
        """Return whether the table has a column with the exact name."""
        return column_name in self.column_names()


@dataclass(frozen=True)
class RoleCandidates:
    """Represent inferred tables used for aggregate reporting."""

    transactions: TableInfo | None
    categories: TableInfo | None
    tags: TableInfo | None
    transaction_tags: TableInfo | None
    category_rules: TableInfo | None
    accounts: TableInfo | None
    statements: TableInfo | None
    statement_types: TableInfo | None
    audit: TableInfo | None


@dataclass(frozen=True)
class InspectionContext:
    """Represent all introspected schema state needed for report rendering."""

    db_path: Path
    tables: tuple[TableInfo, ...]
    roles: RoleCandidates
    relevant_schema: Mapping[str, tuple[tuple[str, tuple[str, ...]], ...]]
    missing_concepts: tuple[str, ...]


@dataclass(frozen=True)
class AggregateReport:
    """Represent aggregate counts that are safe to print."""

    total_transactions: int | None
    category_counts: tuple[tuple[str, int], ...]
    tag_counts: tuple[tuple[str, int], ...]
    direction_counts: tuple[tuple[str, int], ...]
    statement_type_counts: tuple[tuple[str, int], ...]
    account_counts: tuple[tuple[str, int], ...]
    unknown_count: int | None
    review_count: int | None
    evidence_counts: tuple[tuple[str, int], ...]
    confidence_count: int | None


class InspectionError(RuntimeError):
    """Represent a database inspection failure."""


def open_readonly_sqlite(db_path: Path) -> sqlite3.Connection:
    """Open a SQLite database in read-only mode with query-only protection."""
    if not db_path.exists():
        raise InspectionError(f"database not found: {db_path}")
    uri_path = quote(str(db_path.resolve()).replace("\\", "/"), safe="/:")
    connection = sqlite3.connect(f"file:{uri_path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    return connection


def introspect_schema(conn: sqlite3.Connection) -> tuple[TableInfo, ...]:
    """Return all user tables and columns from SQLite schema metadata."""
    rows = conn.execute("""
        SELECT name
        FROM sqlite_master
        WHERE type = 'table'
          AND name NOT LIKE 'sqlite_%'
        ORDER BY name
        """).fetchall()
    tables = []
    for row in rows:
        table_name = str(row["name"])
        column_rows = conn.execute(f"PRAGMA table_info({quote_identifier(table_name)})").fetchall()
        columns = tuple(
            ColumnInfo(
                name=str(column["name"]),
                declared_type=str(column["type"] or ""),
                not_null=bool(column["notnull"]),
                primary_key=bool(column["pk"]),
            )
            for column in column_rows
        )
        tables.append(TableInfo(name=table_name, columns=columns))
    return tuple(tables)


def infer_roles(tables: Sequence[TableInfo]) -> RoleCandidates:
    """Infer likely table roles from table and column names."""
    return RoleCandidates(
        transactions=best_table(tables, transaction_score),
        categories=best_table(tables, category_score),
        tags=best_table(tables, tag_score),
        transaction_tags=best_table(tables, transaction_tag_score),
        category_rules=best_table(tables, category_rule_score),
        accounts=best_table(tables, account_score),
        statements=best_table(tables, statement_score),
        statement_types=best_table(tables, statement_type_score),
        audit=best_table(tables, audit_score),
    )


def best_table(tables: Sequence[TableInfo], scorer: Any) -> TableInfo | None:
    """Return the highest-scoring inferred table when the score is positive."""
    scored = sorted(((scorer(table), table.name, table) for table in tables), key=lambda item: (-item[0], item[1]))
    if not scored or scored[0][0] <= 0:
        return None
    return scored[0][2]


def transaction_score(table: TableInfo) -> int:
    """Return an inference score for the transaction ledger table."""
    name = table.name.lower()
    columns = lower_columns(table)
    score = 0
    if "transaction" in name and "tag" not in name:
        score += 5
    if "amount" in columns:
        score += 5
    if columns & {"description", "merchant_id", "merchant", "tx_date", "date"}:
        score += 3
    if columns & {"category", "category_id", "category_source"}:
        score += 3
    if "rule" in name or "tag" in name:
        score -= 4
    return score


def category_score(table: TableInfo) -> int:
    """Return an inference score for the category taxonomy table."""
    name = table.name.lower()
    columns = lower_columns(table)
    score = 0
    if "categor" in name:
        score += 5
    if {"id", "name"} <= columns:
        score += 4
    if "rule" in name or "transaction" in name:
        score -= 5
    return score


def tag_score(table: TableInfo) -> int:
    """Return an inference score for the tag taxonomy table."""
    name = table.name.lower()
    columns = lower_columns(table)
    score = 0
    if name == "tags" or name.endswith("_tags"):
        score += 5
    if "tag" in name:
        score += 2
    if {"id", "name"} <= columns:
        score += 4
    if "transaction" in name:
        score -= 5
    return score


def transaction_tag_score(table: TableInfo) -> int:
    """Return an inference score for the transaction-tag assignment table."""
    name = table.name.lower()
    columns = lower_columns(table)
    score = 0
    if "transaction" in name and "tag" in name:
        score += 6
    if {"transaction_id", "tag_id"} <= columns:
        score += 6
    return score


def category_rule_score(table: TableInfo) -> int:
    """Return an inference score for the categorization rules table."""
    name = table.name.lower()
    columns = lower_columns(table)
    score = 0
    if "rule" in name and "categor" in name:
        score += 6
    if columns & {"keyword", "pattern", "category", "category_id"}:
        score += 4
    if "transaction" in name:
        score -= 4
    return score


def account_score(table: TableInfo) -> int:
    """Return an inference score for the accounts table."""
    name = table.name.lower()
    columns = lower_columns(table)
    score = 0
    if "account" in name:
        score += 5
    if {"id", "name"} <= columns:
        score += 2
    if "transaction" in name:
        score -= 5
    return score


def statement_score(table: TableInfo) -> int:
    """Return an inference score for uploaded statement rows."""
    name = table.name.lower()
    columns = lower_columns(table)
    score = 0
    if "statement" in name and "type" not in name:
        score += 5
    if columns & {"statement_type_id", "filename", "uploaded_at"}:
        score += 4
    return score


def statement_type_score(table: TableInfo) -> int:
    """Return an inference score for statement type metadata."""
    name = table.name.lower()
    columns = lower_columns(table)
    score = 0
    if "statement" in name and "type" in name:
        score += 6
    if {"id", "name"} <= columns:
        score += 2
    return score


def audit_score(table: TableInfo) -> int:
    """Return an inference score for audit or manual edit history."""
    name = table.name.lower()
    columns = lower_columns(table)
    score = 0
    if "audit" in name:
        score += 5
    if columns & {"action", "event_type", "user_id", "created_at"}:
        score += 2
    return score


def lower_columns(table: TableInfo) -> set[str]:
    """Return lower-case column names for inference."""
    return {column.name.lower() for column in table.columns}


def infer_relevant_schema(
    tables: Sequence[TableInfo], roles: RoleCandidates
) -> dict[str, tuple[tuple[str, tuple[str, ...]], ...]]:
    """Return inferred relevant tables and columns by concept."""
    helpers = {
        "transactions": lambda: transaction_schema_matches(roles),
        "categories": lambda: category_schema_matches(roles),
        "tags": lambda: tag_schema_matches(tables, roles),
        "transaction-category assignment": lambda: transaction_category_schema_matches(roles),
        "transaction-tag assignment": lambda: transaction_tag_schema_matches(tables, roles),
        "categorization rules": lambda: categorization_rule_schema_matches(tables, roles),
        "review status": lambda: table_column_matches(roles.transactions, ("needs_review", "reviewed_at")),
        "AI confidence or evidence": lambda: ai_evidence_schema_matches(roles),
        "manual edits or audit history": lambda: manual_audit_schema_matches(tables, roles),
    }
    return {concept: tuple(sorted(helpers[concept]())) for concept in CONCEPT_NAMES}


def transaction_schema_matches(roles: RoleCandidates) -> list[tuple[str, tuple[str, ...]]]:
    """Return schema matches relevant to transaction payload context."""
    matches = []
    matches.extend(table_column_matches(roles.transactions, None))
    matches.extend(table_column_matches(roles.accounts, ("id", "account_type", "paid_from_account_id")))
    matches.extend(
        table_column_matches(
            roles.statements,
            ("id", "account_id", "statement_type_id", "import_status", "llm_candidate_count", "uploaded_at"),
        )
    )
    matches.extend(table_column_matches(roles.statement_types, ("id", "name", "parser_type", "import_mode")))
    return matches


def category_schema_matches(roles: RoleCandidates) -> list[tuple[str, tuple[str, ...]]]:
    """Return schema matches relevant to categories."""
    matches = []
    matches.extend(table_column_matches(roles.categories, None))
    matches.extend(
        table_column_matches(
            roles.transactions,
            ("category", "category_id", "category_source", "category_confidence", "category_metadata"),
        )
    )
    matches.extend(table_column_matches(roles.category_rules, ("category", "category_id", "source", "ai_approved")))
    return matches


def tag_schema_matches(tables: Sequence[TableInfo], roles: RoleCandidates) -> list[tuple[str, tuple[str, ...]]]:
    """Return schema matches relevant to tags."""
    matches = []
    matches.extend(table_column_matches(roles.tags, None))
    matches.extend(table_column_matches(roles.transaction_tags, None))
    matches.extend(table_column_matches(find_table(tables, "category_rule_tags"), None))
    return matches


def transaction_category_schema_matches(roles: RoleCandidates) -> list[tuple[str, tuple[str, ...]]]:
    """Return schema matches relevant to transaction-category assignments."""
    matches = []
    matches.extend(
        table_column_matches(
            roles.transactions,
            (
                "category",
                "category_id",
                "category_source",
                "category_confidence",
                "category_rule_id",
                "category_metadata",
                "needs_review",
                "reviewed_at",
            ),
        )
    )
    matches.extend(
        table_column_matches(roles.category_rules, ("id", "category", "category_id", "source", "ai_approved"))
    )
    return matches


def transaction_tag_schema_matches(
    tables: Sequence[TableInfo], roles: RoleCandidates
) -> list[tuple[str, tuple[str, ...]]]:
    """Return schema matches relevant to transaction-tag assignments."""
    matches = []
    matches.extend(table_column_matches(roles.transaction_tags, None))
    matches.extend(table_column_matches(roles.tags, ("id", "name", "builtin_key", "description", "instruction")))
    matches.extend(table_column_matches(find_table(tables, "category_rule_tags"), None))
    return matches


def categorization_rule_schema_matches(
    tables: Sequence[TableInfo], roles: RoleCandidates
) -> list[tuple[str, tuple[str, ...]]]:
    """Return schema matches relevant to categorization rules."""
    matches = []
    matches.extend(table_column_matches(roles.category_rules, None))
    matches.extend(table_column_matches(find_table(tables, "category_rule_tags"), None))
    matches.extend(table_column_matches(roles.transactions, ("category_rule_id",)))
    return matches


def ai_evidence_schema_matches(roles: RoleCandidates) -> list[tuple[str, tuple[str, ...]]]:
    """Return schema matches relevant to AI confidence or evidence."""
    matches = []
    matches.extend(
        table_column_matches(
            roles.transactions,
            ("category_source", "category_confidence", "category_metadata", "category_rule_id"),
        )
    )
    matches.extend(table_column_matches(roles.statements, ("llm_candidate_count",)))
    matches.extend(table_column_matches(roles.category_rules, ("ai_approved",)))
    return matches


def manual_audit_schema_matches(
    tables: Sequence[TableInfo], roles: RoleCandidates
) -> list[tuple[str, tuple[str, ...]]]:
    """Return schema matches relevant to manual edits or audit history."""
    matches = []
    matches.extend(table_column_matches(roles.audit, None))
    matches.extend(table_column_matches(roles.transactions, ("category_source", "reviewed_at")))
    matches.extend(table_column_matches(roles.transaction_tags, ("source", "rule_id", "assigned_at")))
    matches.extend(table_column_matches(roles.category_rules, ("source", "created_at")))
    matches.extend(table_column_matches(find_table(tables, "category_rule_tags"), ("rule_id", "tag_id")))
    return matches


def table_column_matches(table: TableInfo | None, columns: Sequence[str] | None) -> list[tuple[str, tuple[str, ...]]]:
    """Return table/column matches for a known table role."""
    if table is None:
        return []
    if columns is None:
        return [(table.name, table.column_names())]
    table_columns = table.column_names()
    lower_to_actual = {column_name.lower(): column_name for column_name in table_columns}
    matches = tuple(lower_to_actual[column.lower()] for column in columns if column.lower() in lower_to_actual)
    return [(table.name, matches)] if matches else []


def find_table(tables: Sequence[TableInfo], table_name: str) -> TableInfo | None:
    """Return a table by exact case-insensitive name."""
    for table in tables:
        if table.name.lower() == table_name.lower():
            return table
    return None


def missing_concepts(relevant_schema: Mapping[str, tuple[tuple[str, tuple[str, ...]], ...]]) -> tuple[str, ...]:
    """Return concepts without any inferred schema support."""
    return tuple(concept for concept, matches in relevant_schema.items() if not matches)


def inspect_database(db_path: Path) -> str:
    """Inspect a SQLite database and return a Markdown coverage report."""
    conn = open_readonly_sqlite(db_path)
    try:
        tables = introspect_schema(conn)
        roles = infer_roles(tables)
        relevant_schema = infer_relevant_schema(tables, roles)
        context = InspectionContext(
            db_path=db_path,
            tables=tables,
            roles=roles,
            relevant_schema=relevant_schema,
            missing_concepts=missing_concepts(relevant_schema),
        )
        aggregates = compute_aggregates(conn, context)
    finally:
        conn.close()
    return render_report(context, aggregates)


def compute_aggregates(conn: sqlite3.Connection, context: InspectionContext) -> AggregateReport:
    """Compute safe aggregate counts from inferred schema roles."""
    transactions = context.roles.transactions
    if transactions is None:
        return AggregateReport(
            total_transactions=None,
            category_counts=(),
            tag_counts=(),
            direction_counts=(),
            statement_type_counts=(),
            account_counts=(),
            unknown_count=None,
            review_count=None,
            evidence_counts=(),
            confidence_count=None,
        )

    total_transactions = count_rows(conn, transactions.name)
    category_counts = transaction_category_counts(conn, context)
    tag_counts = transaction_tag_counts(conn, context)
    direction_counts = transaction_direction_counts(conn, transactions)
    statement_type_counts = transaction_statement_type_counts(conn, context)
    account_counts = transaction_account_counts(conn, context)
    unknown_count = transaction_unknown_count(conn, context)
    review_count = transaction_review_count(conn, transactions)
    evidence_counts = transaction_evidence_counts(conn, transactions)
    confidence_count = non_null_count(
        conn, transactions, first_existing_column(transactions, ("category_confidence", "confidence"))
    )
    return AggregateReport(
        total_transactions=total_transactions,
        category_counts=category_counts,
        tag_counts=tag_counts,
        direction_counts=direction_counts,
        statement_type_counts=statement_type_counts,
        account_counts=account_counts,
        unknown_count=unknown_count,
        review_count=review_count,
        evidence_counts=evidence_counts,
        confidence_count=confidence_count,
    )


def count_rows(conn: sqlite3.Connection, table_name: str) -> int:
    """Return row count for a table."""
    row = conn.execute(f"SELECT COUNT(*) AS count FROM {quote_identifier(table_name)}").fetchone()
    return int(row["count"])


def transaction_category_counts(conn: sqlite3.Connection, context: InspectionContext) -> tuple[tuple[str, int], ...]:
    """Return transaction counts by category without printing transaction descriptions."""
    transactions = context.roles.transactions
    if transactions is None:
        return ()
    if transactions.has_column("category"):
        return grouped_counts(conn, transactions.name, "category", label_fallback="(null)")
    if transactions.has_column("category_id") and context.roles.categories is not None:
        return joined_name_counts(
            conn,
            left_table=transactions.name,
            left_fk="category_id",
            right_table=context.roles.categories.name,
            right_id="id",
            right_name=first_existing_column(context.roles.categories, ("name", "label")),
        )
    return ()


def transaction_tag_counts(conn: sqlite3.Connection, context: InspectionContext) -> tuple[tuple[str, int], ...]:
    """Return transaction counts by tag when a transaction-tag assignment table exists."""
    assignment = context.roles.transaction_tags
    tags = context.roles.tags
    if assignment is None:
        return ()
    tag_id_column = first_existing_column(assignment, ("tag_id", "tag"))
    if tag_id_column is None:
        return ()
    if tags is None:
        return grouped_counts(conn, assignment.name, tag_id_column, label_fallback="(null)")
    tag_name_column = first_existing_column(tags, ("name", "label"))
    return joined_name_counts(
        conn,
        left_table=assignment.name,
        left_fk=tag_id_column,
        right_table=tags.name,
        right_id="id",
        right_name=tag_name_column,
    )


def transaction_direction_counts(conn: sqlite3.Connection, transactions: TableInfo) -> tuple[tuple[str, int], ...]:
    """Return transaction counts by signed amount direction."""
    amount_column = first_existing_column(transactions, ("amount", "value", "signed_amount"))
    if amount_column is None:
        return ()
    sql = f"""
        SELECT
            CASE
                WHEN {quote_identifier(amount_column)} > 0 THEN 'debit_positive'
                WHEN {quote_identifier(amount_column)} < 0 THEN 'credit_negative'
                ELSE 'zero'
            END AS label,
            COUNT(*) AS count
        FROM {quote_identifier(transactions.name)}
        GROUP BY label
        ORDER BY label
    """
    return tuple((str(row["label"]), int(row["count"])) for row in conn.execute(sql).fetchall())


def transaction_statement_type_counts(
    conn: sqlite3.Connection, context: InspectionContext
) -> tuple[tuple[str, int], ...]:
    """Return transaction counts by statement type when inferable."""
    transactions = context.roles.transactions
    statements = context.roles.statements
    statement_types = context.roles.statement_types
    if transactions is None:
        return ()
    if transactions.has_column("statement_type"):
        return grouped_counts(conn, transactions.name, "statement_type", label_fallback="(null)")
    if not (transactions.has_column("statement_id") and statements is not None):
        return ()
    if statement_types is not None and statements.has_column("statement_type_id"):
        type_name_column = first_existing_column(statement_types, ("name", "parser_type", "id"))
        if type_name_column is None:
            return ()
        sql = f"""
            SELECT COALESCE(CAST(st.{quote_identifier(type_name_column)} AS TEXT), '(null)') AS label,
                   COUNT(*) AS count
            FROM {quote_identifier(transactions.name)} AS tx
            LEFT JOIN {quote_identifier(statements.name)} AS s
              ON tx.{quote_identifier("statement_id")} = s.{quote_identifier("id")}
            LEFT JOIN {quote_identifier(statement_types.name)} AS st
              ON s.{quote_identifier("statement_type_id")} = st.{quote_identifier("id")}
            GROUP BY label
            ORDER BY count DESC, label
        """
        return count_rows_from_query(conn, sql)
    if statements.has_column("statement_type_id"):
        sql = f"""
            SELECT COALESCE(CAST(s.{quote_identifier("statement_type_id")} AS TEXT), '(null)') AS label,
                   COUNT(*) AS count
            FROM {quote_identifier(transactions.name)} AS tx
            LEFT JOIN {quote_identifier(statements.name)} AS s
              ON tx.{quote_identifier("statement_id")} = s.{quote_identifier("id")}
            GROUP BY label
            ORDER BY count DESC, label
        """
        return count_rows_from_query(conn, sql)
    return ()


def transaction_account_counts(conn: sqlite3.Connection, context: InspectionContext) -> tuple[tuple[str, int], ...]:
    """Return transaction counts by account identifier without printing account names."""
    transactions = context.roles.transactions
    if transactions is None:
        return ()
    if transactions.has_column("account_id"):
        return grouped_counts(conn, transactions.name, "account_id", label_fallback="(null)", prefix="account_id ")
    return ()


def transaction_unknown_count(conn: sqlite3.Connection, context: InspectionContext) -> int | None:
    """Return count of likely UNKNOWN transactions when inferable."""
    transactions = context.roles.transactions
    if transactions is None:
        return None
    if transactions.has_column("category"):
        row = conn.execute(f"""
            SELECT COUNT(*) AS count
            FROM {quote_identifier(transactions.name)}
            WHERE UPPER(CAST({quote_identifier("category")} AS TEXT)) = 'UNKNOWN'
            """).fetchone()
        return int(row["count"])
    if transactions.has_column("category_id") and context.roles.categories is not None:
        category_name_column = first_existing_column(context.roles.categories, ("name", "label"))
        if category_name_column is None:
            return None
        row = conn.execute(f"""
            SELECT COUNT(*) AS count
            FROM {quote_identifier(transactions.name)} AS tx
            JOIN {quote_identifier(context.roles.categories.name)} AS c
              ON tx.{quote_identifier("category_id")} = c.{quote_identifier("id")}
            WHERE UPPER(CAST(c.{quote_identifier(category_name_column)} AS TEXT)) = 'UNKNOWN'
            """).fetchone()
        return int(row["count"])
    return None


def transaction_review_count(conn: sqlite3.Connection, transactions: TableInfo) -> int | None:
    """Return count of likely review-needed transactions when inferable."""
    review_column = first_existing_column(transactions, ("needs_review", "review_required"))
    if review_column is not None:
        row = conn.execute(f"""
            SELECT COUNT(*) AS count
            FROM {quote_identifier(transactions.name)}
            WHERE {quote_identifier(review_column)} IN (1, '1', 'true', 'TRUE', 'yes', 'YES')
            """).fetchone()
        return int(row["count"])
    if transactions.has_column("reviewed_at"):
        row = conn.execute(f"""
            SELECT COUNT(*) AS count
            FROM {quote_identifier(transactions.name)}
            WHERE {quote_identifier("reviewed_at")} IS NOT NULL
            """).fetchone()
        return int(row["count"])
    return None


def transaction_evidence_counts(conn: sqlite3.Connection, transactions: TableInfo) -> tuple[tuple[str, int], ...]:
    """Return counts by categorization evidence/source column when inferable."""
    source_column = first_existing_column(transactions, ("category_source", "source", "evidence_type"))
    if source_column is None:
        return ()
    return grouped_counts(conn, transactions.name, source_column, label_fallback="(null)")


def non_null_count(conn: sqlite3.Connection, table: TableInfo, column_name: str | None) -> int | None:
    """Return non-null count for a column when available."""
    if column_name is None:
        return None
    row = conn.execute(f"""
        SELECT COUNT(*) AS count
        FROM {quote_identifier(table.name)}
        WHERE {quote_identifier(column_name)} IS NOT NULL
        """).fetchone()
    return int(row["count"])


def grouped_counts(
    conn: sqlite3.Connection,
    table_name: str,
    column_name: str,
    *,
    label_fallback: str,
    prefix: str = "",
) -> tuple[tuple[str, int], ...]:
    """Return grouped counts for a non-sensitive taxonomy or ID column."""
    sql = f"""
        SELECT COALESCE(CAST({quote_identifier(column_name)} AS TEXT), ?) AS label,
               COUNT(*) AS count
        FROM {quote_identifier(table_name)}
        GROUP BY label
        ORDER BY count DESC, label
    """
    return tuple((f"{prefix}{row['label']}", int(row["count"])) for row in conn.execute(sql, (label_fallback,)))


def joined_name_counts(
    conn: sqlite3.Connection,
    *,
    left_table: str,
    left_fk: str,
    right_table: str,
    right_id: str,
    right_name: str | None,
) -> tuple[tuple[str, int], ...]:
    """Return grouped counts by joined taxonomy name or ID."""
    if right_name is None:
        return grouped_counts(conn, left_table, left_fk, label_fallback="(null)")
    sql = f"""
        SELECT COALESCE(CAST(r.{quote_identifier(right_name)} AS TEXT), CAST(l.{quote_identifier(left_fk)} AS TEXT)) AS label,
               COUNT(*) AS count
        FROM {quote_identifier(left_table)} AS l
        LEFT JOIN {quote_identifier(right_table)} AS r
          ON l.{quote_identifier(left_fk)} = r.{quote_identifier(right_id)}
        GROUP BY label
        ORDER BY count DESC, label
    """
    return count_rows_from_query(conn, sql)


def count_rows_from_query(conn: sqlite3.Connection, sql: str) -> tuple[tuple[str, int], ...]:
    """Return label/count rows from an aggregate query."""
    return tuple((str(row["label"]), int(row["count"])) for row in conn.execute(sql).fetchall())


def first_existing_column(table: TableInfo, candidates: Sequence[str]) -> str | None:
    """Return the first exact candidate column name present on a table."""
    column_names = table.column_names()
    lower_to_actual = {column_name.lower(): column_name for column_name in column_names}
    for candidate in candidates:
        if candidate.lower() in lower_to_actual:
            return lower_to_actual[candidate.lower()]
    return None


def render_report(context: InspectionContext, aggregates: AggregateReport) -> str:
    """Render the final deterministic Markdown inspection report."""
    lines = [
        "# LLM Categorization Database Inspection Report",
        "",
        "This report is generated from SQLite schema introspection and aggregate counts.",
        "Inferred table roles are inferred, not guaranteed.",
        "",
        "## Source",
        "",
        f"- Database: `{context.db_path}`",
        "- Connection: SQLite read-only URI with `PRAGMA query_only=ON`",
        "- Raw merchant names and transaction descriptions are not printed.",
        "",
        "## Inferred Relevant Schema",
        "",
    ]
    for concept in sorted(context.relevant_schema):
        matches = context.relevant_schema[concept]
        lines.append(f"### {section_title(concept)}")
        if not matches:
            lines.append("- Missing or not found.")
        else:
            for table_name, columns in matches:
                lines.append(f"- Inferred table `{table_name}`; relevant columns: {format_inline_code_list(columns)}")
        lines.append("")

    lines.extend(render_missing_section(context))
    lines.extend(render_aggregate_section(aggregates))
    lines.extend(render_readiness_section(context, aggregates))
    lines.extend(render_risk_section(context, aggregates))
    return "\n".join(lines).rstrip() + "\n"


def render_missing_section(context: InspectionContext) -> list[str]:
    """Render missing inferred concepts."""
    lines = ["## Missing or not found", ""]
    if not context.missing_concepts:
        lines.append("- No methodology concept was completely absent from inferred schema matches.")
    else:
        lines.extend(f"- {concept}" for concept in context.missing_concepts)
    lines.append("")
    return lines


def render_aggregate_section(aggregates: AggregateReport) -> list[str]:
    """Render safe aggregate counts."""
    lines = ["## Aggregate Counts", ""]
    lines.append(f"- Total transactions: {format_optional_count(aggregates.total_transactions)}")
    lines.append(f"- Likely UNKNOWN transactions: {format_optional_count(aggregates.unknown_count)}")
    lines.append(f"- Likely needs_review transactions: {format_optional_count(aggregates.review_count)}")
    lines.append(f"- Transactions with confidence value: {format_optional_count(aggregates.confidence_count)}")
    lines.append("")
    lines.extend(render_count_subsection("Transactions By Category", aggregates.category_counts))
    lines.extend(render_count_subsection("Transactions By Tag", aggregates.tag_counts))
    lines.extend(render_count_subsection("Transactions By Debit/Credit Sign", aggregates.direction_counts))
    lines.extend(render_count_subsection("Transactions By Statement Type", aggregates.statement_type_counts))
    lines.extend(render_count_subsection("Transactions By Account", aggregates.account_counts))
    lines.extend(render_count_subsection("Transactions By Evidence Source", aggregates.evidence_counts))
    return lines


def render_readiness_section(context: InspectionContext, aggregates: AggregateReport) -> list[str]:
    """Render benchmark coverage readiness checks."""
    concept_presence = concept_presence_counts(aggregates)
    lines = ["## Benchmark coverage readiness", ""]
    lines.append(f"- Category candidate examples: {coverage_status(aggregates.category_counts, 'category')}")
    lines.append(f"- Tag candidate examples: {coverage_status(aggregates.tag_counts, 'tag')}")
    lines.append(f"- Positive and negative amounts: {direction_readiness(aggregates.direction_counts)}")
    lines.append(f"- Likely `UNKNOWN` examples exist: {yes_no(positive_count(aggregates.unknown_count))}")
    lines.append(f"- Likely `needs_review` examples exist: {yes_no(positive_count(aggregates.review_count))}")
    for concept in BUILTIN_CONCEPTS:
        lines.append(
            f"- {concept.replace('_', '-').title()}-like cases appear to exist: {yes_no(concept_presence[concept] > 0)}"
        )
    lines.append(
        f"- Similar-history evidence can be extracted: {yes_no(similar_history_possible(context, aggregates))}"
    )
    lines.append(
        "- Manual or reviewed labels can be treated as high-trust ground truth: "
        f"{yes_no(high_trust_labels_possible(context, aggregates))}"
    )
    lines.append("")
    return lines


def render_risk_section(context: InspectionContext, aggregates: AggregateReport) -> list[str]:
    """Render potential evaluation risks."""
    risks = potential_risks(context, aggregates)
    lines = ["## Potential evaluation risks", ""]
    if not risks:
        lines.append("- No immediate coverage risks detected from inferred schema and aggregate counts.")
    else:
        lines.extend(f"- {risk}" for risk in risks)
    lines.append("")
    return lines


def potential_risks(context: InspectionContext, aggregates: AggregateReport) -> tuple[str, ...]:
    """Return deterministic evaluation risk notes."""
    risks = []
    if context.roles.categories is None:
        risks.append("Missing categories table or category assignment signal.")
    if context.roles.tags is None:
        risks.append("Missing tags table or tag taxonomy signal.")
    risks.extend(too_few_examples("category", aggregates.category_counts, MIN_EXAMPLES_PER_CATEGORY))
    risks.extend(too_few_examples("tag", aggregates.tag_counts, MIN_EXAMPLES_PER_TAG))
    if not positive_count(aggregates.review_count) and not positive_count(aggregates.unknown_count):
        risks.append("Absence of ambiguous examples inferred from review and UNKNOWN counts.")
    if not positive_count(aggregates.unknown_count):
        risks.append("Absence of expected UNKNOWN examples.")
    if not high_trust_labels_possible(context, aggregates):
        risks.append("Absence of reviewed or manual labels for high-trust ground truth.")
    if not evidence_fields_available(context):
        risks.append(
            "Missing evidence fields may make it hard to distinguish prompt failures from taxonomy, rule, or data-quality failures."
        )
    return tuple(risks)


def too_few_examples(kind: str, counts: Sequence[tuple[str, int]], minimum: int) -> list[str]:
    """Return risks for coverage buckets below the minimum example count."""
    return [f"{kind.title()} `{label}` has too few examples: {count}" for label, count in counts if count < minimum]


def concept_presence_counts(aggregates: AggregateReport) -> Counter[str]:
    """Return likely built-in concept presence from category, tag, and evidence labels."""
    counter: Counter[str] = Counter()
    labels = [label for label, _ in (*aggregates.category_counts, *aggregates.tag_counts, *aggregates.evidence_counts)]
    for label in labels:
        normalized = label.lower()
        for concept in BUILTIN_CONCEPTS:
            if concept in normalized:
                counter[concept] += 1
    return counter


def similar_history_possible(context: InspectionContext, aggregates: AggregateReport) -> bool:
    """Return whether similar-history evidence appears extractable."""
    transactions = context.roles.transactions
    if transactions is None:
        return False
    columns = lower_columns(transactions)
    has_reusable_identity = bool(columns & {"merchant_id", "merchant_key", "description"})
    has_assignment = bool(columns & {"category", "category_id"})
    has_history_source = any(
        label.lower() in {"history", "historical", "similar_transactions"} for label, _ in aggregates.evidence_counts
    )
    return (has_reusable_identity and has_assignment) or has_history_source


def high_trust_labels_possible(context: InspectionContext, aggregates: AggregateReport) -> bool:
    """Return whether manual or reviewed labels appear available."""
    transactions = context.roles.transactions
    if transactions is None:
        return False
    columns = lower_columns(transactions)
    has_review_column = bool(columns & {"reviewed_at", "reviewed_by", "needs_review"})
    has_manual_source = any(
        label.lower() in {"manual", "reviewed", "manual_edit"} for label, _ in aggregates.evidence_counts
    )
    return has_review_column or has_manual_source


def evidence_fields_available(context: InspectionContext) -> bool:
    """Return whether fields exist for separating prompt, taxonomy, rule, and data failures."""
    transactions = context.roles.transactions
    if transactions is None:
        return False
    columns = lower_columns(transactions)
    evidence_columns = {
        "category_source",
        "category_confidence",
        "category_metadata",
        "category_rule_id",
        "needs_review",
    }
    return bool(columns & evidence_columns) or context.roles.category_rules is not None


def coverage_status(counts: Sequence[tuple[str, int]], label: str) -> str:
    """Return a compact coverage readiness status for category or tag counts."""
    if not counts:
        return f"no {label} counts found"
    too_few = sum(
        1 for _, count in counts if count < (MIN_EXAMPLES_PER_CATEGORY if label == "category" else MIN_EXAMPLES_PER_TAG)
    )
    if too_few:
        return f"{len(counts)} {label}(s) found; {too_few} below recommended minimum"
    return f"{len(counts)} {label}(s) found; all meet recommended minimum"


def direction_readiness(direction_counts: Sequence[tuple[str, int]]) -> str:
    """Return readiness for positive and negative amount coverage."""
    counts = dict(direction_counts)
    has_debit = counts.get("debit_positive", 0) > 0
    has_credit = counts.get("credit_negative", 0) > 0
    if has_debit and has_credit:
        return "yes"
    missing = []
    if not has_debit:
        missing.append("positive/debit")
    if not has_credit:
        missing.append("negative/credit")
    return f"no; missing {', '.join(missing)}"


def render_count_subsection(title: str, counts: Sequence[tuple[str, int]]) -> list[str]:
    """Render a bounded count subsection."""
    lines = [f"### {title}", ""]
    if not counts:
        lines.append("- Missing or not found.")
    else:
        for label, count in counts[:MAX_REPORT_ROWS]:
            lines.append(f"- `{label}`: {count}")
        if len(counts) > MAX_REPORT_ROWS:
            lines.append(f"- ... {len(counts) - MAX_REPORT_ROWS} more")
    lines.append("")
    return lines


def format_inline_code_list(values: Sequence[str]) -> str:
    """Return a comma-separated inline-code list."""
    return ", ".join(f"`{value}`" for value in values)


def section_title(value: str) -> str:
    """Return a readable section title while preserving AI uppercase."""
    return (value[:1].upper() + value[1:]).replace("Ai ", "AI ")


def format_optional_count(value: int | None) -> str:
    """Return a printable count or missing marker."""
    return "Missing or not found" if value is None else str(value)


def yes_no(value: bool) -> str:
    """Return a stable yes/no label."""
    return "yes" if value else "no"


def positive_count(value: int | None) -> bool:
    """Return whether a nullable count is positive."""
    return value is not None and value > 0


def quote_identifier(identifier: str) -> str:
    """Return a safely quoted SQLite identifier."""
    return '"' + identifier.replace('"', '""') + '"'


def write_report(report: str, out_path: Path) -> None:
    """Write a Markdown report to disk."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report, encoding="utf-8", newline="\n")


def build_parser() -> argparse.ArgumentParser:
    """Create the command-line parser."""
    parser = argparse.ArgumentParser(
        description="Inspect a FinScope SQLite database for LLM categorization eval readiness."
    )
    parser.add_argument("--db", required=True, type=Path, help="Path to the SQLite database to inspect.")
    parser.add_argument("--out", required=True, type=Path, help="Path to write the Markdown inspection report.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the database inspection CLI."""
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        report = inspect_database(args.db)
        write_report(report, args.out)
    except (OSError, sqlite3.Error, InspectionError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"Wrote database inspection report: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
