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

## Continuation: reimbursement mutation target

This continuation applies the same survivor-driven methodology to the
reimbursement allocation service:

- Mutation target:
  `src/finance_app/modules/reimbursements/service.py`
- Cosmic Ray profile: `cosmic-ray-reimbursements.toml`
- Focused profile runner:
  `tools/cosmic_ray_profile_tests.py reimbursements`
- Focused tests:
  `tests/integration/test_reimbursements_service.py`

The scope intentionally targets the service layer instead of the whole
reimbursements package. The service owns write-time reimbursement invariants:
transaction role validation, allocation limits, insert/update/delete
orchestration, completion/resume state changes, and the result totals returned
after allocation writes. Route and presenter tests remain useful confidence
layers, but mutating them in this first reimbursement campaign would add UI and
template noise rather than focusing on the financial rules.

The shared mutation profile runner now passes `-x` to pytest so killed mutants
stop at the first failing focused test. This does not change killed versus
survived semantics; it only keeps on-demand integration-level mutation runs
practical.

### Initial reimbursement result

Commands:

```powershell
.\.venv\Scripts\cosmic-ray.exe baseline cosmic-ray-reimbursements.toml
.\.venv\Scripts\cosmic-ray.exe init --force cosmic-ray-reimbursements.toml runtime\mutation\reimbursements-initial.sqlite
.\.venv\Scripts\cosmic-ray.exe exec cosmic-ray-reimbursements.toml runtime\mutation\reimbursements-initial.sqlite
.\.venv\Scripts\cosmic-ray.exe dump runtime\mutation\reimbursements-initial.sqlite > runtime\mutation\reimbursements-initial.jsonl
.\.venv\Scripts\python.exe tools\cosmic_ray_summary.py runtime\mutation\reimbursements-initial.jsonl
```

| Metric | Count |
| --- | ---: |
| Total mutants | 257 |
| Killed | 223 |
| Survived | 34 |
| Timeout | 0 |
| Incompetent | 0 |
| Error or abnormal worker result | 0 |
| Pending | 0 |
| Mutation score | 86.77% |

### Meaningful reimbursement survivor analysis

Actionable survivors were concentrated in a few high-value reimbursement
boundaries:

- Additive allocation checks survived when `allocated_before + amount` was
  replaced with multiplicative operators. Existing tests used large values
  where both the original and mutant still rejected the allocation. This was a
  missing test for small fractional over-allocation.
- Exact sign and zero boundaries survived for reimbursement credits, expense
  rows, match amounts, and parsed ids. Existing tests covered ordinary positive
  and negative examples but not the exact boundary values.
- Self-match protection survived an equality-to-identity mutation. Existing
  tests did not assert that equal ids are rejected before any database lookup.
- Empty multiple-match helper guards survived because the service-level tests
  did not call the batch helpers directly.
- Legacy category-label fallback survived around `category_id is None` because
  factory-created rows normally resolve category ids.

Non-actionable survivors in the initial run included:

- The private `_save_reimbursement_allocation()` keyword-only separator mutated
  into a positional-only separator. Current internal callers still behave the
  same, and this is not a product contract.
- The `ReimbursementAllocationResult` dataclass `frozen=True` toggle. Result
  immutability is nice, but not part of the reimbursement financial invariant
  under test.
- A transaction-kind comparison changed from `!=` to `>`. All valid
  non-expense transaction-kind enum values currently sort after `"expense"`,
  and the database constraint prevents arbitrary lower-sorting values.
- The `ensure_reimbursable_tag()` missing-built-in fallback. Built-in taxonomy
  metadata is deterministic in normal runtime; this branch is defensive for a
  broken taxonomy registry rather than ordinary reimbursement behavior.

No production defect was confirmed by the initial survivors.

### Survivor-driven reimbursement tests added

Tests added in `tests/integration/test_reimbursements_service.py` protect:

- Exact one-dollar reimbursement, expense, and match amount boundaries.
- Empty, zero, and negative match amounts being rejected as domain errors before
  persistence constraints.
- Invalid reimbursement and expense ids: zero, negative, and non-numeric input.
- Same transaction id rejection before missing-row lookup.
- Fractional over-allocation from one reimbursement to multiple expenses.
- Fractional over-allocation from multiple reimbursements to one expense.
- Zero and positive reimbursement credits being rejected.
- Zero and negative reimbursable expense amounts being rejected.
- Legacy category labels with `category_id` cleared still classifying a
  reimbursement credit while keeping a non-reimbursement expense valid.
