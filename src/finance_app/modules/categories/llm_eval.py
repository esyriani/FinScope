"""Offline evaluation helpers for LLM transaction categorization quality.

This module turns a locally labeled CSV of privacy-minimized transaction
candidates into prompt payloads, validates the labels against the active
taxonomy, runs an injectable LLM requester, and computes deterministic quality
metrics. It does not write to the database or read uploaded statement text.
"""

from __future__ import annotations

import csv
import re
import zipfile
from collections import Counter
from collections.abc import Callable, Mapping, MutableMapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from sqlalchemy import select

from finance_app.core.builtin_taxonomy import BUILTIN_TAG_REIMBURSABLE, builtin_tag_name_for_key
from finance_app.core.config import settings
from finance_app.core.constants import CATEGORY_SOURCE_UNKNOWN, TRANSACTION_KINDS, UNKNOWN_CATEGORY
from finance_app.core.money import rounded_money_decimal
from finance_app.database.tables import (
    merchants as merchants_table,
)
from finance_app.database.tables import (
    statement_types as statement_types_table,
)
from finance_app.database.tables import (
    statements as statements_table,
)
from finance_app.database.tables import (
    transactions as transactions_table,
)
from finance_app.modules.categories.categorization import (
    historical_evidence_payload,
    rule_evidence_payload,
)
from finance_app.modules.categories.history import retrieve_historical_decision
from finance_app.modules.categories.llm import (
    LLM_BATCH_SIZE,
    LlmCategorizationRequestContext,
    chunked,
    pair_llm_results,
    request_llm_categories,
    unknown_llm_result,
    validated_llm_result,
)
from finance_app.modules.categories.llm_prompts import (
    amount_direction as prompt_amount_direction,
)
from finance_app.modules.categories.llm_prompts import (
    amount_magnitude as prompt_amount_magnitude,
)
from finance_app.modules.categories.llm_taxonomy import (
    MAX_CANDIDATE_TAGS,
    compact_category_candidates,
    prepare_llm_candidate_taxonomies,
    semantic_taxonomy_names,
)
from finance_app.modules.categories.repository import get_category_rules
from finance_app.modules.categories.rules_matching import score_category_rule_match
from finance_app.modules.categories.taxonomy import (
    get_category_rows,
    get_tag_options,
    get_tag_rows,
    get_transaction_tags_by_id,
    normalize_tag_names,
)
from finance_app.modules.merchants.normalization import normalize_merchant
from finance_app.modules.settings.runtime import get_float_setting, get_setting, get_unknown_category

CSV_TRUE_VALUES = {"1", "true", "yes", "y"}
CSV_FALSE_VALUES = {"0", "false", "no", "n"}
AMOUNT_MAGNITUDE_VALUES = {"small", "medium", "large", "very_large", "unknown"}
AMOUNT_DIRECTION_VALUES = {"debit", "credit", "zero", "unknown"}
LLM_EVAL_CONTEXT_COLD = "cold"
LLM_EVAL_CONTEXT_RULES = "rules"
LLM_EVAL_CONTEXT_FULL = "full"
LLM_EVAL_CONTEXT_MODES = (
    LLM_EVAL_CONTEXT_COLD,
    LLM_EVAL_CONTEXT_RULES,
    LLM_EVAL_CONTEXT_FULL,
)


@dataclass(frozen=True)
class LlmEvalRow:
    """One labeled transaction candidate from an offline eval CSV."""

    candidate_id: str
    merchant_key: str
    amount_direction: str
    amount_magnitude: str
    transaction_kind: str
    expected_category: str
    expected_tags: tuple[str, ...]
    should_remain_unknown: bool
    retain_for_eval: bool
    source_row_number: int
    source_statement_type_names: tuple[str, ...] = ()
    transaction_id: int | None = None

    @property
    def signature(self) -> tuple[str, str, str, str]:
        """Return the candidate identity used to find duplicate eval cases."""
        return (
            self.merchant_key,
            self.amount_direction,
            self.amount_magnitude,
            self.transaction_kind,
        )


@dataclass(frozen=True)
class LlmEvalValidationIssue:
    """Validation issue found in an offline eval dataset."""

    row_id: str
    code: str
    message: str


@dataclass(frozen=True)
class PreparedLlmEval:
    """Prepared retained rows and prompt context for a provider request."""

    rows: tuple[LlmEvalRow, ...]
    request_context: LlmCategorizationRequestContext
    unknown_category: str
    rules: Sequence[Mapping[str, Any]] = ()
    mode: str = LLM_EVAL_CONTEXT_COLD
    row_contexts: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)


