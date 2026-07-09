"""Build a conservative draft LLM categorization dataset from SQLite.

This offline extractor reads a FinScope-like SQLite database in read-only mode,
builds redacted candidate examples for manual curation, and writes coverage
reports. It does not mutate the database, call LLM providers, or score prompts.
"""

import argparse
import sqlite3
import sys
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from evals.llm_categorization.tools.inspect_db import (
    InspectionError,
    infer_roles,
    introspect_schema,
    open_readonly_sqlite,
)
from evals.llm_categorization.tools.io_utils import write_jsonl

HIGH_CONFIDENCE_THRESHOLD = 0.95
MEDIUM_CONFIDENCE_THRESHOLD = 0.85
MIN_STABLE_HISTORY_COUNT = 3
MAX_SIMILAR_EVIDENCE = 3
TARGET_CATEGORY_EXAMPLES = 5
TARGET_TAG_EXAMPLES = 3
ADJUDICATION_LIMIT = 100

BENCHMARK_STRATA = (
    "straightforward",
    "tag_required",
    "no_tag",
    "ambiguous_merchant",
    "noisy_description",
    "debit",
    "credit",
    "transfer_like",
    "income_like",
    "rental_like",
    "reimbursement_like",
    "reimbursable_like",
    "tax_like",
    "unknown_correct",
    "similar_history",
    "weak_history",
    "misleading_history",
)
HIGH_TRUST_SOURCE_PRIORITY = {
    "manual_edit": 5,
    "reviewed": 4,
    "high_confidence_rule": 3,
    "stable_history": 2,
    "unknown": 1,
}

CUE_PATTERNS = (
    (("grocery", "grocer", "supermarket", "metro", "iga", "maxi", "costco", "walmart"), "grocery"),
    (("pharmacy", "pharmacie", "drug", "jean coutu", "uniprix"), "pharmacy"),
    (("salary", "payroll", "paie", "pay", "deposit"), "salary"),
    (("transfer", "tfr", "interac", "etransfer", "e-transfer", "payment"), "transfer"),
    (("tax", "cra", "revenu", "impot", "government"), "tax"),
    (("rent", "rental", "lease"), "rental"),
    (("reimbursement", "refund", "rebate", "remboursement"), "reimbursement"),
    (("restaurant", "cafe", "coffee", "pizza", "sushi", "dining", "uber eats", "doordash"), "restaurant"),
    (("transport", "transit", "opus", "stm", "exo", "parking", "fuel", "gas"), "transportation"),
    (("subscription", "netflix", "spotify", "apple", "google", "microsoft"), "subscription"),
    (("hydro", "utility", "utilities", "electric", "internet", "mobile", "phone"), "utilities"),
    (("insurance", "assurance"), "insurance"),
    (("fee", "fees", "interest", "charge"), "fees"),
    (("travel", "hotel", "airline", "flight", "airbnb"), "travel"),
    (("medical", "health", "clinic", "dentist", "doctor"), "health"),
    (("school", "tuition", "university", "udem", "education"), "education"),
    (("child", "daycare", "camp"), "children"),
    (("investment", "dividend", "broker", "wealth"), "investment"),
)
NOISY_PATTERNS = (
    "pos",
    "pymt",
    "preauth",
    "purchase",
    "debit",
    "credit",
    "withdrawal",
    "online",
    "misc",
    "auth",
)


@dataclass(frozen=True)
class TaxonomyItem:
    """Represent one category or tag row for prompt candidates."""

    id: str
    name: str
    description: str
    instruction: str | None

    def as_json(self) -> dict[str, Any]:
        """Return the dataset JSON shape for this taxonomy row."""
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "instruction": self.instruction,
        }


@dataclass(frozen=True)
class TransactionRecord:
    """Represent one transaction and its joined categorization context."""

    id: int
    description: str
    amount: float
    category_id: str | None
    category_name: str | None
    tag_ids: tuple[str, ...]
    tag_names: tuple[str, ...]
    category_source: str | None
    category_confidence: float | None
    needs_review: bool
    reviewed_at: str | None
    category_rule_id: int | None
    merchant_id: int | None
    account: str | None
    statement_type: str | None