- Empty batch submissions for both reimbursement-to-expenses and
  expense-to-reimbursements helpers.

### Final reimbursement result

Commands:

```powershell
.\.venv\Scripts\cosmic-ray.exe baseline cosmic-ray-reimbursements.toml
.\.venv\Scripts\cosmic-ray.exe init --force cosmic-ray-reimbursements.toml runtime\mutation\reimbursements-final.sqlite
.\.venv\Scripts\cosmic-ray.exe exec cosmic-ray-reimbursements.toml runtime\mutation\reimbursements-final.sqlite
.\.venv\Scripts\cosmic-ray.exe dump runtime\mutation\reimbursements-final.sqlite > runtime\mutation\reimbursements-final.jsonl
.\.venv\Scripts\python.exe tools\cosmic_ray_summary.py runtime\mutation\reimbursements-final.jsonl
```

| Metric | Count |
| --- | ---: |
| Total mutants | 257 |
| Killed | 252 |
| Survived | 5 |
| Timeout | 0 |
| Incompetent | 0 |
| Error or abnormal worker result | 0 |
| Pending | 0 |
| Mutation score | 98.05% |

### Reimbursement survivors remaining

The five remaining survivors are non-actionable for this campaign:

- `ReimbursementAllocationResult` dataclass `frozen=True` changed to `False`:
  low-value mutation outside the financial behavior contract.
- `_save_reimbursement_allocation()` keyword-only separator changed to a
  positional-only separator: private call-shape mutation with no observable
  reimbursement behavior difference for current callers.
- `validate_expense_transaction()` transaction-kind `!=` changed to `>`:
  equivalent for the valid transaction-kind vocabulary because all non-expense
  enum values sort after `"expense"`.
- `ensure_reimbursable_tag()` `tag is None` branch inverted or negated:
  defensive fallback for missing built-in taxonomy metadata, not ordinary
  reimbursement allocation behavior.

No meaningful reimbursement survivor remains. No production defect or
specification ambiguity was confirmed.

### Reimbursement verification

Focused reimbursement tests after the survivor-driven additions:

```text
33 passed in 24.52s
```

