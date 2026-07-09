# FinScope LLM Categorization Evals

This directory contains tooling for evaluating prompt candidates used to
categorize financial transactions. It is intentionally separate from the
FinScope application runtime: it does not import `finance_app` and does not read
or write runtime databases. Most commands are offline-only; `run_eval` is the
explicit command that can call the OpenAI API.

The current implementation provides methodology notes, a placeholder baseline
prompt, JSONL helpers, typed schema structures, strict dataset validation,
database inspection, draft dataset extraction, dataset summaries, and
reproducible splitting, dry-run prompt rendering, and saved-output scoring.
The eval runner can call OpenAI for saved raw outputs. Prompt-comparison
reports are generated from saved scoring artifacts.

## Workflow

1. Inspect database.
2. Build draft dataset.
3. Manually curate examples.
4. Validate dataset.
5. Split dataset.
6. Render prompt for inspection.
7. Run prompt evaluation.
8. Score outputs.
9. Compare prompt candidates.
10. Inspect failures and revise prompts.

## Dataset validation

Store curated examples as JSON Lines files under `datasets/`. Each non-empty
line must contain one evaluation example with this shape:

```json
{
  "request_id": "example-001",
  "transaction": {
    "description": "GROCERY STORE",
    "merchant": "GROCERY STORE",
    "amount": 42.25,
    "date": "2026-05-03",
    "account": "Credit card",
    "statement_type": "credit_card"
  },
  "candidate_taxonomy": {
    "categories": [
      {
        "id": "cat_unknown",
        "name": "UNKNOWN",
        "description": "Unresolved transactions.",
        "instruction": null
      },
      {
        "id": "cat_groceries",
        "name": "Groceries",
        "description": "Food and household grocery purchases.",
        "instruction": "Use for grocery stores and supermarkets."
      }
    ],
    "tags": [
      {
        "id": "tag_household",
        "name": "Household",
        "description": "Household-related spending.",
        "instruction": null
      }
    ]
  },
  "similar_transactions": [
    {
      "description": "GROCERY STORE",
      "amount": 39.8,
      "category_id": "cat_groceries",
      "tag_ids": ["tag_household"],
      "evidence_type": "history",
      "confidence": 0.91
    }
  ],
  "expected": {
    "category_id": "cat_groceries",
    "tag_ids": ["tag_household"],
    "needs_review": false
  },
  "label_source": "reviewed",
  "privacy_level": "redacted_real",
  "coverage": {
    "category": "Groceries",
    "tags": ["Household"],
    "direction": "debit",
    "statement_type": "credit_card",
    "confidence_band": "high",
    "ambiguity_type": "straightforward"
  },
  "notes": "Clear grocery merchant."
}
```

Validate a dataset with:

```powershell
.\.venv\Scripts\python.exe -B -m evals.llm_categorization.tools.validate_dataset evals\llm_categorization\datasets\example.jsonl
```

The validator checks JSONL syntax, strict object fields, unique request IDs,
taxonomy ID references, numeric amounts, confidence probability ranges, boolean
review flags, expected `UNKNOWN` consistency, and signed-amount direction
consistency. Nullable values such as unknown merchants, dates, accounts,
statement types, and ambiguity types must be present as `null` or `"unknown"`
where the schema allows that value.

Successful validation prints counts for examples, unique request IDs, category
coverage, tag coverage, label sources, privacy levels, directions, review flags,
expected `UNKNOWN` labels, ambiguity types, and statement types. Methodology
risks, such as fewer than 80 examples, missing expected `UNKNOWN`, missing
review-needed examples, missing debit or credit examples, missing tag/no-tag
coverage, category or tag coverage gaps, and missing benchmark ambiguity strata,
are reported as warnings. Schema and cross-reference failures are reported with
line numbers and exit with a non-zero status.

Summarize curation coverage with:

```powershell
.\.venv\Scripts\python.exe -B -m evals.llm_categorization.tools.summarize_dataset `
  --input evals\llm_categorization\datasets\curated.jsonl `
  --out evals\llm_categorization\datasets\curated_summary.md
```

The summary reports counts, category and tag coverage, missing taxonomy values,
examples needing review, expected `UNKNOWN` counts, high-trust versus low-trust
label counts, and warnings when important strata are absent.