@dataclass(frozen=True)
class LlmEvalPrediction:
    """Validated model prediction for one eval row."""

    row: LlmEvalRow
    category: str
    tags: tuple[str, ...]
    confidence: float | None
    needs_review: bool
    metadata: Mapping[str, Any]
    context: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LlmEvalMetrics:
    """Aggregate quality metrics for one offline eval run."""

    total: int
    category_correct: int
    concrete_category_total: int
    concrete_category_correct: int
    exact_match_correct: int
    tag_set_correct: int
    tag_true_positive: int
    tag_false_positive: int
    tag_false_negative: int
    unknown_true_positive: int
    unknown_false_positive: int
    unknown_false_negative: int
    expected_category_counts: Mapping[str, int]
    predicted_category_counts: Mapping[str, int]

    @property
    def category_accuracy(self) -> float:
        """Return exact category accuracy."""
        return ratio(self.category_correct, self.total)

    @property
    def concrete_category_accuracy(self) -> float:
        """Return category accuracy excluding expected UNKNOWN rows."""
        return ratio(self.concrete_category_correct, self.concrete_category_total)

    @property
    def exact_match_accuracy(self) -> float:
        """Return row accuracy requiring both category and exact tag set."""
        return ratio(self.exact_match_correct, self.total)

    @property
    def tag_set_accuracy(self) -> float:
        """Return row accuracy requiring exact tag-set equality."""
        return ratio(self.tag_set_correct, self.total)

    @property
    def tag_precision(self) -> float:
        """Return micro tag precision."""
        return ratio(self.tag_true_positive, self.tag_true_positive + self.tag_false_positive)

    @property
    def tag_recall(self) -> float:
        """Return micro tag recall."""
        return ratio(self.tag_true_positive, self.tag_true_positive + self.tag_false_negative)

    @property
    def tag_f1(self) -> float:
        """Return micro tag F1 score."""
        precision = self.tag_precision
        recall = self.tag_recall
        return ratio(2 * precision * recall, precision + recall)

    @property
    def unknown_precision(self) -> float:
        """Return precision for predicting UNKNOWN."""
        return ratio(self.unknown_true_positive, self.unknown_true_positive + self.unknown_false_positive)

    @property
    def unknown_recall(self) -> float:
        """Return recall for expected UNKNOWN rows."""
        return ratio(self.unknown_true_positive, self.unknown_true_positive + self.unknown_false_negative)


@dataclass(frozen=True)
class LlmEvalReport:
    """Predictions, metrics, and row-level failures from one eval run."""

    predictions: tuple[LlmEvalPrediction, ...]
    metrics: LlmEvalMetrics

    @property
    def mismatches(self) -> tuple[LlmEvalPrediction, ...]:
        """Return rows where category or tags differ from the expected labels."""
        return tuple(
            prediction
            for prediction in self.predictions
            if prediction.category != prediction.row.expected_category
            or set(prediction.tags) != set(prediction.row.expected_tags)
        )


def load_eval_csv(path: str | Path) -> list[LlmEvalRow]:
    """Read an offline categorization eval CSV."""
    rows: list[LlmEvalRow] = []
    with Path(path).open(newline="", encoding="utf-8") as csv_file:
        reader = csv.DictReader(csv_file)
        for index, raw_row in enumerate(reader, start=2):
            rows.append(eval_row_from_csv(raw_row, source_row_number=index))
    return rows


def load_eval_rows(path: str | Path) -> list[LlmEvalRow]:
    """Read an offline categorization eval CSV or XLSX workbook."""
    input_path = Path(path)
    if input_path.suffix.lower() in {".xlsx", ".xlsm"}:
        return load_eval_xlsx(input_path)
    return load_eval_csv(input_path)


def load_eval_xlsx(path: str | Path, sheet_name: str | None = None) -> list[LlmEvalRow]:
    """Read an offline categorization eval workbook without optional dependencies."""
    workbook_path = Path(path)
    matrix = read_xlsx_first_table(workbook_path, sheet_name=sheet_name)
    if not matrix:
        return []

    headers = [clean_cell(value) for value in matrix[0]]
    rows: list[LlmEvalRow] = []
    for index, values in enumerate(matrix[1:], start=2):
        row = {
            header: values[column_index] if column_index < len(values) else ""
            for column_index, header in enumerate(headers)
        }
        if any(clean_cell(value) for value in row.values()):
            rows.append(eval_row_from_csv(row, source_row_number=index))
    return rows


def read_xlsx_first_table(path: Path, sheet_name: str | None = None) -> list[list[Any]]:
    """Return the first sheet's rectangular values from a simple XLSX workbook."""
    with zipfile.ZipFile(path) as archive:
        shared_strings = read_xlsx_shared_strings(archive)
        sheet_path = first_sheet_path(archive, sheet_name=sheet_name)
        with archive.open(sheet_path) as worksheet_file:
            root = ElementTree.parse(worksheet_file).getroot()

    rows: list[list[Any]] = []
    for row_element in root.iterfind(".//{*}sheetData/{*}row"):
        values_by_column: dict[int, Any] = {}
        for cell in row_element.findall("{*}c"):
            column_index = xlsx_column_index(str(cell.attrib.get("r", "")))
            if column_index is None:
                continue
            values_by_column[column_index] = xlsx_cell_value(cell, shared_strings)
        if values_by_column:
            max_column = max(values_by_column)
            rows.append([values_by_column.get(column_index, "") for column_index in range(1, max_column + 1)])
    return rows


def read_xlsx_shared_strings(archive: zipfile.ZipFile) -> list[str]:
    """Return shared-string values from an XLSX archive."""
    if "xl/sharedStrings.xml" not in archive.namelist():
        return []
    with archive.open("xl/sharedStrings.xml") as shared_file:
        root = ElementTree.parse(shared_file).getroot()
    strings: list[str] = []
    for item in root.findall("{*}si"):
        strings.append("".join(text.text or "" for text in item.findall(".//{*}t")))
    return strings


