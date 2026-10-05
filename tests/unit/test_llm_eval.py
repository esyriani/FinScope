"""Unit tests for offline LLM categorization evaluation helpers."""

from zipfile import ZIP_DEFLATED, ZipFile

from finance_app.modules.categories.llm_eval import (
    LlmEvalPrediction,
    LlmEvalRow,
    load_eval_rows,
    score_predictions,
    validate_eval_rows,
)


def eval_row(
    candidate_id,
    expected_category,
    *,
    amount_direction="debit",
    expected_tags=(),
    should_remain_unknown=False,
):
    """Build a retained eval row for unit tests."""
    return LlmEvalRow(
        candidate_id=candidate_id,
        merchant_key=f"MERCHANT {candidate_id}",
        amount_direction=amount_direction,
        amount_magnitude="medium",
        transaction_kind="expense" if amount_direction == "debit" else "income",
        expected_category=expected_category,
        expected_tags=tuple(expected_tags),
        should_remain_unknown=should_remain_unknown,
        retain_for_eval=True,
        source_row_number=2,
    )


def prediction(row, category, tags=()):
    """Build one eval prediction for metric tests."""
    return LlmEvalPrediction(
        row=row,
        category=category,
        tags=tuple(tags),
        confidence=0.91,
        needs_review=False,
        metadata={},
    )


def test_validate_eval_rows_checks_taxonomy_unknown_flag_and_reimbursable_direction():
    """Verify eval CSV validation catches label and semantic problems."""
    rows = [
        eval_row("valid", "Food", expected_tags=("Tax",)),
        eval_row("bad-category", "NotReal"),
        eval_row("bad-tag", "Food", expected_tags=("NotReal",)),
        eval_row("bad-unknown", "Food", should_remain_unknown=True),
        eval_row("bad-reimbursable", "Food", amount_direction="credit", expected_tags=("Reimbursable",)),
    ]

    issues = validate_eval_rows(rows, ["Food", "UNKNOWN"], ["Tax", "Reimbursable"])

    assert {(issue.row_id, issue.code) for issue in issues} == {
        ("bad-category", "invalid_expected_category"),
        ("bad-tag", "invalid_expected_tag"),
        ("bad-unknown", "unknown_flag_mismatch"),
        ("bad-reimbursable", "reimbursable_requires_debit"),
    }


def test_score_predictions_reports_category_unknown_and_tag_quality():
    """Verify eval metrics summarize category, UNKNOWN, and tag quality."""
    food = eval_row("food", "Food", expected_tags=("Grocery",))
    unknown = eval_row("unknown", "UNKNOWN", should_remain_unknown=True)
    travel = eval_row("travel", "Travel", expected_tags=("Reimbursable",))
    expected_unknown_missed = eval_row("ambiguous", "UNKNOWN", should_remain_unknown=True)

    metrics = score_predictions(
        [
            prediction(food, "Food", ("Grocery",)),
            prediction(unknown, "UNKNOWN"),
            prediction(travel, "Travel"),
            prediction(expected_unknown_missed, "Food"),
        ]
    )

    assert metrics.total == 4
    assert metrics.category_correct == 3
    assert metrics.concrete_category_correct == 2
    assert metrics.concrete_category_total == 2
    assert metrics.exact_match_correct == 2
    assert metrics.tag_true_positive == 1
    assert metrics.tag_false_positive == 0
    assert metrics.tag_false_negative == 1
    assert metrics.unknown_true_positive == 1
    assert metrics.unknown_false_negative == 1
    assert metrics.category_accuracy == 0.75
    assert metrics.tag_precision == 1.0
    assert metrics.tag_recall == 0.5


def test_load_eval_rows_reads_basic_xlsx_without_optional_dependencies(tmp_path):
    """Verify the eval loader can read workbook oracles through stdlib XML parsing."""
    workbook_path = tmp_path / "oracle.xlsx"
    write_basic_eval_workbook(workbook_path)

    rows = load_eval_rows(workbook_path)

    assert len(rows) == 1
    assert rows[0].merchant_key == "METRO"
    assert rows[0].amount_direction == "debit"
    assert rows[0].expected_category == "Food"
    assert rows[0].expected_tags == ("Grocery",)
    assert rows[0].retain_for_eval is True


def write_basic_eval_workbook(path):
    """Write a minimal XLSX workbook with one eval row."""
    worksheet_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <sheetData>
    <row r="1">
      <c r="A1" t="inlineStr"><is><t>merchant_key</t></is></c>
      <c r="B1" t="inlineStr"><is><t>amount_direction</t></is></c>
      <c r="C1" t="inlineStr"><is><t>amount_magnitude</t></is></c>
      <c r="D1" t="inlineStr"><is><t>transaction_kind</t></is></c>
      <c r="E1" t="inlineStr"><is><t>retain_for_eval</t></is></c>
      <c r="F1" t="inlineStr"><is><t>expected_category</t></is></c>
      <c r="G1" t="inlineStr"><is><t>expected_tags</t></is></c>
      <c r="H1" t="inlineStr"><is><t>should_remain_unknown</t></is></c>
    </row>
    <row r="2">
      <c r="A2" t="inlineStr"><is><t>METRO</t></is></c>
      <c r="B2" t="inlineStr"><is><t>debit</t></is></c>
      <c r="C2" t="inlineStr"><is><t>medium</t></is></c>
      <c r="D2" t="inlineStr"><is><t>expense</t></is></c>
      <c r="E2"><v>1</v></c>
      <c r="F2" t="inlineStr"><is><t>Food</t></is></c>
      <c r="G2" t="inlineStr"><is><t>Grocery</t></is></c>
      <c r="H2"><v>0</v></c>
    </row>
  </sheetData>
</worksheet>"""
    with ZipFile(path, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr(
            "[Content_Types].xml",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
  <Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
</Types>""",
        )
        archive.writestr(
            "_rels/.rels",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>""",
        )
        archive.writestr(
            "xl/workbook.xml",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets>
</workbook>""",
        )
        archive.writestr(
            "xl/_rels/workbook.xml.rels",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
</Relationships>""",
        )
        archive.writestr("xl/worksheets/sheet1.xml", worksheet_xml)