@dataclass(frozen=True)
class CandidateExample:
    """Represent a draft or adjudication candidate before JSON serialization."""

    transaction: TransactionRecord
    label_source: str
    ambiguity_type: str
    confidence_band: str
    needs_review: bool
    notes: str
    score: tuple[int, float, int]
    adjudication_reason: str | None = None


@dataclass(frozen=True)
class ExtractionData:
    """Represent database state needed for draft extraction."""

    categories: tuple[TaxonomyItem, ...]
    tags: tuple[TaxonomyItem, ...]
    transactions: tuple[TransactionRecord, ...]
    stable_history_counts: Mapping[tuple[int, str], int]

    def category_ids(self) -> set[str]:
        """Return current category IDs."""
        return {item.id for item in self.categories}

    def category_name(self, category_id: str | None) -> str | None:
        """Return category name for an ID."""
        if category_id is None:
            return None
        for item in self.categories:
            if item.id == category_id:
                return item.name
        return None

    def unknown_category_id(self) -> str | None:
        """Return the UNKNOWN category ID when present."""
        for item in self.categories:
            if item.name == "UNKNOWN":
                return item.id
        return None


@dataclass(frozen=True)
class ExtractionResult:
    """Represent selected draft and adjudication records."""

    selected: tuple[dict[str, Any], ...]
    adjudication: tuple[dict[str, Any], ...]
    selected_candidates: tuple[CandidateExample, ...]
    missing_strata: tuple[str, ...]
    missing_categories: tuple[str, ...]
    missing_tags: tuple[str, ...]


class ExtractionError(RuntimeError):
    """Represent a draft extraction failure."""


def build_dataset(db_path: Path, max_examples: int) -> ExtractionResult:
    """Build draft and adjudication examples from a read-only database."""
    conn = open_readonly_sqlite(db_path)
    try:
        data = load_extraction_data(conn)
    finally:
        conn.close()

    draft_candidates, adjudication_candidates = classify_candidates(data)
    selected_candidates = select_stratified_candidates(draft_candidates, max_examples)
    selected = tuple(example_json(data, candidate) for candidate in selected_candidates)
    adjudication = tuple(example_json(data, candidate) for candidate in adjudication_candidates[:ADJUDICATION_LIMIT])
    selected_categories = {candidate.transaction.category_name for candidate in selected_candidates}
    selected_tags = {tag for candidate in selected_candidates for tag in candidate.transaction.tag_names}
    available_strata = {
        stratum
        for candidate in (*draft_candidates, *adjudication_candidates)
        for stratum in candidate_strata(candidate)
    }
    return ExtractionResult(
        selected=selected,
        adjudication=adjudication,
        selected_candidates=tuple(selected_candidates),
        missing_strata=tuple(stratum for stratum in BENCHMARK_STRATA if stratum not in available_strata),
        missing_categories=tuple(item.name for item in data.categories if item.name not in selected_categories),
        missing_tags=tuple(item.name for item in data.tags if item.name not in selected_tags),
    )


def load_extraction_data(conn: sqlite3.Connection) -> ExtractionData:
    """Load taxonomy and transaction rows through inferred schema roles."""
    roles = infer_roles(introspect_schema(conn))
    if roles.transactions is None:
        raise ExtractionError("could not infer transactions table")
    if roles.categories is None:
        raise ExtractionError("could not infer categories table")
    if roles.tags is None or roles.transaction_tags is None:
        raise ExtractionError("could not infer tags and transaction-tag assignment tables")

    categories = load_taxonomy_items(conn, roles.categories.name)
    tags = load_taxonomy_items(conn, roles.tags.name)
    rows = load_transaction_rows(conn)
    transaction_tags = load_transaction_tags(conn)
    transactions = tuple(record_from_row(row, transaction_tags) for row in rows)
    stable_counts = stable_history_counts(transactions)
    return ExtractionData(
        categories=categories,
        tags=tags,
        transactions=transactions,
        stable_history_counts=stable_counts,
    )


def load_taxonomy_items(conn: sqlite3.Connection, table_name: str) -> tuple[TaxonomyItem, ...]:
    """Load categories or tags as full candidate taxonomy rows."""
    rows = conn.execute(f"""
        SELECT id, name, description, instruction
        FROM {quote_identifier(table_name)}
        ORDER BY name, id
        """).fetchall()
    return tuple(
        TaxonomyItem(
            id=str(row["id"]),
            name=str(row["name"]),
            description=str(row["description"] or ""),
            instruction=str(row["instruction"]) if row["instruction"] is not None else None,
        )
        for row in rows
    )