## Dataset splitting

After manual curation, create deterministic development, validation, and
held-out test splits:

```powershell
.\.venv\Scripts\python.exe -B -m evals.llm_categorization.tools.split_dataset `
  --input evals\llm_categorization\datasets\curated.jsonl `
  --out-dir evals\llm_categorization\datasets `
  --dev-ratio 0.5 `
  --validation-ratio 0.3 `
  --test-ratio 0.2 `
  --seed 42
```

The splitter validates the input first, keeps near-duplicates and known source
variants in the same split, balances coverage strata as much as possible, and
writes `dev.jsonl`, `validation.jsonl`, `test.jsonl`, and `split_report.md`.

Use the splits as follows:

- Development set: prompt design and iteration.
- Validation set: prompt candidate selection.
- Held-out test set: final estimate after prompt selection only.

Do not tune prompts directly against the held-out test set. Do not encode
merchant-specific fixes in the prompt when a rule or taxonomy fix is more
appropriate. Use failure categories to revise prompts, not isolated examples.

## Prompt rendering

Render the exact message payload for one prompt candidate and one dataset
example without calling an API:

```powershell
.\.venv\Scripts\python.exe -B -m evals.llm_categorization.tools.render_prompt `
  --prompt evals\llm_categorization\prompts\001_baseline.md `
  --dataset evals\llm_categorization\datasets\dev.jsonl `
  --request-id example-001 `
  --out evals\llm_categorization\runs\rendered_example.txt
```

Use dry-run mode to inspect the first few examples in dataset order:

```powershell
.\.venv\Scripts\python.exe -B -m evals.llm_categorization.tools.render_prompt `
  --prompt evals\llm_categorization\prompts\001_baseline.md `
  --dataset evals\llm_categorization\datasets\dev.jsonl `
  --dry-run 5 `
  --out evals\llm_categorization\runs\rendered_dry_run.txt
```

Rendered inputs include transaction fields, candidate taxonomy, similar
transaction evidence, the required JSON output format, and conservative review
instructions. They intentionally exclude expected labels, coverage metadata,
label sources, privacy levels, and curator notes.

## Running prompt candidates

Run one prompt candidate against one dataset split and save raw outputs:

```powershell
.\.venv\Scripts\python.exe -B -m evals.llm_categorization.tools.run_eval `
  --prompt evals\llm_categorization\prompts\001_baseline.md `
  --dataset evals\llm_categorization\datasets\validation.jsonl `
  --model gpt-5-mini `
  --temperature 0 `
  --out-dir evals\llm_categorization\runs\validation_001_baseline
```

The runner reads the API key using the project convention: `OPENAI_API_KEY`
first, then `config.ini` under `[api_keys] openai_api_key`. It writes
`config.json`, `prompt.md`, `dataset.meta.json`, and `raw_outputs.jsonl`.
Unless `--no-score` is passed, it also runs the scorer and writes scoring
artifacts in the same run directory.

Use dry-run mode before any provider call:

```powershell
.\.venv\Scripts\python.exe -B -m evals.llm_categorization.tools.run_eval `
  --prompt evals\llm_categorization\prompts\001_baseline.md `
  --dataset evals\llm_categorization\datasets\validation.jsonl `
  --model gpt-5-mini `
  --dry-run `
  --limit 3 `
  --no-score `
  --out-dir evals\llm_categorization\runs\dry_run_001_baseline
```

Use `--limit N` for smoke tests, `--request-id ID` for one-example debugging,
and `--resume` to skip request IDs already present in `raw_outputs.jsonl`.
When auto-scoring a limited or one-example run, the runner scores only the
selected examples by writing `dataset.selected.jsonl` in the run directory.

Safety notes:

- Run dry-run inspection before spending API calls.
- Do not run or tune against the held-out test set until after prompt selection.
- The runner renders inputs with the same helper used by dry-run inspection, so
  expected labels and curation metadata are excluded from model input.
- The runner never writes to `finscope.db`; keep eval artifacts under
  `evals/llm_categorization/runs/`.
- Do not commit raw `finscope.db`, API keys, unredacted statements, or raw
  personal financial data.
- Review and redact datasets before committing. Use `privacy_level` values
  consistently: `raw_real`, `redacted_real`, or `synthetic`.