def first_sheet_path(archive: zipfile.ZipFile, sheet_name: str | None = None) -> str:
    """Return the archive path for the requested or first workbook sheet."""
    with archive.open("xl/workbook.xml") as workbook_file:
        workbook_root = ElementTree.parse(workbook_file).getroot()
    with archive.open("xl/_rels/workbook.xml.rels") as rels_file:
        rels_root = ElementTree.parse(rels_file).getroot()

    targets_by_id = {
        str(rel.attrib.get("Id")): str(rel.attrib.get("Target"))
        for rel in rels_root.findall("{*}Relationship")
        if rel.attrib.get("Id") and rel.attrib.get("Target")
    }
    for sheet in workbook_root.findall(".//{*}sheet"):
        name = str(sheet.attrib.get("name") or "")
        if sheet_name and name != sheet_name:
            continue
        relationship_id = str(
            sheet.attrib.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id")
        )
        target = targets_by_id.get(relationship_id)
        if target:
            return xlsx_archive_path("xl", target)

    if sheet_name:
        raise ValueError(f"Workbook does not contain sheet {sheet_name!r}.")
    raise ValueError("Workbook does not contain any worksheets.")


def xlsx_archive_path(base_dir: str, target: str) -> str:
    """Return a normalized archive path for a workbook relationship target."""
    cleaned_target = target.lstrip("/")
    if cleaned_target.startswith("xl/"):
        return cleaned_target
    return f"{base_dir}/{cleaned_target}"


def xlsx_cell_value(cell: ElementTree.Element, shared_strings: Sequence[str]) -> Any:
    """Return a scalar value for one XLSX cell element."""
    cell_type = cell.attrib.get("t")
    if cell_type == "inlineStr":
        return "".join(text.text or "" for text in cell.findall(".//{*}is/{*}t"))

    value_element = cell.find("{*}v")
    if value_element is None or value_element.text is None:
        return ""
    value = value_element.text
    if cell_type == "s":
        index = int(value)
        return shared_strings[index] if 0 <= index < len(shared_strings) else ""
    if cell_type == "b":
        return value == "1"
    return value


def xlsx_column_index(cell_reference: str) -> int | None:
    """Return a one-based column index from an XLSX cell reference."""
    match = re.match(r"([A-Z]+)", cell_reference.upper())
    if not match:
        return None
    column_index = 0
    for char in match.group(1):
        column_index = column_index * 26 + ord(char) - ord("A") + 1
    return column_index


def eval_row_from_csv(raw_row: Mapping[str, object], source_row_number: int) -> LlmEvalRow:
    """Return one typed eval row from a CSV row mapping."""
    candidate_id = clean_cell(raw_row.get("candidate_id")) or clean_cell(raw_row.get("sample_id"))
    transaction_id = first_optional_int(
        raw_row.get("transaction_id"),
        raw_row.get("representative_transaction_id"),
        raw_row.get("source_transaction_id"),
    )
    return LlmEvalRow(
        candidate_id=candidate_id or f"row-{source_row_number}",
        merchant_key=clean_cell(raw_row.get("merchant_key")),
        amount_direction=clean_cell(raw_row.get("amount_direction")).lower(),
        amount_magnitude=clean_cell(raw_row.get("amount_magnitude")).lower(),
        transaction_kind=clean_cell(raw_row.get("transaction_kind")).lower(),
        expected_category=clean_cell(raw_row.get("expected_category")),
        expected_tags=tuple(split_tag_text(raw_row.get("expected_tags"))),
        should_remain_unknown=parse_csv_bool(raw_row.get("should_remain_unknown")),
        retain_for_eval=parse_csv_bool(raw_row.get("retain_for_eval")),
        source_row_number=source_row_number,
        source_statement_type_names=tuple(split_text_cell(raw_row.get("source_statement_type_names"))),
        transaction_id=transaction_id,
    )


def retained_eval_rows(rows: Sequence[LlmEvalRow]) -> tuple[LlmEvalRow, ...]:
    """Return only rows selected for evaluation."""
    return tuple(row for row in rows if row.retain_for_eval)


def validate_eval_rows(
    rows: Sequence[LlmEvalRow],
    category_options: Sequence[str],
    tag_options: Sequence[str],
    unknown_category: str = UNKNOWN_CATEGORY,
) -> list[LlmEvalValidationIssue]:
    """Validate retained eval rows against the active taxonomy and eval rules."""
    category_names = set(category_options)
    tag_names = set(tag_options)
    reimbursable_tag = builtin_tag_name_for_key(BUILTIN_TAG_REIMBURSABLE)
    issues: list[LlmEvalValidationIssue] = []
    retained = retained_eval_rows(rows)

    if not retained:
        issues.append(
            LlmEvalValidationIssue(
                row_id="dataset",
                code="no_retained_rows",
                message="No rows are marked retain_for_eval.",
            )
        )
        return issues

    signature_counts = Counter(row.signature for row in retained)
    for row in retained:
        issues.extend(
            validate_retained_eval_row(
                row,
                category_names,
                tag_names,
                reimbursable_tag,
                unknown_category,
            )
        )
        if signature_counts[row.signature] > 1:
            issues.append(
                LlmEvalValidationIssue(
                    row_id=row.candidate_id,
                    code="duplicate_candidate_signature",
                    message="Retained rows must have unique merchant, amount, and transaction-kind signatures.",
                )
            )
    return issues