def load_transaction_rows(conn: sqlite3.Connection) -> Sequence[sqlite3.Row]:
    """Load transaction rows with safe joined account and statement context."""
    return conn.execute("""
        SELECT
            tx.id,
            tx.description,
            tx.amount,
            COALESCE(CAST(tx.category_id AS TEXT), CAST(c.id AS TEXT)) AS category_id,
            COALESCE(c.name, tx.category) AS category_name,
            tx.category_source,
            tx.category_confidence,
            tx.needs_review,
            tx.reviewed_at,
            tx.category_rule_id,
            tx.merchant_id,
            a.account_type AS account_type,
            st.name AS statement_type_name,
            st.parser_type AS statement_parser_type
        FROM transactions AS tx
        LEFT JOIN categories AS c
          ON tx.category_id = c.id OR (tx.category_id IS NULL AND tx.category = c.name)
        LEFT JOIN accounts AS a
          ON tx.account_id = a.id
        LEFT JOIN statements AS s
          ON tx.statement_id = s.id
        LEFT JOIN statement_types AS st
          ON s.statement_type_id = st.id
        WHERE COALESCE(tx.ignored, 0) = 0
        ORDER BY tx.id
        """).fetchall()


def load_transaction_tags(conn: sqlite3.Connection) -> dict[int, tuple[tuple[str, str], ...]]:
    """Return tag IDs and names by transaction ID."""
    rows = conn.execute("""
        SELECT tt.transaction_id, CAST(t.id AS TEXT) AS tag_id, t.name AS tag_name
        FROM transaction_tags AS tt
        JOIN tags AS t
          ON tt.tag_id = t.id
        ORDER BY tt.transaction_id, t.name, t.id
        """).fetchall()
    tags_by_transaction: dict[int, list[tuple[str, str]]] = defaultdict(list)
    for row in rows:
        tags_by_transaction[int(row["transaction_id"])].append((str(row["tag_id"]), str(row["tag_name"])))
    return {transaction_id: tuple(tags) for transaction_id, tags in tags_by_transaction.items()}


def record_from_row(row: sqlite3.Row, transaction_tags: Mapping[int, tuple[tuple[str, str], ...]]) -> TransactionRecord:
    """Build a transaction record from one query row."""
    tags = transaction_tags.get(int(row["id"]), ())
    return TransactionRecord(
        id=int(row["id"]),
        description=str(row["description"] or ""),
        amount=float(row["amount"]),
        category_id=str(row["category_id"]) if row["category_id"] is not None else None,
        category_name=str(row["category_name"]) if row["category_name"] is not None else None,
        tag_ids=tuple(tag_id for tag_id, _ in tags),
        tag_names=tuple(tag_name for _, tag_name in tags),
        category_source=lower_or_none(row["category_source"]),
        category_confidence=float(row["category_confidence"]) if row["category_confidence"] is not None else None,
        needs_review=bool(row["needs_review"]),
        reviewed_at=str(row["reviewed_at"]) if row["reviewed_at"] is not None else None,
        category_rule_id=int(row["category_rule_id"]) if row["category_rule_id"] is not None else None,
        merchant_id=int(row["merchant_id"]) if row["merchant_id"] is not None else None,
        account=safe_account_label(row["account_type"]),
        statement_type=safe_statement_type(row["statement_type_name"], row["statement_parser_type"]),
    )


def stable_history_counts(transactions: Sequence[TransactionRecord]) -> dict[tuple[int, str], int]:
    """Return counts for repeated merchant/category assignments."""
    counts: Counter[tuple[int, str]] = Counter()
    for transaction in transactions:
        if transaction.merchant_id is None or transaction.category_id is None or transaction.category_name == "UNKNOWN":
            continue
        counts[(transaction.merchant_id, transaction.category_id)] += 1
    return dict(counts)