Full local quality gate after the reimbursement mutation work:

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
1253 passed in 176.52s (0:02:56)
```

## Continuation: transaction import/parsing mutation target

This continuation applies the same survivor-driven methodology to transaction
import and parsing. Mutation testing remains on demand only; no CI/CD or GitHub
Actions integration was added.

The selected targets were split by responsibility so survivor analysis stayed
readable:

- Statement parser:
  `src/finance_app/modules/statements/importer.py`
- Transaction deduplication importer:
  `src/finance_app/modules/transactions/importer.py`
- Import transaction-kind and linked-payment helpers:
  `src/finance_app/modules/upload/transaction_kinds.py`

`src/finance_app/modules/statements/types.py` was inspected but not mutated in
this campaign. Its tests primarily cover statement-type settings persistence
and synchronization, not imported row parsing semantics.

### Transaction import/parsing profiles

Profiles added for this campaign:

- `cosmic-ray-statement-parser.toml`, running
  `tools/cosmic_ray_profile_tests.py statement-parser`
- `cosmic-ray-transaction-importer.toml`, running
  `tools/cosmic_ray_profile_tests.py transaction-importer`
- `cosmic-ray-import-transaction-kinds.toml`, running
  `tools/cosmic_ray_profile_tests.py import-transaction-kinds`
- `cosmic-ray-import-transaction-kinds-unit.toml`, running
  `tools/cosmic_ray_profile_tests.py import-transaction-kinds-unit`

The broader `import-transaction-kinds` focused test command remains useful for
normal verification because it includes account-payment and Interac integration
flows. The final mutation rerun used the unit-only profile because the broader
profile became noisy and stalled with an active date-window mutant after 395 of
414 mutants. The interrupted broad diagnostic run had 302 killed, 93 survived,
and 19 pending mutants; it was not counted as a final result.

### Initial transaction import/parsing results

Commands followed the same pattern for each profile:

```powershell
.\.venv\Scripts\cosmic-ray.exe baseline <profile>.toml
.\.venv\Scripts\cosmic-ray.exe init --force <profile>.toml runtime\mutation\<name>-initial.sqlite
.\.venv\Scripts\cosmic-ray.exe exec <profile>.toml runtime\mutation\<name>-initial.sqlite
.\.venv\Scripts\cosmic-ray.exe dump runtime\mutation\<name>-initial.sqlite > runtime\mutation\<name>-initial.jsonl
.\.venv\Scripts\python.exe tools\cosmic_ray_summary.py runtime\mutation\<name>-initial.jsonl
```

| Target | Total mutants | Killed | Survived | Incompetent/error | Raw mutation score |
| --- | ---: | ---: | ---: | ---: | ---: |
| Statement parser | 819 | 632 | 187 | 0 | 77.17% |
| Transaction importer | 45 | 39 | 6 | 0 | 86.67% |
| Import transaction kinds, broad initial profile | 414 | 255 | 159 | 0 | 61.59% |

### Meaningful transaction import/parsing survivor analysis

Actionable survivors were concentrated in these areas:

- Duplicate source-identity filtering: a `continue` to `break` mutation allowed
  one duplicate parsed row to stop later fresh rows from importing.
- Statement parser date and amount boundaries: explicit date-order priority,
  zero values, signed debit/credit handling, bank-account sign normalization,
  Interac required fields, and ignored-row counts were underasserted.
- Import-kind inference: default Interac enrichment mode, credit-card payment
  text, linked-account source filtering, four-character account tokens, negative
  linked-transfer descriptions, and exact zero/positive/negative boundaries
  needed direct coverage.
- Linked-payment matching: existing tests did not assert enough of the amount,
  date, ignored-row, already-payment, account-id, and update-only-the-matched-row
  invariants.
- Undo-state capture: duplicate update snapshots needed a direct test to ensure
  each transaction id is recorded once.
- Transaction source identity: provider transaction ids needed direct coverage
  as stable fingerprint inputs without falling back to parsed row indexes.

Non-actionable or low-value survivor groups included:

- SQLAlchemy comparison variants that compile to equivalent behavior for the
  current constrained enum/string values or for SQLite's query result in these
  narrowly scoped helper tests.
- Identity-comparison mutations for small integer ids, where behavior is an
  implementation artifact of Python object identity rather than a product
  contract.
- Two-decimal money tolerance variants where the database stores amounts at
  cents precision and sub-cent distinctions are not observable through normal
  persisted rows.
- Parser bookkeeping count mutations for malformed/padded rows after the
  transaction list and source-row semantics are already covered.
- File checksum, extension, and allowed-file helper mutations, which are
  low-value for this parsing/import semantics campaign.
- Delimiter/header heuristic variants that did not change the parsed
  transactions for representative inputs and would be better handled by a
  dedicated parser fixture set if those heuristics become product-critical.

No production defect was confirmed. One specification note remains: linked
credit-account matching is intentionally token-based and therefore matches broad
tokens such as `"Mastercard"`; tests were written to preserve the current fuzzy
rule rather than tightening it accidentally.

### Survivor-driven transaction import/parsing tests added

Tests added in `tests/unit/test_statement_importer.py` protect:

- Date-format priority for explicit month-first/day-first choices.
- Auto date-order analysis for inputs with no slash-style numeric dates.
- Zero, positive, negative, debit, credit, and bank-account sign boundaries.
- CSV source-row offsets after a one-line preamble.
- Interac required date/name/amount fields and one-cent boundary.
- Interac ignored-row counts for unusable exports.
- Provider transaction ids as stable fingerprint source identities.

Tests added in `tests/integration/test_transaction_importer.py` protect:

- Duplicate source identities being skipped without stopping later fresh rows in
  the same import batch.

Tests added in `tests/unit/test_upload_transaction_kinds.py` protect:

- Interac-only default enrichment mode, including dynamic string equality.
- Payment/transfer category metadata and review state.
- Account-role, amount-sign, linked-description, and payment-text
  classification boundaries.
- Linked credit-account matching by paid-from account, account type, normalized
  description, and four-character account tokens.
- Nearest-payment matching and ambiguity handling.
- Inclusive date windows and invalid date rejection.
- Linked-account payment matching filters for amount, date, ignored rows,
  existing payment rows, source account, and description.
- Updating only the matched funding row, not neighboring transaction ids.
- Missing account handling.
- Undo snapshot deduplication by transaction id.

### Final transaction import/parsing results

Final commands used the same baseline/init/exec/dump/summary pattern as the
initial campaign. The final transaction-kind score below is from the unit-only
mutation profile; the broader integration profile is retained for normal
focused verification.

| Target | Total mutants | Killed | Survived | Incompetent/error | Raw mutation score | Meaningful survivors remaining |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Statement parser | 819 | 667 | 152 | 0 | 81.44% | 0 |
| Transaction importer | 45 | 42 | 3 | 0 | 93.33% | 0 |
| Import transaction kinds, unit profile | 414 | 363 | 51 | 0 | 87.68% | 0 |

The completed statement-parser final run above predates the last
provider-source-identity assertion. A rerun was attempted, but Cosmic Ray stalled
after 3 mutants on a non-behavioral annotation mutation
(`object | None` changed to `object % None`). The source file was restored and
the completed final campaign is kept as the official statement-parser mutation
result.

Narrow manual fault-injection confirmation was performed for the added
provider-source-identity behavior. The focused test
`tests/unit/test_statement_importer.py::test_transaction_fingerprint_prefers_provider_source_identity`
passed against unmodified production code. Temporarily removing
`provider_transaction_id` from the `transaction_source_identity()` precedence
tuple made the test fail because two rows with the same provider id but
different fallback source fields produced different fingerprints. Restoring the
tuple made the test pass again. This confirms the intended mutant would be
killed without rerunning the full statement-parser campaign.

### Transaction import/parsing survivors remaining

The remaining transaction-importer survivors are non-actionable:

- The empty-chunk `continue` in `get_existing_transaction_fingerprints()` is
  unreachable for `range(0, len(values), positive_chunk_size)`.
- The two `chunk_size = 900` number replacements preserve observable behavior;
  chunk size affects batching performance, not deduplication correctness.

The remaining statement-parser survivors are non-actionable for this campaign:

- Row-count arithmetic mutants in malformed Interac/CSV bookkeeping do not
  change imported transaction values for the covered parsing cases.
- Date-order heuristic threshold mutations are low value after explicit tests
  for detected month-first/day-first, ambiguous, and no-choice paths.
- File checksum, file-extension, and allowed-file mutations are outside the
  transaction-value parsing risk area.
- Header/delimiter helper mutations that preserve parsed transactions are
  equivalent for the representative fixtures used here.

The remaining import transaction-kind survivors are non-actionable:

- SQLAlchemy comparison and boolean-expression mutations that preserve the
  result under the constrained account/type/kind vocabulary used by these
  helpers.
- Identity-comparison mutants for small integer ids and enum-like strings.
- Number replacements around default `0`, confidence `1.0`, and token-length
  thresholds where added tests cover the meaningful boundary and remaining
  variants do not change product behavior.
- Sub-cent amount-tolerance variants that are not observable with persisted
  two-decimal transaction amounts.

No production defect was confirmed. No import-rule ambiguity required a
production change.

### Transaction import/parsing verification

Focused import/parsing tests after survivor-driven additions:

```text
statement-parser: 53 passed in 0.47s
transaction-importer: 11 passed in 7.86s
import-transaction-kinds: 26 passed in 14.38s
```

Full local quality gate after the transaction import/parsing mutation work:

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
1289 passed in 186.52s (0:03:06)
```