def validate_retained_eval_row(
    row: LlmEvalRow,
    category_names: set[str],
    tag_names: set[str],
    reimbursable_tag: str,
    unknown_category: str,
) -> list[LlmEvalValidationIssue]:
    """Validate one retained eval row."""
    issues: list[LlmEvalValidationIssue] = []
    if not row.merchant_key:
        issues.append(issue(row, "missing_merchant_key", "Retained rows must include merchant_key."))
    if row.amount_direction not in AMOUNT_DIRECTION_VALUES:
        issues.append(issue(row, "invalid_amount_direction", f"Unsupported amount_direction: {row.amount_direction}."))
    if row.amount_magnitude not in AMOUNT_MAGNITUDE_VALUES:
        issues.append(issue(row, "invalid_amount_magnitude", f"Unsupported amount_magnitude: {row.amount_magnitude}."))
    if row.transaction_kind not in TRANSACTION_KINDS:
        issues.append(issue(row, "invalid_transaction_kind", f"Unsupported transaction_kind: {row.transaction_kind}."))
    if not row.expected_category:
        issues.append(issue(row, "missing_expected_category", "Retained rows must include expected_category."))
    elif row.expected_category not in category_names:
        issues.append(issue(row, "invalid_expected_category", f"Unknown expected_category: {row.expected_category}."))
    if row.should_remain_unknown != (row.expected_category == unknown_category):
        issues.append(
            issue(
                row,
                "unknown_flag_mismatch",
                "should_remain_unknown must match whether expected_category is UNKNOWN.",
            )
        )
    for tag in row.expected_tags:
        if tag not in tag_names:
            issues.append(issue(row, "invalid_expected_tag", f"Unknown expected tag: {tag}."))
    if reimbursable_tag in row.expected_tags and row.amount_direction != "debit":
        issues.append(
            issue(
                row,
                "reimbursable_requires_debit",
                "The Reimbursable tag can only be expected for debit transactions.",
            )
        )
    return issues


def prepare_llm_eval(
    conn: Any,
    rows: Sequence[LlmEvalRow],
    openai_model: str | None = None,
    mode: str = LLM_EVAL_CONTEXT_COLD,
) -> PreparedLlmEval:
    """Prepare retained eval rows and prompt context inside a short transaction."""
    if mode not in LLM_EVAL_CONTEXT_MODES:
        raise ValueError(f"Unsupported LLM eval context mode: {mode}.")

    retained = retained_eval_rows(rows)
    unknown_category = get_unknown_category(conn) or UNKNOWN_CATEGORY
    if mode == LLM_EVAL_CONTEXT_COLD:
        return prepare_cold_llm_eval(conn, retained, unknown_category, openai_model=openai_model)
    return prepare_contextual_llm_eval(conn, retained, unknown_category, openai_model=openai_model, mode=mode)


def prepare_cold_llm_eval(
    conn: Any,
    retained: Sequence[LlmEvalRow],
    unknown_category: str,
    openai_model: str | None = None,
) -> PreparedLlmEval:
    """Prepare taxonomy-only eval rows that simulate a new account."""
    category_rows = get_category_rows(conn)
    tag_rows = get_tag_rows(conn)
    category_options = [row["name"] for row in category_rows]
    tag_options = get_tag_options(conn)
    unknown_items = [eval_row_prompt_item(row, unknown_category, index) for index, row in enumerate(retained)]
    prepare_taxonomy_only_candidate_taxonomies(
        unknown_items,
        category_options,
        tag_options,
        unknown_category,
        category_rows,
        tag_rows,
    )
    request_context = build_eval_request_context(
        conn,
        unknown_items=unknown_items,
        category_options=category_options,
        tag_options=tag_options,
        category_rows=category_rows,
        tag_rows=tag_rows,
        openai_model=openai_model,
    )
    return PreparedLlmEval(
        rows=tuple(retained),
        request_context=request_context,
        unknown_category=unknown_category,
        rules=(),
        mode=LLM_EVAL_CONTEXT_COLD,
        row_contexts={
            row.candidate_id: {
                "mode": LLM_EVAL_CONTEXT_COLD,
                "db_match_status": "not_used",
                "db_match_count": 0,
                "rule_evidence": False,
                "historical_evidence": False,
                "candidate_categories": list(unknown_items[index].get("llm_candidate_categories") or []),
                "candidate_tags": list(unknown_items[index].get("llm_candidate_tags") or []),
            }
            for index, row in enumerate(retained)
        },
    )