def classify_candidates(data: ExtractionData) -> tuple[tuple[CandidateExample, ...], tuple[CandidateExample, ...]]:
    """Split transaction records into draft and adjudication candidates."""
    draft = []
    adjudication = []
    for transaction in data.transactions:
        candidate = classify_candidate(data, transaction)
        if candidate is None:
            continue
        if candidate.adjudication_reason is None:
            draft.append(candidate)
        else:
            adjudication.append(candidate)
    return tuple(sorted(draft, key=candidate_sort_key)), tuple(sorted(adjudication, key=candidate_sort_key))


def classify_candidate(data: ExtractionData, transaction: TransactionRecord) -> CandidateExample | None:
    """Return a draft or adjudication candidate for one transaction."""
    unknown_category_id = data.unknown_category_id()
    if transaction.category_id not in data.category_ids():
        if unknown_category_id is None:
            return None
        return adjudication_candidate(
            transaction,
            unknown_category_id,
            "current category is missing from candidate taxonomy",
        )

    source = transaction.category_source or "unknown"
    stable_count = (
        data.stable_history_counts.get((transaction.merchant_id, transaction.category_id), 0)
        if transaction.merchant_id is not None and transaction.category_id is not None
        else 0
    )
    label_source = label_source_for_transaction(transaction, stable_count)
    ambiguity_type = ambiguity_type_for_transaction(transaction, stable_count)
    confidence_band = confidence_band_for_transaction(transaction.category_confidence)
    notes = notes_for_transaction(transaction, label_source, ambiguity_type, stable_count)
    score = candidate_score(transaction, label_source)

    adjudication_reason = adjudication_reason_for_transaction(transaction, source, stable_count)
    if adjudication_reason is not None:
        return CandidateExample(
            transaction=transaction,
            label_source=label_source,
            ambiguity_type=ambiguity_type,
            confidence_band=confidence_band,
            needs_review=True,
            notes=f"Adjudication needed: {adjudication_reason}",
            score=score,
            adjudication_reason=adjudication_reason,
        )

    needs_review = expected_needs_review(transaction, label_source, ambiguity_type)
    return CandidateExample(
        transaction=transaction,
        label_source=label_source,
        ambiguity_type=ambiguity_type,
        confidence_band=confidence_band,
        needs_review=needs_review,
        notes=notes,
        score=score,
    )


def label_source_for_transaction(transaction: TransactionRecord, stable_count: int) -> str:
    """Return the benchmark label source for a transaction."""
    source = transaction.category_source or "unknown"
    if source == "manual":
        return "manual_edit"
    if transaction.reviewed_at:
        return "reviewed"
    if (
        source == "rule"
        and (transaction.category_confidence or 0.0) >= HIGH_CONFIDENCE_THRESHOLD
        and not transaction.needs_review
    ):
        return "high_confidence_rule"
    if stable_count >= MIN_STABLE_HISTORY_COUNT and source in {"history", "rule"}:
        return "stable_history"
    return "unknown"


def adjudication_reason_for_transaction(transaction: TransactionRecord, source: str, stable_count: int) -> str | None:
    """Return why a transaction needs adjudication, or None for draft use."""
    if source == "ai" and not transaction.reviewed_at:
        return "AI-only category assignment without explicit review"
    if transaction.category_name == "UNKNOWN":
        return None
    if transaction.needs_review and not transaction.reviewed_at and source != "manual":
        return "transaction is marked as needing review"
    if source == "history" and stable_count < MIN_STABLE_HISTORY_COUNT:
        return "historical label is not stable enough"
    if source not in {"manual", "rule", "history", "unknown"} and not transaction.reviewed_at:
        return f"low-trust category source: {source}"
    if (
        transaction.category_confidence is not None
        and transaction.category_confidence < MEDIUM_CONFIDENCE_THRESHOLD
        and not transaction.reviewed_at
        and source != "manual"
    ):
        return "low category confidence"
    return None


def expected_needs_review(transaction: TransactionRecord, label_source: str, ambiguity_type: str) -> bool:
    """Infer expected review status conservatively."""
    if transaction.category_name == "UNKNOWN":
        return True
    if label_source in {"manual_edit", "reviewed", "high_confidence_rule", "stable_history"}:
        return False
    return ambiguity_type not in {"straightforward"}