## Continuation: financial/report calculations mutation target

This continuation applies the same survivor-driven methodology to shared
financial calculation helpers. Mutation testing remains on demand only; no
CI/CD or GitHub Actions integration was added.

The selected targets were split by responsibility so SQL reporting predicates,
pure dashboard/report helper calculations, and comparison statistics could be
analyzed separately:

- Financial reporting predicates and expressions:
  `src/finance_app/core/reporting.py`
- Analytics summary helpers:
  `src/finance_app/core/analytics.py`
- Comparison statistics:
  `src/finance_app/modules/comparison/statistics.py`

Controllers, templates, URL builders, and presentation-only formatting were not
mutated for this campaign.

### Financial/report calculation profiles

Profiles added for this campaign:

- `cosmic-ray-financial-reporting.toml`, running
  `tools/cosmic_ray_profile_tests.py financial-reporting`
- `cosmic-ray-analytics-summary.toml`, running
  `tools/cosmic_ray_profile_tests.py analytics-summary`
- `cosmic-ray-comparison-statistics.toml`, running
  `tools/cosmic_ray_profile_tests.py comparison-statistics`

Initial reporting and analytics runs used existing focused integration checks,
including `tests/integration/test_financial_correctness.py`, selected dashboard
context tests, and selected reports overview tests. Final profiles use the
survivor-driven direct tests added in this campaign so the on-demand mutation
lanes remain compact.