def prepare_contextual_llm_eval(
    conn: Any,
    retained: Sequence[LlmEvalRow],
    unknown_category: str,
    openai_model: str | None,
    mode: str,
) -> PreparedLlmEval:
    """Prepare eval rows with DB-backed rules or full production evidence without writes."""
    category_rows = get_category_rows(conn)
    tag_rows = get_tag_rows(conn)
    category_options = [row["name"] for row in category_rows]
    tag_options = get_tag_options(conn)
    rules = get_category_rules(conn)
    matches = match_eval_rows_to_transactions(conn, retained)
    unknown_items = [
        contextual_eval_prompt_item(row, matches.get(row.candidate_id), unknown_category, index)
        for index, row in enumerate(retained)
    ]
    attach_contextual_eval_evidence(
        conn,
        unknown_items,
        mode=mode,
        unknown_category=unknown_category,
        category_options=category_options,
        rules=rules,
    )
    excluded_ids = [
        context["transaction_id"] for context in matches.values() if context.get("transaction_id") is not None
    ]
    if mode == LLM_EVAL_CONTEXT_RULES:
        prepare_rules_only_candidate_taxonomies(
            unknown_items,
            category_options,
            tag_options,
            unknown_category,
            category_rows,
            tag_rows,
        )
    else:
        prepare_llm_candidate_taxonomies(
            conn,
            unknown_items,
            category_options,
            tag_options,
            unknown_category,
            category_rows,
            tag_rows,
            exclude_transaction_ids=excluded_ids,
        )
    request_context = build_eval_request_context(
        conn,
        unknown_items=unknown_items,
        category_options=category_options,
        tag_options=tag_options,
        category_rows=category_rows,
        tag_rows=tag_rows,
        openai_model=openai_model,
    )
    return PreparedLlmEval(
        rows=tuple(retained),
        request_context=request_context,
        unknown_category=unknown_category,
        rules=rules if mode in {LLM_EVAL_CONTEXT_RULES, LLM_EVAL_CONTEXT_FULL} else (),
        mode=mode,
        row_contexts=eval_row_contexts(retained, unknown_items, matches, mode),
    )


def build_eval_request_context(
    conn: Any,
    unknown_items: list[MutableMapping[str, Any]],
    category_options: Sequence[str],
    tag_options: Sequence[str],
    category_rows: Sequence[Mapping[str, Any]],
    tag_rows: Sequence[Mapping[str, Any]],
    openai_model: str | None = None,
) -> LlmCategorizationRequestContext:
    """Return the common request context for one eval mode."""
    return LlmCategorizationRequestContext(
        unknown_items=unknown_items,
        category_options=category_options,
        tag_options=tag_options,
        category_rows=category_rows,
        tag_rows=tag_rows,
        confidence_threshold=get_float_setting(
            conn,
            "llm_confidence_threshold",
            settings.default_llm_confidence_threshold,
            minimum=0,
            maximum=1,
        ),
        review_threshold=get_float_setting(
            conn,
            "llm_review_threshold",
            settings.default_llm_review_threshold,
            minimum=0,
            maximum=1,
        ),
        verify_threshold=get_float_setting(
            conn,
            "verify_threshold",
            settings.default_verify_threshold,
            minimum=0,
            maximum=1,
        ),
        openai_model=openai_model or get_setting(conn, "openai_model") or settings.default_categorization_model,
    )


def eval_row_prompt_item(row: LlmEvalRow, unknown_category: str, index: int) -> MutableMapping[str, Any]:
    """Return a transaction-like prompt payload for one eval row."""
    return {
        "llm_request_id": str(index),
        "merchant_key": row.merchant_key,
        "description": row.merchant_key,
        "amount": amount_bucket_decimal(row.amount_direction, row.amount_magnitude),
        "category": unknown_category,
        "transaction_kind": row.transaction_kind,
    }


def contextual_eval_prompt_item(
    row: LlmEvalRow,
    match_context: Mapping[str, Any] | None,
    unknown_category: str,
    index: int,
) -> MutableMapping[str, Any]:
    """Return a DB-backed transaction clone for contextual eval modes."""
    db_row = match_context.get("row") if match_context else None
    if not db_row:
        return eval_row_prompt_item(row, unknown_category, index)

    return {
        "id": db_row["id"],
        "llm_request_id": str(index),
        "account_id": db_row["account_id"],
        "merchant_id": db_row["merchant_id"],
        "tx_date": db_row["tx_date"],
        "description": db_row["description"],
        "merchant_key": db_row["merchant_key"],
        "amount": db_row["amount"],
        "category": unknown_category,
        "category_id": None,
        "needs_review": 1,
        "category_source": CATEGORY_SOURCE_UNKNOWN,
        "transaction_kind": db_row["transaction_kind"],
    }


def prepare_taxonomy_only_candidate_taxonomies(
    unknown_items: Sequence[MutableMapping[str, Any]],
    category_options: Sequence[str],
    tag_options: Sequence[str],
    unknown_category: str,
    category_rows: Sequence[Mapping[str, Any]],
    tag_rows: Sequence[Mapping[str, Any]],
) -> None:
    """Attach candidate taxonomy hints without reading transaction history."""
    for tx in unknown_items:
        categories = semantic_taxonomy_names(tx, category_rows, category_options, unknown_category)
        categories.append(unknown_category)
        tx["llm_candidate_categories"] = compact_category_candidates(
            categories,
            category_options,
            unknown_category,
        )
        semantic_tags = normalize_tag_names(semantic_taxonomy_names(tx, tag_rows, tag_options), tag_options)
        tx["llm_candidate_tags"] = semantic_tags[:MAX_CANDIDATE_TAGS] or list(tag_options[:MAX_CANDIDATE_TAGS])


def prepare_rules_only_candidate_taxonomies(
    unknown_items: Sequence[MutableMapping[str, Any]],
    category_options: Sequence[str],
    tag_options: Sequence[str],
    unknown_category: str,
    category_rows: Sequence[Mapping[str, Any]],
    tag_rows: Sequence[Mapping[str, Any]],
) -> None:
    """Attach rule-and-taxonomy candidate hints without transaction history."""
    for tx in unknown_items:
        rule_evidence = tx.get("rule_evidence") or {}
        categories = [rule_evidence.get("category")]
        categories.extend(semantic_taxonomy_names(tx, category_rows, category_options, unknown_category))
        categories.append(unknown_category)
        tx["llm_candidate_categories"] = compact_category_candidates(
            categories,
            category_options,
            unknown_category,
        )
        tags = list(rule_evidence.get("tags") or [])
        tags.extend(semantic_taxonomy_names(tx, tag_rows, tag_options))
        normalized_tags = normalize_tag_names(tags, tag_options)
        tx["llm_candidate_tags"] = normalized_tags[:MAX_CANDIDATE_TAGS] or list(tag_options[:MAX_CANDIDATE_TAGS])