def ambiguity_type_for_transaction(transaction: TransactionRecord, stable_count: int) -> str:
    """Infer benchmark ambiguity type from labels and redacted text cues."""
    text = combined_text(transaction)
    category = (transaction.category_name or "").lower()
    tags = {tag.lower() for tag in transaction.tag_names}
    cues = classification_cues(text)
    if transaction.category_name == "UNKNOWN":
        return "unknown_correct"
    if "reimbursable" in tags:
        return "reimbursable_like"
    if "tax" in tags or "tax" in cues:
        return "tax_like"
    if "reimbursement" in category or "reimbursement" in cues:
        return "reimbursement_like"
    if "rental" in category or "rental" in cues:
        return "rental_like"
    if "transfers" in category or "transfer" in cues:
        return "transfer_like"
    if "income" in category or "salary" in cues:
        return "income_like"
    if has_noisy_text(text):
        return "noisy_description"
    if not cues:
        return "ambiguous_merchant"
    if (transaction.category_source or "") == "history" and stable_count < MIN_STABLE_HISTORY_COUNT:
        return "weak_history"
    return "straightforward"


def confidence_band_for_transaction(confidence: float | None) -> str:
    """Return the dataset confidence band label."""
    if confidence is None:
        return "unknown"
    if confidence >= HIGH_CONFIDENCE_THRESHOLD:
        return "high"
    if confidence >= MEDIUM_CONFIDENCE_THRESHOLD:
        return "medium"
    return "low"


def notes_for_transaction(
    transaction: TransactionRecord, label_source: str, ambiguity_type: str, stable_count: int
) -> str:
    """Return concise manual-curation notes without sensitive free text."""
    parts = [
        f"Draft from {label_source} label.",
        f"Ambiguity type: {ambiguity_type}.",
    ]
    if stable_count >= MIN_STABLE_HISTORY_COUNT:
        parts.append(f"Similar merchant/category history count: {stable_count}.")
    if transaction.category_confidence is not None:
        parts.append(f"Stored confidence band: {confidence_band_for_transaction(transaction.category_confidence)}.")
    return " ".join(parts)


def candidate_score(transaction: TransactionRecord, label_source: str) -> tuple[int, float, int]:
    """Return deterministic candidate quality score."""
    confidence = transaction.category_confidence if transaction.category_confidence is not None else 0.0
    return (HIGH_TRUST_SOURCE_PRIORITY.get(label_source, 0), confidence, transaction.id)


def candidate_sort_key(candidate: CandidateExample) -> tuple[int, float, int]:
    """Return descending deterministic sort key."""
    priority, confidence, transaction_id = candidate.score
    return (-priority, -confidence, transaction_id)


def select_stratified_candidates(candidates: Sequence[CandidateExample], max_examples: int) -> list[CandidateExample]:
    """Select a deterministic stratified draft sample."""
    selected: list[CandidateExample] = []
    selected_ids: set[int] = set()
    sorted_candidates = sorted(candidates, key=candidate_sort_key)

    for stratum in BENCHMARK_STRATA:
        add_best_matching(
            selected,
            selected_ids,
            sorted_candidates,
            lambda candidate, stratum=stratum: stratum in candidate_strata(candidate),
        )
        if len(selected) >= max_examples:
            return selected

    categories = sorted({candidate.transaction.category_name or "" for candidate in sorted_candidates})
    tags = sorted({tag for candidate in sorted_candidates for tag in candidate.transaction.tag_names})
    for category in categories:
        while len(selected) < max_examples and count_category(selected, category) < TARGET_CATEGORY_EXAMPLES:
            if not add_best_matching(
                selected,
                selected_ids,
                sorted_candidates,
                lambda candidate, category=category: candidate.transaction.category_name == category,
            ):
                break
    for tag in tags:
        while len(selected) < max_examples and count_tag(selected, tag) < TARGET_TAG_EXAMPLES:
            if not add_best_matching(
                selected,
                selected_ids,
                sorted_candidates,
                lambda candidate, tag=tag: tag in candidate.transaction.tag_names,
            ):
                break
    for candidate in sorted_candidates:
        if len(selected) >= max_examples:
            break
        if candidate.transaction.id not in selected_ids:
            selected.append(candidate)
            selected_ids.add(candidate.transaction.id)
    return selected


def add_best_matching(
    selected: list[CandidateExample],
    selected_ids: set[int],
    candidates: Sequence[CandidateExample],
    predicate: Any,
) -> bool:
    """Add the best unselected candidate matching a predicate."""
    for candidate in candidates:
        if candidate.transaction.id in selected_ids:
            continue
        if predicate(candidate):
            selected.append(candidate)
            selected_ids.add(candidate.transaction.id)
            return True
    return False


