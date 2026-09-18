# FinScope rule-engine mutation categorization review

## Scope and setup

This first mutation-testing experiment targets only
`src/finance_app/modules/rules/engine.py`, the pure domain engine for category
rule matching, direction checks, assignment metadata, and transaction-kind
selection.

Infrastructure added for the experiment:

- `cosmic-ray-rules-engine.toml` configures Cosmic Ray with the local
  distributor, a 45 second per-mutant timeout, and
  `src/finance_app/modules/rules/engine.py` as the mutation target.
- `tools/cosmic_ray_rule_engine_tests.py` is the test command invoked by Cosmic
  Ray. It locates the repo virtualenv and runs the existing direct rule-engine
  unit tests: `tests/unit/test_rule_engine_helpers.py`.
- `tools/cosmic_ray_summary.py` summarizes `cosmic-ray dump` JSONL output into
  counts, mutation score, and survivor labels.
- Runtime session files and dumps are kept under `runtime/mutation/`, which is
  ignored by Git.
- Cosmic Ray is declared as a development dependency and pinned through the
  project constraints.

No tests were added or modified before this first campaign. The experiment is
documented as an opt-in local lane, not a CI gate.

## Baseline

Baseline command:

```powershell
.\.venv\Scripts\cosmic-ray.exe baseline cosmic-ray-rules-engine.toml
```

The unmutated baseline passed.

The focused runner also passed directly:

```text
45 passed in 0.20s
```

## Mutation results

Fresh session:

```powershell
.\.venv\Scripts\cosmic-ray.exe init --force cosmic-ray-rules-engine.toml runtime\mutation\rules-engine.sqlite
.\.venv\Scripts\cosmic-ray.exe exec cosmic-ray-rules-engine.toml runtime\mutation\rules-engine.sqlite
.\.venv\Scripts\cosmic-ray.exe dump runtime\mutation\rules-engine.sqlite > runtime\mutation\rules-engine-dump.jsonl
.\.venv\Scripts\python.exe tools\cosmic_ray_summary.py runtime\mutation\rules-engine-dump.jsonl
```

Run window: 2026-09-17 11:48:04 to 12:03:16 America/Toronto, about 15 minutes.

| Metric | Count |
| --- | ---: |
| Total mutants | 195 |
| Killed | 169 |
| Survived | 26 |
| Timeout | 0 |
| Incompetent | 0 |
| Error or abnormal worker result | 0 |
| Pending | 0 |
| Mutation score | 86.67% |

All 195 worker outcomes were normal.

## Surviving mutant analysis

### Merchant-bound rule matching

Survivors:

- `rule_preview_matches_transaction`, line 42:
  `!=` changed to `<`, `>`, and `is not`.
- `rule_matches_transaction`, line 76:
  `==` changed to `<=` and `is`.

Classification: actionable test gap with some identity-operator noise.

The tests cover equal merchant IDs, missing transaction merchant IDs, and one
apply-side mismatched ID. They do not cover preview-side mismatched IDs, both
lower-than and higher-than ID orderings, or large/non-interned integer IDs. The
ordering mutants survived because a single mismatched value can accidentally
exercise only one side of an invalid comparator. The `is`/`is not` survivors
are partly Python identity artifacts, but they still show the current tests use
small IDs where equality and identity can behave the same.

Recommended follow-up: add merchant-bound preview and apply cases with rule ID
and transaction merchant IDs on both sides of the comparison, using IDs outside
the small-integer intern range.

### Account constraint matching

Survivor:

- `rule_account_matches_transaction`, line 103:
  `==` changed to `is`.

Classification: actionable but low-risk test gap.

The account tests use `10` and `"10"`, which can survive an identity mutation
after integer conversion. The behavior should be value equality, not identity.

Recommended follow-up: add account constraint cases with larger account IDs and
mixed string/integer inputs.

### Income-category zero and near-zero boundaries

Survivors:

- `rule_preview_matches_transaction`, line 55:
  `>= 0` changed to `!= 0`, `> 0`, `>= 1`, and `>= -1`.
- `rule_matches_transaction`, line 64:
  `>= 0` changed to `> 0`, `>= 1`, and `>= -1`.

Classification: actionable test gap.

The tests prove that positive amounts are rejected for income categories and
that a large negative income amount can match. They do not prove the exact zero
boundary or near-zero values around it. Those cases matter because FinScope uses
signed amounts to distinguish income/credits from spending.

Recommended follow-up: add preview and apply cases for income rules with
amounts such as `-0.50`, `0.00`, and `0.50`, and assert that zero is treated as
not income.

### Direction matching

Survivors:

- `rule_direction_matches_transaction`, line 117:
  `direction == any` changed to `direction <= any`.
- `rule_direction_matches_transaction`, line 122:
  `direction == debit` changed to `direction >= debit`.
- `rule_direction_matches_transaction`, line 123:
  `amount >= 0` changed to `amount >= -1`.
- `rule_direction_matches_transaction`, line 124:
  `direction == credit` changed to `direction <= credit`,
  `direction >= credit`, and `direction is not credit`.