### Initial financial/report calculation results

Commands followed the established pattern for each profile:

```powershell
.\.venv\Scripts\cosmic-ray.exe baseline <profile>.toml
.\.venv\Scripts\cosmic-ray.exe init --force <profile>.toml runtime\mutation\<name>-initial.sqlite
.\.venv\Scripts\cosmic-ray.exe exec <profile>.toml runtime\mutation\<name>-initial.sqlite
.\.venv\Scripts\cosmic-ray.exe dump runtime\mutation\<name>-initial.sqlite > runtime\mutation\<name>-initial.jsonl
.\.venv\Scripts\python.exe tools\cosmic_ray_summary.py runtime\mutation\<name>-initial.jsonl
```

| Target | Total mutants | Killed | Survived | Incompetent/error | Raw mutation score |
| --- | ---: | ---: | ---: | ---: | ---: |
| Financial reporting | 141 | 80 | 61 | 0 | 56.74% |
| Analytics summary | 249 | 123 | 126 | 0 | 49.40% |
| Comparison statistics | 442 | 404 | 24 | 14 | 94.39% |

### Meaningful financial/report survivor analysis

Actionable survivors were concentrated in these areas:

- Analytics helper branches: cash-flow status/rate arithmetic, quick-view active
  matching, quality risk thresholds, unknown-review copy, review CTA
  pluralization, and secondary data-quality counters were only indirectly
  asserted through integration tests.
- Reporting predicates and expressions: transfer-credit inclusion, zero/sign
  boundaries, reimbursement credit exclusion, income/cash-flow sign handling,
  and reimbursement allocation subtraction needed compact expression-level
  coverage.
- Comparison statistics: non-unit MAD scaling, exact anomaly threshold
  inclusivity, zero-MAD negative differences, non-divisible sample standard
  deviation, and inclusive percentile endpoints needed direct numeric boundary
  assertions.

Non-actionable survivor groups included:

- SQLAlchemy/string-enum comparison variants such as replacing equality with
  broad ordering comparisons. Under the persisted transaction-kind vocabulary
  and sign conventions, these do not change ordinary report behavior.
- Number replacements around zero boundaries where the mutated branch still
  returns an amount of `0` and the visible financial result is unchanged.
- Analytics fallback/default-value mutations for counters that are constrained
  to nonnegative query results or are secondary metadata rather than financial
  arithmetic.
- Comparison helper mutations for impossible negative history counts,
  single-value percentile index aliases, and the keyword-only marker location.

One stale fallback was discovered in `transaction_has_builtin_category_clause()`:
the fallback branch appeared to recognize built-in categories when
`transactions.category_id` is `NULL`, but category identity is intentionally
canonical through `category_id`. The cached transaction category label is
display/import metadata, not taxonomy identity. A follow-up removed that dead
fallback and added a regression case verifying that a legacy
`category = "Reimbursement"` row with `category_id = NULL` is not treated as a
built-in reimbursement credit.

### Survivor-driven financial/report tests added

Tests added in `tests/unit/test_analytics_helpers.py` protect:

- Quick-view option filtering and active matching, including dynamic request
  strings.
- Cash-flow surplus, balanced, deficit, zero-income, savings-rate, and
  spending-rate calculations.
- Data-quality empty, danger, warning, and good levels, including exact and
  above-threshold risk boundaries.
- Unknown-review sentence boundaries, review CTA pluralization, driver warning
  visibility, untagged spending details, source counters, fallback count
  fields, and percentage rounding.

Tests added in `tests/integration/test_reporting_expressions.py` protect:

- Reportable, income, reimbursement-credit, spending-impact, and
  transfer-credit predicate boundaries for expense, refund, income,
  reimbursement, and transfer rows.