def count_category(candidates: Sequence[CandidateExample], category: str) -> int:
    """Return selected count for a category."""
    return sum(1 for candidate in candidates if candidate.transaction.category_name == category)


def count_tag(candidates: Sequence[CandidateExample], tag: str) -> int:
    """Return selected count for a tag."""
    return sum(1 for candidate in candidates if tag in candidate.transaction.tag_names)


def candidate_strata(candidate: CandidateExample) -> set[str]:
    """Return benchmark strata covered by a candidate."""
    strata = {candidate.ambiguity_type, direction_from_amount(candidate.transaction.amount)}
    strata.add("tag_required" if candidate.transaction.tag_ids else "no_tag")
    if candidate.transaction.merchant_id is not None:
        strata.add("similar_history")
    if candidate.ambiguity_type in {"weak_history", "misleading_history"}:
        strata.add(candidate.ambiguity_type)
    return strata


def example_json(data: ExtractionData, candidate: CandidateExample) -> dict[str, Any]:
    """Return one strict dataset JSON object."""
    transaction = candidate.transaction
    return {
        "request_id": f"db-tx-{transaction.id}",
        "transaction": {
            "description": redacted_description(transaction),
            "merchant": redacted_merchant(transaction),
            "amount": round(transaction.amount, 2),
            "date": None,
            "account": transaction.account,
            "statement_type": transaction.statement_type,
        },
        "candidate_taxonomy": {
            "categories": [category.as_json() for category in data.categories],
            "tags": [tag.as_json() for tag in data.tags],
        },
        "similar_transactions": similar_transaction_evidence(data, transaction),
        "expected": {
            "category_id": transaction.category_id,
            "tag_ids": list(transaction.tag_ids),
            "needs_review": candidate.needs_review,
        },
        "label_source": candidate.label_source,
        "privacy_level": "redacted_real",
        "coverage": {
            "category": transaction.category_name,
            "tags": list(transaction.tag_names),
            "direction": direction_from_amount(transaction.amount),
            "statement_type": transaction.statement_type,
            "confidence_band": candidate.confidence_band,
            "ambiguity_type": candidate.ambiguity_type,
        },
        "notes": candidate.notes,
    }


def similar_transaction_evidence(data: ExtractionData, transaction: TransactionRecord) -> list[dict[str, Any]]:
    """Return redacted similar-history evidence for one transaction."""
    if transaction.merchant_id is None:
        return []
    similar = [
        row
        for row in data.transactions
        if row.id != transaction.id
        and row.merchant_id == transaction.merchant_id
        and row.category_id in data.category_ids()
        and row.category_name != "UNKNOWN"
        and row.category_source in {"manual", "rule", "history"}
    ]
    similar.sort(
        key=lambda row: (row.category_id != transaction.category_id, -(row.category_confidence or 0.0), row.id)
    )
    evidence = []
    seen = set()
    for row in similar:
        if row.category_id is None:
            continue
        item = {
            "description": redacted_description(row),
            "amount": round(row.amount, 2),
            "category_id": row.category_id,
            "tag_ids": list(row.tag_ids),
            "evidence_type": similar_evidence_type(row),
            "confidence": row.category_confidence if row.category_confidence is not None else 0.0,
        }
        dedupe_key = (
            item["description"],
            item["amount"],
            item["category_id"],
            tuple(item["tag_ids"]),
            item["evidence_type"],
        )
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        evidence.append(item)
        if len(evidence) >= MAX_SIMILAR_EVIDENCE:
            break
    return evidence


def similar_evidence_type(transaction: TransactionRecord) -> str:
    """Return valid evidence type for similar transaction evidence."""
    source = transaction.category_source or "unknown"
    if source == "manual":
        return "manual"
    if source == "rule":
        return "rule"
    if source == "history":
        return "history"
    if source == "ai":
        return "ai"
    return "unknown"