def attach_contextual_eval_evidence(
    conn: Any,
    transactions: Sequence[MutableMapping[str, Any]],
    mode: str,
    unknown_category: str,
    category_options: Sequence[str],
    rules: Sequence[Mapping[str, Any]],
) -> None:
    """Attach rule and historical evidence requested by one eval mode."""
    include_rules = mode in {LLM_EVAL_CONTEXT_RULES, LLM_EVAL_CONTEXT_FULL}
    include_history = mode == LLM_EVAL_CONTEXT_FULL
    for tx in transactions:
        if tx.get("amount") is not None:
            tx["amount"] = rounded_money_decimal(tx.get("amount"))
        normalized_merchant = normalize_merchant(tx.get("description", tx.get("merchant_key", "")), conn=conn)
        tx["merchant_key"] = normalized_merchant.cleaned_key or tx.get("merchant_key")
        tx["category"] = unknown_category
        tx["category_id"] = None
        tx["needs_review"] = 1
        tx["category_source"] = CATEGORY_SOURCE_UNKNOWN

        if include_rules:
            scored_rule = score_category_rule_match(
                tx.get("merchant_key"),
                tx.get("amount"),
                rules,
                merchant_candidate=tx.get("merchant_key"),
                raw_description=tx.get("description"),
                merchant_id=tx.get("merchant_id"),
                account_id=tx.get("account_id"),
                transaction_kind=tx.get("transaction_kind"),
            )
            if scored_rule is not None:
                tx["rule_evidence"] = rule_evidence_payload(
                    scored_rule,
                    normalize_eval_category(scored_rule.category, category_options, unknown_category),
                )

        if include_history:
            tx["historical_evidence"] = historical_evidence_payload(
                retrieve_historical_decision(conn, tx, unknown_category)
            )


def normalize_eval_category(
    value: object,
    category_options: Sequence[str],
    unknown_category: str,
) -> str:
    """Return an eval-safe category label for evidence payloads."""
    text = str(value or "").strip()
    for category in category_options:
        if category.casefold() == text.casefold():
            return category
    return unknown_category


def eval_row_contexts(
    rows: Sequence[LlmEvalRow],
    unknown_items: Sequence[Mapping[str, Any]],
    matches: Mapping[str, Mapping[str, Any]],
    mode: str,
) -> dict[str, Mapping[str, Any]]:
    """Return JSON-ready diagnostics for each prepared eval row."""
    contexts: dict[str, Mapping[str, Any]] = {}
    for row, item in zip(rows, unknown_items, strict=True):
        match = matches.get(row.candidate_id, {})
        contexts[row.candidate_id] = {
            "mode": mode,
            "db_match_status": match.get("status", "unmatched"),
            "db_match_count": match.get("match_count", 0),
            "db_transaction_id": match.get("transaction_id"),
            "db_statement_type_name": match.get("statement_type_name"),
            "rule_evidence": bool(item.get("rule_evidence")),
            "rule_category": ((item.get("rule_evidence") or {}).get("category")),
            "historical_evidence": bool((item.get("historical_evidence") or {}).get("category")),
            "historical_category": ((item.get("historical_evidence") or {}).get("category")),
            "historical_confidence": ((item.get("historical_evidence") or {}).get("confidence")),
            "candidate_categories": list(item.get("llm_candidate_categories") or []),
            "candidate_tags": list(item.get("llm_candidate_tags") or []),
        }
    return contexts


def evaluate_prepared_llm(
    prepared: PreparedLlmEval,
    request_categories: Callable[..., list[Any]] | None = None,
    batch_size: int | None = None,
) -> LlmEvalReport:
    """Run an injectable LLM request and score validated predictions."""
    requester = request_categories or request_llm_categories
    size = batch_size or LLM_BATCH_SIZE
    predictions: list[LlmEvalPrediction] = []
    rows_by_request_id = {str(index): row for index, row in enumerate(prepared.rows)}
    contexts_by_candidate_id = dict(prepared.row_contexts)

    for unknown_chunk in chunked(prepared.request_context.unknown_items, size):
        raw_results = requester(
            unknown_chunk,
            prepared.rules,
            prepared.request_context.category_options,
            prepared.request_context.tag_options,
            prepared.request_context.category_rows,
            prepared.request_context.tag_rows,
            prepared.request_context.openai_model,
            prepared.request_context.verify_threshold,
            prepared.request_context.review_threshold,
        )
        paired_request_ids: set[str] = set()
        for tx, raw_result in pair_llm_results(unknown_chunk, raw_results):
            request_id = str(tx.get("llm_request_id"))
            paired_request_ids.add(request_id)
            row = rows_by_request_id[request_id]
            if isinstance(raw_result, Mapping):
                accepted = validated_llm_result(
                    tx,
                    raw_result,
                    prepared.request_context,
                    prepared.unknown_category,
                )
            else:
                accepted = unknown_llm_result(
                    tx,
                    prepared.unknown_category,
                    failure_reason="llm_non_object_result",
                )
            predictions.append(
                prediction_from_result(
                    row,
                    accepted,
                    contexts_by_candidate_id.get(row.candidate_id, {}),
                )
            )
        for tx in unknown_chunk:
            request_id = str(tx.get("llm_request_id"))
            if request_id not in paired_request_ids:
                accepted = unknown_llm_result(
                    tx,
                    prepared.unknown_category,
                    failure_reason="llm_missing_result",
                )
                row = rows_by_request_id[request_id]
                predictions.append(
                    prediction_from_result(
                        row,
                        accepted,
                        contexts_by_candidate_id.get(row.candidate_id, {}),
                    )
                )

    predictions.sort(key=lambda prediction: prediction.row.source_row_number)
    return LlmEvalReport(
        predictions=tuple(predictions),
        metrics=score_predictions(predictions, prepared.unknown_category),
    )