- Canonical category semantics: cached category text without `category_id` does
  not confer built-in reimbursement behavior.
- Zero-value boundaries for expenses, refunds, reimbursement credits, and
  transfers.
- Income and cash-flow sign conventions.
- Reimbursement allocation subtraction from original expense spending and net
  cash-flow impact.

Tests added in `tests/unit/test_comparison_statistics.py` protect:

- Non-divisible sample standard deviation.
- Robust z-score scaling when MAD is not `1`.
- Exact anomaly-threshold inclusivity.
- Zero-MAD negative differences.
- Inclusive percentile lower and upper endpoints.

### Final financial/report calculation results

Final commands used the same baseline/init/exec/dump/summary pattern. The final
analytics and reporting profiles use the direct survivor-driven tests described
above.

| Target | Total mutants | Killed | Survived | Incompetent/error | Raw mutation score | Meaningful test-gap survivors remaining |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Financial reporting | 141 | 116 | 25 | 0 | 82.27% | 0 |
| Analytics summary | 249 | 206 | 43 | 0 | 82.73% | 0 |
| Comparison statistics | 442 | 415 | 11 | 16 | 97.42% | 0 |

### Financial/report calculation survivors remaining

The remaining financial-reporting survivors are non-actionable for this
campaign. The legacy category-label fallback noted during analysis was resolved
after the final campaign by removing the dead branch and pinning canonical
category-id behavior:

- Transaction-kind comparison mutations that broaden enum equality to ordering
  comparisons are low value under the constrained persisted kind vocabulary and
  normal sign conventions.
- Some zero-boundary mutations in amount expressions preserve the observable
  amount because both original and mutant produce `0`.

The remaining analytics-summary survivors are non-actionable:

- Nonnegative-count guards such as `transaction_count == 0` mutated to
  `<= 0`, and `count > 0` mutated to `!= 0`, are equivalent under query
  constraints.
- `total_income > 0` mutated to `!= 0` would only differ for negative income
  totals, which are outside the report summary domain.
- Several `or 0` and numeric replacement survivors affect defensive defaults
  for nullable counters or duplicate equivalent fallback values after direct
  assertions cover the visible helper contract.

The remaining comparison-statistics survivors are non-actionable:

- Impossible negative history-count and negative-length percentile branches.
- Single-value percentile index aliases such as `0` versus `-1`.
- Unreachable anomaly-direction `>= 0` behavior after the explicit
  zero-difference branch.
- A low-value mutation reported at the keyword-only marker line.

No financial arithmetic defect was fixed in this campaign. The only discovered
specification ambiguity was the unreachable legacy category-label fallback; the
follow-up implementation made the canonical `category_id` behavior explicit.

### Financial/report calculation verification

Focused financial/report tests after survivor-driven additions:

```text
analytics-summary: 21 passed in 0.14s
financial-reporting: 14 passed in 8.25s
comparison-statistics: 18 passed in 0.37s
```

## Review/manual recategorization

### Mutation target and focused tests

The review/manual recategorization campaign targeted:

- `src/finance_app/modules/review/workflow.py`

The focused test profile was:

- `tests/integration/test_review_workflow.py`

The profile was added as `cosmic-ray-review-workflow.toml` and uses the shared
`tools/cosmic_ray_profile_tests.py` runner under the `review-workflow` profile.
No controllers, templates, presentation-only code, or background-runner
infrastructure were mutated.

### Initial review/manual recategorization result

The initial campaign was run before modifying tests:

```text
cosmic-ray baseline cosmic-ray-review-workflow.toml
cosmic-ray init --force cosmic-ray-review-workflow.toml runtime\mutation\review-workflow-initial.sqlite
cosmic-ray exec cosmic-ray-review-workflow.toml runtime\mutation\review-workflow-initial.sqlite
cosmic-ray dump runtime\mutation\review-workflow-initial.sqlite > runtime\mutation\review-workflow-initial.jsonl
python tools\cosmic_ray_summary.py runtime\mutation\review-workflow-initial.jsonl
```

Initial result:

| Total mutants | Killed | Survived | Incompetent/error | Raw mutation score |
| ---: | ---: | ---: | ---: | ---: |
| 386 | 291 | 95 | 0 | 75.39% |

### Meaningful review/manual recategorization survivor analysis