def render_coverage_report(result: ExtractionResult) -> str:
    """Render deterministic Markdown coverage report for selected examples."""
    examples = result.selected
    lines = [
        "# Draft Dataset Coverage Report",
        "",
        f"- Selected examples: {len(examples)}",
        f"- Adjudication examples: {len(result.adjudication)}",
        "- Source: read-only SQLite extraction with redacted free text.",
        "",
    ]
    lines.extend(render_counter_section("Selected examples by category", count_expected_categories(examples)))
    lines.extend(render_counter_section("Selected examples by tag", count_expected_tags(examples)))
    lines.extend(
        render_counter_section("Debit versus credit counts", count_nested(examples, ("coverage", "direction")))
    )
    lines.extend(
        render_counter_section("Statement type counts", count_nested(examples, ("coverage", "statement_type")))
    )
    lines.extend(render_counter_section("Account counts", count_nested(examples, ("transaction", "account"))))
    lines.extend(render_counter_section("needs_review counts", count_nested(examples, ("expected", "needs_review"))))
    lines.append(f"## UNKNOWN expected counts\n\n- UNKNOWN: {count_unknown_examples(examples)}\n")
    lines.extend(render_counter_section("Label source counts", count_nested(examples, ("label_source",))))
    lines.extend(
        render_counter_section("Ambiguity type counts", count_nested(examples, ("coverage", "ambiguity_type")))
    )
    lines.append("## Missing categories and tags")
    lines.append("")
    lines.append(f"- Categories not covered: {format_missing(result.missing_categories)}")
    lines.append(f"- Tags not covered: {format_missing(result.missing_tags)}")
    lines.append("")
    lines.append("## Missing benchmark strata")
    lines.append("")
    if result.missing_strata:
        lines.extend(f"- {stratum}" for stratum in result.missing_strata)
    else:
        lines.append("- None detected from available database evidence.")
    lines.append("")
    return "\n".join(lines)


def render_counter_section(title: str, counter: Counter[Any]) -> list[str]:
    """Render a count section."""
    lines = [f"## {title}", ""]
    if not counter:
        lines.append("- Missing or not found.")
    else:
        for label, count in sorted(counter.items(), key=lambda item: (str(item[0]), item[1])):
            lines.append(f"- `{label}`: {count}")
    lines.append("")
    return lines


def count_expected_categories(examples: Sequence[Mapping[str, Any]]) -> Counter[str]:
    """Return selected examples by coverage category."""
    return Counter(str(example["coverage"]["category"]) for example in examples)


def count_expected_tags(examples: Sequence[Mapping[str, Any]]) -> Counter[str]:
    """Return selected examples by coverage tag."""
    counter: Counter[str] = Counter()
    for example in examples:
        for tag in example["coverage"]["tags"]:
            counter[str(tag)] += 1
    return counter


def count_nested(examples: Sequence[Mapping[str, Any]], path: Sequence[str]) -> Counter[str]:
    """Return counts for a nested JSON value path."""
    counter: Counter[str] = Counter()
    for example in examples:
        value: Any = example
        for key in path:
            value = value[key]
        counter[str(value)] += 1
    return counter


def count_unknown_examples(examples: Sequence[Mapping[str, Any]]) -> int:
    """Return selected examples whose expected category is UNKNOWN."""
    return sum(1 for example in examples if example["coverage"]["category"] == "UNKNOWN")


def format_missing(values: Sequence[str]) -> str:
    """Return a compact missing coverage list."""
    return "None" if not values else ", ".join(values)


def redacted_description(transaction: TransactionRecord) -> str:
    """Return a redacted transaction description preserving classification cues."""
    cues = classification_cues(combined_text(transaction))
    if cues:
        return "redacted transaction: " + ", ".join(cues)
    return "redacted transaction"


def redacted_merchant(transaction: TransactionRecord) -> str | None:
    """Return a redacted merchant hint, or null when no merchant identity exists."""
    if transaction.merchant_id is None:
        return None
    cues = classification_cues(combined_text(transaction))
    return "redacted merchant: " + ", ".join(cues) if cues else "redacted merchant"


def classification_cues(text: str) -> tuple[str, ...]:
    """Return generic classification cues from sensitive free text."""
    normalized = text.lower()
    cues = []
    for patterns, cue in CUE_PATTERNS:
        if any(pattern in normalized for pattern in patterns) and cue not in cues:
            cues.append(cue)
    return tuple(cues)