- Keep synthetic examples clearly separate from real examples in notes,
  filenames, or split construction.

## Scoring saved outputs

Score saved raw model outputs against a labeled split without calling an API:

```powershell
.\.venv\Scripts\python.exe -B -m evals.llm_categorization.tools.score_outputs `
  --dataset evals\llm_categorization\datasets\validation.jsonl `
  --outputs evals\llm_categorization\runs\some_run\raw_outputs.jsonl `
  --out-dir evals\llm_categorization\runs\some_run
```

Each raw-output JSONL row must contain `request_id`, `raw_output`, `model`, and
`prompt_id`. The `raw_output` value is parsed as strict JSON and must be one
object with `request_id`, `category_id`, `tag_ids`, `confidence`,
`needs_review`, `supported_by_similar_transactions`, and `reason`.

The scorer writes `parsed_outputs.jsonl`, `scored_outputs.jsonl`,
`failures.jsonl`, `metrics.json`, and `report.md`. Invalid JSON, invalid schema,
and invalid taxonomy IDs are tracked separately; examples with invalid outputs
receive zero semantic score. The composite score is a convenience signal only,
not an authoritative ranking.

## Comparing prompt runs

Compare scored prompt runs on the same dataset:

```powershell
.\.venv\Scripts\python.exe -B -m evals.llm_categorization.tools.compare_runs `
  --runs `
    evals\llm_categorization\runs\validation_001_baseline `
    evals\llm_categorization\runs\validation_002_conservative `
    evals\llm_categorization\runs\validation_003_strict_tags `
  --out evals\llm_categorization\runs\comparisons\validation_prompt_comparison.md
```

The comparison reads each run's `metrics.json`, `config.json`,
`failures.jsonl`, and `scored_outputs.jsonl`; it also summarizes token usage
from `raw_outputs.jsonl` when available. The report warns when dataset hashes
do not match, includes disagreement examples, highlights unsafe versus uniquely
correct cases, and presents interpretation notes for prompt, taxonomy, rule,
and manual-adjudication follow-up. Do not select a prompt from the composite
score alone.

## Run artifacts

Keep generated evaluation artifacts organized by run directory under `runs/`.
A typical provider-backed run contains:

- `config.json`: reproducibility metadata, prompt and dataset hashes, model,
  temperature, response format, retry policy, timestamp, and scorer version when
  auto-scoring runs.
- `prompt.md`: the exact prompt candidate file copied into the run.
- `dataset.meta.json`: dataset hash, total examples, selected examples, and
  selected request IDs.
- `raw_outputs.jsonl`: one saved provider response per request ID.
- `parsed_outputs.jsonl`, `scored_outputs.jsonl`, `failures.jsonl`,
  `metrics.json`, and `report.md` after scoring.

Dry runs write `rendered_prompts.jsonl` instead of calling the provider.
Comparison reports should live under `runs/comparisons/`.

## Database inspection

Before building a draft dataset, inspect the local SQLite database in read-only
mode and write a coverage report:

```powershell
.\.venv\Scripts\python.exe -B -m evals.llm_categorization.tools.inspect_db --db runtime\finescope.db --out evals\llm_categorization\datasets\db_inspection_report.md
```

The inspection report is based on schema inference, not guaranteed table roles.
It reports relevant tables and columns, safe aggregate counts, benchmark
coverage readiness, missing concepts, and evaluation risks without printing raw
merchant names or transaction descriptions.

## Draft extraction

Create a redacted, manually curated starting point from the local SQLite
database:

```powershell
.\.venv\Scripts\python.exe -B -m evals.llm_categorization.tools.build_dataset_from_db `
  --db runtime\finescope.db `
  --out evals\llm_categorization\datasets\draft_from_db.jsonl `
  --coverage-report evals\llm_categorization\datasets\draft_coverage_report.md `
  --adjudication-out evals\llm_categorization\datasets\adjudication_needed.jsonl `
  --max-examples 150
```

The extractor opens SQLite in read-only mode, selects a deterministic
stratified draft sample, includes the full current candidate taxonomy in each
example, and redacts transaction free text to generic classification cues.
Uncertain but potentially useful examples are written separately to
`adjudication_needed.jsonl` with the adjudication reason in `notes`.