Meaningful initial survivor groups included:

- Selected transaction ID parsing skipped `None`, malformed, nonpositive, and
  duplicate values without proving later valid IDs were preserved.
- Transaction-kind inference lacked compact direct boundaries for transfer
  categories, refund preservation, negative income, zero/positive expense, and
  `None` amounts.
- Review undo guard predicates needed stronger assertions that changed
  category, source, metadata, reviewed timestamp, and matching transaction ID
  prevent unsafe restoration.
- Legacy undo snapshots without old category IDs needed a direct assertion that
  undo resolves the canonical category foreign key.
- Rule undo needed protection for rules already removed or changed after
  processing, legacy previous-rule snapshots without `category_id` or
  `ai_approved`, and neighboring rules that must not be deleted or updated.
- Rule snapshot comparison needed value-based equality and changed-field
  assertions across every persisted decision field, including tags.
- Review job summaries had weak pluralization coverage for one transaction.

The large survivor cluster around the no-op skip inside
`apply_review_group_transactions()` is non-actionable for the current workflow:
`review_group_rows()` is fed by `review_candidate_rows()`, which only returns
rows that need review or resolve to the configured unknown category through
`category_id`. A row that already has the target category, `needs_review = 0`,
and identical tags is excluded before the in-loop no-op check is reached. This
was later confirmed as stale defensive code and removed in the follow-up review.

### Survivor-driven review/manual recategorization tests added

Tests added in `tests/integration/test_review_workflow.py` protect:

- Completed review rows being excluded while pending candidates in the same
  group are still updated.
- Selected review transaction ID filtering across malformed values, duplicates,
  nonpositive IDs, and later valid IDs.
- Reviewed transaction kind inference for transfers, refunds, negative income,
  zero, positive, and missing amounts.
- Singular review job messaging for one updated transaction.
- Undo of legacy transaction snapshots where the old `category_id` is missing.
- Undo guard exactness for changed category, review state, category source,
  category metadata, reviewed timestamp, and transaction ID.
- Created-rule undo deleting only the created rule while preserving neighboring
  rules.
- Rule undo behavior when the rule was removed or changed after processing.
- Previous-rule restoration from legacy snapshots missing `category_id` and
  `ai_approved`.
- Rule snapshot equality by value rather than identity, and mismatch detection
  for all persisted rule decision fields and tags.

### Final review/manual recategorization result

The final campaign used the same baseline/init/exec/dump/summary pattern:

```text
cosmic-ray baseline cosmic-ray-review-workflow.toml
cosmic-ray init --force cosmic-ray-review-workflow.toml runtime\mutation\review-workflow-final3.sqlite
cosmic-ray exec cosmic-ray-review-workflow.toml runtime\mutation\review-workflow-final3.sqlite
cosmic-ray dump runtime\mutation\review-workflow-final3.sqlite > runtime\mutation\review-workflow-final3.jsonl
python tools\cosmic_ray_summary.py runtime\mutation\review-workflow-final3.jsonl
```

Final result:

| Total mutants | Killed | Survived | Incompetent/error | Raw mutation score | Meaningful test-gap survivors remaining |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 386 | 358 | 28 | 0 | 92.75% | 0 |

### Review/manual recategorization survivors remaining

The final campaign was run before the stale no-op branch follow-up. At that
point, the remaining 28 survivors were non-actionable for this campaign:

- Twenty-four survivors are the stale in-loop no-op skip branch in
  `apply_review_group_transactions()`. A follow-up inspection confirmed the
  review candidate query excludes the fully reviewed target-category row shape
  needed to reach that branch, and the branch was removed.
- Two survivors are low-value pluralization variants in background-job summary
  strings and do not affect durable review, rule, tag, category, or undo state.
- One survivor changes `needs_review == 0` to `needs_review <= 0` in an undo
  guard. The database constrains `needs_review` to `0` or `1`, making the mutant
  equivalent for persisted rows.
- One survivor is the related zero/one message-count comparison and is also
  presentation-only.

No production defect was fixed in this campaign. The only stale-code finding was
the unreachable in-loop no-op skip described above; it was removed in a
follow-up focused cleanup after the mutation campaign.

### Review/manual recategorization verification

Focused review workflow tests after survivor-driven additions:

```text
45 passed in 12.96s
```