- `rule_direction_matches_transaction`, line 125:
  `amount < 0` changed to `amount < -1`.
- `rule_direction_matches_transaction`, line 126:
  final fallback `return True` changed to `return False`.

Classification: mixed actionable gaps and equivalent/noise mutants.

The numeric boundary survivors at lines 123 and 125 are actionable. Existing
tests use `0.00`, a positive amount, and a large negative amount, but they do
not cover small negative amounts for debit rejection or credit acceptance.

The string-ordering comparison mutants are likely equivalent within the current
implementation because `rule_direction()` normalizes output to only `any`,
`debit`, or `credit`, and the surrounding branch order masks several invalid
comparators. The final fallback mutation is also effectively unreachable after
normalization. These are useful signals that the defensive tail may be
unnecessary or should be intentionally excluded from mutation scoring if it
remains.

Recommended follow-up: add small signed boundary cases for debit and credit
rules. Separately consider simplifying the unreachable final fallback or
excluding equivalent direction-string comparator mutants in future campaigns.

### Transaction-kind classification

Survivors:

- `rule_transaction_kind`, line 166:
  `category == Transfers` changed to `category >= Transfers` and
  `category is Transfers`.
- `rule_transaction_kind`, line 168:
  `current_kind == refund` changed to `current_kind is refund`.
- `rule_transaction_kind`, line 170:
  `amount < 0` changed to `amount != 0` and `amount < -1`.

Classification: actionable test gap with some identity-operator noise.

The tests cover transfer using the shared constant, refund using the shared
constant, a large negative amount, and zero. They do not cover dynamically built
strings equal to protected constants, non-transfer category names that sort
after `Transfers`, positive non-refund amounts, or small negative credits.

Recommended follow-up: add transaction-kind cases using dynamically constructed
`Transfers` and `refund` strings, a non-transfer category such as `Zoo`, a
positive non-refund amount, and a small negative amount such as `-0.50`.

## Test-suite weaknesses

The direct rule-engine tests are a good low-cost mutation target, but the
survivors show a few repeatable blind spots:

- Boundary values around zero are underrepresented for income categories,
  direction matching, and transaction-kind classification.
- Identifier comparisons mostly use small IDs, which can make equality and
  identity mutations behave the same.
- Merchant-bound matching does not cover lower-than and higher-than mismatches
  symmetrically in both preview and apply semantics.
- Protected semantic strings are usually passed as module constants, so identity
  mutations can survive unless tests also use equal strings constructed outside
  the constants module.
- Some normalized/defensive branches produce equivalent mutants, especially
  direction-string ordering and the unreachable fallback after
  `rule_direction()` normalization.

## Recommended test improvements

Do not chase every survivor one by one. The highest-value follow-up is a compact
set of parameterized boundary tests:

- Add merchant-bound preview/apply cases with large IDs and both lower/higher
  mismatches.
- Add account constraint cases with large integer and string IDs.
- Add income-category preview/apply cases at `-0.50`, `0.00`, and `0.50`.
- Add direction cases for debit and credit rules at small negative and near-zero
  values.
- Add transaction-kind cases for dynamic `Transfers`/`refund` strings, a
  lexicographically later non-transfer category, positive non-refund spending,
  and small negative income.

After those tests are added, rerun the same Cosmic Ray campaign and compare the
survivor count. A second optional profile can then add
`tests/integration/test_rules_engine.py` for a slower workflow-level mutation
lane, but the first regression target should stay small enough to run locally.

## Implementation issues

No production defect was confirmed by this campaign. The strongest design smell
is the unreachable `return True` fallback in
`rule_direction_matches_transaction()` after direction normalization. It is not
harmful, but it creates equivalent mutants and makes the branch contract less
obvious.

The mutation setup itself exposed one tooling caveat: Cosmic Ray 8.7 invokes
the test command without a shell and records only stdout for failing tests. The
runner helper exists so failures are diagnosable and so Windows runs use the
repo virtualenv reliably.

## Continuation: category mutation targets

This continuation extends the opt-in mutation lane to the four requested
category-focused targets:

- `src/finance_app/modules/categories/categorization.py`
- `src/finance_app/modules/categories/llm_results.py`
- `src/finance_app/modules/merchants/normalization.py`
- `src/finance_app/modules/categories/rules_matching.py`

Additional Cosmic Ray configuration files were added for each target:

- `cosmic-ray-categorization-orchestration.toml`
- `cosmic-ray-llm-results.toml`
- `cosmic-ray-merchant-normalization.toml`
- `cosmic-ray-rule-scoring.toml`

`tools/cosmic_ray_profile_tests.py` is the shared profile runner. It maps each
mutation target to the smallest existing pytest selection that exercises that
target:

- `categorization-orchestration`:
  `tests/integration/test_categorization_workflow.py`
- `llm-results`: `tests/integration/test_llm_categorization.py`
- `merchant-normalization`: `tests/unit/test_merchant_normalization.py`
- `rule-scoring`: `tests/unit/test_category_rules_matching.py`

The default pytest lane remains unchanged. These Cosmic Ray campaigns are
optional local mutation checks.