def prediction_from_result(
    row: LlmEvalRow,
    result: Mapping[str, Any],
    context: Mapping[str, Any] | None = None,
) -> LlmEvalPrediction:
    """Return a typed prediction from a validated LLM result mapping."""
    raw_metadata = result.get("metadata")
    metadata: Mapping[str, Any] = raw_metadata if isinstance(raw_metadata, Mapping) else {}
    return LlmEvalPrediction(
        row=row,
        category=str(result.get("category") or UNKNOWN_CATEGORY),
        tags=tuple(normalize_tag_names(result.get("tags") or [])),
        confidence=result.get("confidence") if result.get("confidence") is None else float(result["confidence"]),
        needs_review=bool(result.get("needs_review")),
        metadata=metadata,
        context=context or {},
    )


def score_predictions(
    predictions: Sequence[LlmEvalPrediction],
    unknown_category: str = UNKNOWN_CATEGORY,
) -> LlmEvalMetrics:
    """Compute deterministic quality metrics for validated predictions."""
    category_correct = 0
    concrete_category_total = 0
    concrete_category_correct = 0
    exact_match_correct = 0
    tag_set_correct = 0
    tag_true_positive = 0
    tag_false_positive = 0
    tag_false_negative = 0
    unknown_true_positive = 0
    unknown_false_positive = 0
    unknown_false_negative = 0
    expected_category_counts: Counter[str] = Counter()
    predicted_category_counts: Counter[str] = Counter()

    for prediction in predictions:
        expected_category = prediction.row.expected_category
        predicted_category = prediction.category
        expected_tags = set(prediction.row.expected_tags)
        predicted_tags = set(prediction.tags)
        expected_category_counts[expected_category] += 1
        predicted_category_counts[predicted_category] += 1
        if predicted_category == expected_category:
            category_correct += 1
        if expected_category != unknown_category:
            concrete_category_total += 1
            if predicted_category == expected_category:
                concrete_category_correct += 1
        if predicted_tags == expected_tags:
            tag_set_correct += 1
        if predicted_category == expected_category and predicted_tags == expected_tags:
            exact_match_correct += 1
        tag_true_positive += len(expected_tags & predicted_tags)
        tag_false_positive += len(predicted_tags - expected_tags)
        tag_false_negative += len(expected_tags - predicted_tags)

        expected_unknown = expected_category == unknown_category
        predicted_unknown = predicted_category == unknown_category
        if expected_unknown and predicted_unknown:
            unknown_true_positive += 1
        elif not expected_unknown and predicted_unknown:
            unknown_false_positive += 1
        elif expected_unknown and not predicted_unknown:
            unknown_false_negative += 1

    return LlmEvalMetrics(
        total=len(predictions),
        category_correct=category_correct,
        concrete_category_total=concrete_category_total,
        concrete_category_correct=concrete_category_correct,
        exact_match_correct=exact_match_correct,
        tag_set_correct=tag_set_correct,
        tag_true_positive=tag_true_positive,
        tag_false_positive=tag_false_positive,
        tag_false_negative=tag_false_negative,
        unknown_true_positive=unknown_true_positive,
        unknown_false_positive=unknown_false_positive,
        unknown_false_negative=unknown_false_negative,
        expected_category_counts=dict(expected_category_counts),
        predicted_category_counts=dict(predicted_category_counts),
    )


def match_eval_rows_to_transactions(
    conn: Any,
    rows: Sequence[LlmEvalRow],
) -> dict[str, Mapping[str, Any]]:
    """Match eval rows to representative persisted transactions."""
    candidates = eval_transaction_candidates(conn)
    by_id = {candidate["id"]: candidate for candidate in candidates}
    candidates_by_signature: dict[tuple[str, str, str, str], list[Mapping[str, Any]]] = {}
    for candidate in candidates:
        candidates_by_signature.setdefault(candidate["signature"], []).append(candidate)

    contexts: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        if row.transaction_id is not None:
            transaction_candidate = by_id.get(row.transaction_id)
            contexts[row.candidate_id] = matched_context(
                transaction_candidate,
                1 if transaction_candidate else 0,
            )
            continue

        matches = list(candidates_by_signature.get(row.signature, ()))
        if row.source_statement_type_names:
            statement_matches = [
                candidate
                for candidate in matches
                if candidate.get("statement_type_name") in set(row.source_statement_type_names)
            ]
            if statement_matches:
                matches = statement_matches
        contexts[row.candidate_id] = matched_context(best_eval_transaction_match(matches), len(matches))
    return contexts