def has_noisy_text(text: str) -> bool:
    """Return whether a transaction description looks like noisy bank text."""
    normalized = text.lower()
    return any(pattern in normalized for pattern in NOISY_PATTERNS)


def combined_text(transaction: TransactionRecord) -> str:
    """Return text used only for local redaction and cue inference."""
    return " ".join(
        value
        for value in (
            transaction.description,
            transaction.category_name or "",
            " ".join(transaction.tag_names),
            transaction.statement_type or "",
        )
        if value
    )


def direction_from_amount(amount: float) -> str:
    """Return dataset direction from signed amount."""
    if amount > 0:
        return "debit"
    if amount < 0:
        return "credit"
    return "zero"


def safe_account_label(account_type: object) -> str | None:
    """Return non-sensitive account type context."""
    if account_type is None:
        return None
    text = str(account_type).strip()
    return text or None


def safe_statement_type(statement_type_name: object, parser_type: object) -> str | None:
    """Return statement type context without file or account identifiers."""
    if statement_type_name is not None and str(statement_type_name).strip():
        return str(statement_type_name).strip()
    if parser_type is not None and str(parser_type).strip():
        return str(parser_type).strip()
    return None


def lower_or_none(value: object) -> str | None:
    """Return lower-case text or None."""
    if value is None:
        return None
    text = str(value).strip().lower()
    return text or None


def adjudication_candidate(transaction: TransactionRecord, unknown_category_id: str, reason: str) -> CandidateExample:
    """Build an adjudication candidate for unresolved category references."""
    replacement = TransactionRecord(
        id=transaction.id,
        description=transaction.description,
        amount=transaction.amount,
        category_id=unknown_category_id,
        category_name="UNKNOWN",
        tag_ids=(),
        tag_names=(),
        category_source=transaction.category_source,
        category_confidence=transaction.category_confidence,
        needs_review=True,
        reviewed_at=transaction.reviewed_at,
        category_rule_id=transaction.category_rule_id,
        merchant_id=transaction.merchant_id,
        account=transaction.account,
        statement_type=transaction.statement_type,
    )
    return CandidateExample(
        transaction=replacement,
        label_source="unknown",
        ambiguity_type="unknown_correct",
        confidence_band=confidence_band_for_transaction(transaction.category_confidence),
        needs_review=True,
        notes=f"Adjudication needed: {reason}",
        score=(0, 0.0, transaction.id),
        adjudication_reason=reason,
    )


def write_outputs(result: ExtractionResult, out: Path, coverage_report: Path, adjudication_out: Path) -> None:
    """Write draft dataset, coverage report, and adjudication queue."""
    write_jsonl(out, result.selected)
    write_jsonl(adjudication_out, result.adjudication)
    coverage_report.parent.mkdir(parents=True, exist_ok=True)
    coverage_report.write_text(render_coverage_report(result), encoding="utf-8", newline="\n")


def quote_identifier(identifier: str) -> str:
    """Return a safely quoted SQLite identifier."""
    return '"' + identifier.replace('"', '""') + '"'


def build_parser() -> argparse.ArgumentParser:
    """Create the command-line parser."""
    parser = argparse.ArgumentParser(
        description="Build a draft LLM categorization eval dataset from a FinScope SQLite database."
    )
    parser.add_argument("--db", required=True, type=Path, help="Path to the SQLite database to inspect.")
    parser.add_argument("--out", required=True, type=Path, help="Path for the draft JSONL dataset.")
    parser.add_argument("--coverage-report", required=True, type=Path, help="Path for the Markdown coverage report.")
    parser.add_argument("--adjudication-out", required=True, type=Path, help="Path for uncertain examples JSONL.")
    parser.add_argument("--max-examples", type=int, default=150, help="Maximum draft examples to select.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the draft dataset extractor CLI."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.max_examples <= 0:
        print("error: --max-examples must be positive", file=sys.stderr)
        return 1
    try:
        result = build_dataset(args.db, args.max_examples)
        write_outputs(result, args.out, args.coverage_report, args.adjudication_out)
    except (OSError, sqlite3.Error, InspectionError, ExtractionError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"Wrote {len(result.selected)} draft example(s): {args.out}")
    print(f"Wrote {len(result.adjudication)} adjudication example(s): {args.adjudication_out}")
    print(f"Wrote coverage report: {args.coverage_report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