### Target summary

| Target | Initial result | Final result | Score change |
| --- | ---: | ---: | ---: |
| Categorization orchestration | 158 killed / 80 survived / 238 total | 179 killed / 59 survived / 238 total | 66.39% -> 75.21% |
| LLM result handling | 288 killed / 76 survived / 364 total | 333 killed / 31 survived / 364 total | 79.12% -> 91.48% |
| Merchant normalization | 114 killed / 14 survived / 128 total | 126 killed / 2 survived / 128 total | 89.06% -> 98.44% |
| Rule scoring and precedence | 566 killed / 315 survived / 881 total | 742 killed / 95 survived / 44 incompetent / 881 total | 64.25% -> 88.65% |

### Categorization orchestration

Tests added in `tests/integration/test_categorization_workflow.py` protect:

- Exact medium and high confidence rule boundaries.
- The product distinction between ordinary local categorization and the split
  LLM preparation workflow.
- Amount normalization before categorization.
- Unknown-category audit metadata.
- Historical/rule agreement using value equality rather than object identity.
- Preservation of historical evidence IDs in unresolved metadata.

Remaining survivors are mostly low-value or equivalent:

- Defensive forced-review code in `rule_category_state()` that current callers
  do not reach because medium-confidence rules already enter the review path.
- String equality/identity/order mutants around category names where tests now
  cover the behavioral value-equality cases.
- Cache propagation and category-ID resolution loop mutants where the ordinary
  workflow already exercises the resulting fields and the second pass is an
  optimization.
- Rule-ID parsing exception variants for malformed rule objects outside normal
  database-backed evidence.

No production defect was confirmed. The meaningful regression protection gained
is around confidence boundaries and the LLM fallback split.

### LLM result handling

Tests added in `tests/integration/test_llm_categorization.py` protect:

- Inclusive confidence clamping for valid `0..1` LLM evidence.
- Boolean parsing for provider-style values.
- Review and verify threshold boundaries.
- Medium-confidence disagreement collection.
- Tag-ID parsing with invalid, duplicate, missing, and non-list payloads.
- Forced review for malformed known-category outputs while keeping unknown
  assignments conservative.
- Metadata warnings for taxonomy fallback, invalid tags, dropped tags, and tag
  payload validity.
- Value-equality agreement for rule/retrieval evidence.
- Failure-reason distinctions for invalid category IDs, invalid confidence,
  explicit `UNKNOWN`, and below-review suggestions.
- LLM category normalization to the configured unknown category.
- Cleanup of transient candidate taxonomies.
- Provider error redaction and length limiting.

Remaining survivors are mostly operator noise in equality-heavy metadata paths,
defensive number replacements for unknown fallback metadata, and parser
exception variants whose observable behavior is already covered by the helper
tests and deterministic integration scenarios.

### Merchant normalization

Tests added in `tests/unit/test_merchant_normalization.py` protect:

- Removed-token metadata for card processor, suffix, location, and payment
  processor cleanup.
- Low confidence when a description collapses to an empty merchant key.
- Rule-vs-fallback normalization source metadata when artifacts are removed.

The final two survivors are `@dataclass(frozen=True)` boolean toggles. They do
not change normalization behavior under the current product contract and are
not worth additional tests.

### Rule scoring and precedence

Tests added in `tests/unit/test_category_rules_matching.py` protect:

- Candidate construction and raw-description preservation.
- Exact, containment, fuzzy, and empty-candidate text scoring.
- Prefix full-word and prefix-signal boundaries.
- Income rules requiring credit amounts.
- Merchant-ID scoped rules not falling back to keyword matching.
- Account, direction, and amount filters applying before keyword matching.
- Amount-specificity confidence adjustments.
- Confidence composition for merchant, account, direction, source, income, and
  clamp behavior.
- Rule specificity dimensions.
- Low-level account, direction, amount, and cache-key edge cases.

The remaining 95 survivors are mostly:

- Equality/operator variants in small predicate helpers that are behaviorally
  redundant after the new direct tests.
- Dataclass `frozen=True` and numeric constant replacements.
- Direction and amount comparison variants that do not produce a distinct
  product behavior with the normalized direction vocabulary and inclusive
  amount-boundary tests.
- `continue`/`break` mutants in filtering loops where the same filter
  semantics are now covered, but some later rules are intentionally not
  represented in every preceding rejection scenario.

The final rule-scoring score excludes 44 incompetent mutants generated by
Cosmic Ray from invalid operator replacements. No production defect was
confirmed.

### Verification

Focused tests after the new mutation-killing cases:

```text
150 passed in 22.28s
```

Full local quality gate:

```powershell
.\.venv\Scripts\python.exe -B -m black --check .
.\.venv\Scripts\python.exe -B -m djlint src\finance_app\templates --profile=jinja --lint
.\.venv\Scripts\python.exe -B -m ruff check .
.\.venv\Scripts\python.exe -B -m mypy
npm run lint:frontend
.\.venv\Scripts\python.exe -B -m pytest
```

All passed. The full Python suite result was:

```text
1233 passed in 176.89s (0:02:56)
```