def eval_transaction_candidates(conn: Any) -> list[Mapping[str, Any]]:
    """Return DB transactions annotated with eval matching signatures."""
    rows = (
        conn.execute(
            select(
                transactions_table.c.id,
                transactions_table.c.statement_id,
                transactions_table.c.account_id,
                transactions_table.c.merchant_id,
                merchants_table.c.merchant_key.label("stored_merchant_key"),
                statement_types_table.c.name.label("statement_type_name"),
                transactions_table.c.tx_date,
                transactions_table.c.description,
                transactions_table.c.amount,
                transactions_table.c.category,
                transactions_table.c.category_id,
                transactions_table.c.needs_review,
                transactions_table.c.category_source,
                transactions_table.c.category_confidence,
                transactions_table.c.category_rule_id,
                transactions_table.c.category_metadata,
                transactions_table.c.ignored,
                transactions_table.c.transaction_kind,
                transactions_table.c.fingerprint,
            )
            .select_from(
                transactions_table.outerjoin(
                    merchants_table,
                    merchants_table.c.id == transactions_table.c.merchant_id,
                )
                .outerjoin(statements_table, statements_table.c.id == transactions_table.c.statement_id)
                .outerjoin(statement_types_table, statement_types_table.c.id == statements_table.c.statement_type_id)
            )
            .where(transactions_table.c.ignored == 0)
        )
        .mappings()
        .fetchall()
    )
    tags_by_id = get_transaction_tags_by_id(conn, [row["id"] for row in rows])
    candidates: list[Mapping[str, Any]] = []
    for row in rows:
        merchant_key = row["stored_merchant_key"] or normalize_merchant(row["description"], conn=conn).cleaned_key
        candidate = dict(row)
        candidate["merchant_key"] = merchant_key
        candidate["tags"] = tags_by_id.get(row["id"], [])
        candidate["signature"] = (
            merchant_key,
            prompt_amount_direction(row["amount"]),
            prompt_amount_magnitude(row["amount"]),
            row["transaction_kind"],
        )
        candidates.append(candidate)
    return candidates


def best_eval_transaction_match(matches: Sequence[Mapping[str, Any]]) -> Mapping[str, Any] | None:
    """Return the deterministic representative transaction for one eval row."""
    if not matches:
        return None
    return sorted(
        matches,
        key=lambda row: (
            str(row.get("tx_date") or ""),
            int(row.get("id") or 0),
        ),
        reverse=True,
    )[0]


def matched_context(candidate: Mapping[str, Any] | None, match_count: int) -> Mapping[str, Any]:
    """Return a row match diagnostic payload."""
    if not candidate:
        return {
            "status": "unmatched",
            "match_count": 0,
            "row": None,
            "transaction_id": None,
            "statement_type_name": None,
        }
    return {
        "status": "matched" if match_count <= 1 else "ambiguous",
        "match_count": match_count,
        "row": candidate,
        "transaction_id": candidate["id"],
        "statement_type_name": candidate.get("statement_type_name"),
    }


def amount_bucket_decimal(direction: str, magnitude: str) -> Decimal | None:
    """Return a synthetic amount that preserves only direction and magnitude."""
    magnitude_amounts = {
        "small": Decimal("10.00"),
        "medium": Decimal("50.00"),
        "large": Decimal("250.00"),
        "very_large": Decimal("1000.00"),
    }
    amount = magnitude_amounts.get(magnitude)
    if amount is None:
        return None
    if direction == "credit":
        return -amount
    if direction == "zero":
        return Decimal("0.00")
    return amount


def split_tag_text(value: object) -> list[str]:
    """Split a CSV tag cell into ordered tag names."""
    return [
        tag
        for tag in normalize_tag_names(
            str(value or "").replace(";", "|").replace(",", "|").split("|"),
            allowed_tags=None,
        )
    ]


def split_text_cell(value: object) -> list[str]:
    """Split a generic multi-value CSV or workbook cell into text values."""
    return [chunk.strip() for chunk in str(value or "").replace(";", "|").replace(",", "|").split("|") if chunk.strip()]


def first_optional_int(*values: object) -> int | None:
    """Return the first positive integer parsed from optional cell values."""
    for value in values:
        parsed = optional_int(value)
        if parsed is not None:
            return parsed
    return None


def optional_int(value: object) -> int | None:
    """Return a positive integer from a cell value, when present."""
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = int(float(text))
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def parse_csv_bool(value: object) -> bool:
    """Return a permissive boolean value for CSV flag cells."""
    normalized = str(value or "").strip().lower()
    if normalized in CSV_TRUE_VALUES:
        return True
    if normalized in CSV_FALSE_VALUES:
        return False
    return False


def clean_cell(value: object) -> str:
    """Return a stripped string for one CSV cell."""
    return str(value or "").strip()


def issue(row: LlmEvalRow, code: str, message: str) -> LlmEvalValidationIssue:
    """Build one validation issue for an eval row."""
    return LlmEvalValidationIssue(row_id=row.candidate_id, code=code, message=message)


def ratio(numerator: float, denominator: float) -> float:
    """Return a safe ratio, using 0 when the denominator is empty."""
    if denominator == 0:
        return 0.0
    return numerator / denominator
