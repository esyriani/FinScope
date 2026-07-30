# FinScope Database Review

Independent database review of FinScope focused on schema design, database
configuration, SQLAlchemy Core usage, data-access code, queries, constraints,
indexes, transaction handling, import deduplication, reimbursements, reports,
prompt evaluation boundaries, and database-related tests.

No application files were modified as part of this review.

## Findings

### 1. Coarse transaction fingerprints can silently drop legitimate repeated transactions

**Severity:** CRITICAL

**Location:** `src/finance_app/modules/statements/importer.py:601` (`transaction_fingerprint`), `src/finance_app/modules/transactions/importer.py:24` (`filter_new_transactions`), `src/finance_app/modules/upload/workflow.py:166`, `src/finance_app/database/tables.py:393` (`uq_transactions_fingerprint`), `tests/integration/test_transaction_importer.py:99`

**Evidence:** `transaction_fingerprint()` hashes only `account_id`, `tx_date`, `description`, and `amount`. `filter_new_transactions()` skips any in-batch row with the same fingerprint and any row whose fingerprint already exists in the database. The `transactions` table enforces global uniqueness on `fingerprint`. The tests explicitly encode this boundary: same account/date/description/amount is expected to be skipped, while changes to amount/account/description are expected to import.

**Impact:** Two real transactions from the same account on the same day with the same statement description and amount are indistinguishable and one will be skipped. This is realistic for repeated transit, parking, coffee, subscriptions, transfers, ATM fees, or split purchases. The result is missing ledger rows and materially understated spending, income, reimbursements, recurring detection, and reports. Because skipped rows are only counted, the user cannot reliably recover which transaction was dropped.

**Recommendation:** Separate import replay deduplication from real transaction identity. Prefer provider transaction IDs when available; otherwise include a stable statement-row occurrence or statement-scoped sequence in the persisted import identity, and treat same-day same-amount rows as potential duplicates for review rather than silently suppressing them. Update the unique constraint and tests to preserve legitimate repeated rows while still making re-imports idempotent.

### 2. Home financial totals bypass the shared refund and reimbursement reporting logic

**Severity:** CRITICAL

**Location:** `src/finance_app/modules/home/service.py:275` (`fetch_home_overview`), `src/finance_app/modules/home/service.py:437` (`fetch_top_categories`), `src/finance_app/core/reporting.py:25`, `src/finance_app/modules/dashboard/queries.py:65`, `src/finance_app/modules/reports/queries.py:135`, `src/finance_app/modules/comparison/queries.py:28`, `tests/integration/test_home_context_services.py:10`, `tests/integration/test_financial_correctness.py:173`

**Evidence:** Home computes year-to-date spending with `transactions.amount > 0` and `transaction_kind == "expense"`, income with `transactions.amount < 0` and `transaction_kind == "income"`, and top categories with `sum(transactions.amount)` over positive expense rows. It does not use `reportable_transaction_clause()`, `spending_impact_clause()`, `spending_impact_amount_expression()`, or `cashflow_amount_expression()`. Dashboard, Reports, and Comparison do use those shared expressions, and financial correctness tests cover refunds and reimbursements there. The Home tests assert only seeded happy-path values and do not include refund or reimbursement allocation cases.

**Impact:** The first-screen Home cash-flow numbers can disagree with the rest of the application. Refunds do not reduce Home spending. Reimbursement credits can be counted as income, while the reimbursed expense is not reduced by allocations. For example, a 1000.00 reimbursable expense with a 900.00 matched reimbursement should report 100.00 spending and 0.00 ordinary income, but Home's current query shape can show 1000.00 spending and 900.00 income. This is materially incorrect financial reporting in the user's primary overview.

**Recommendation:** Reuse the shared reporting expressions in Home overview and top-category queries, including the same reimbursement-credit exclusion and allocation offsets used by Dashboard, Reports, and Comparison. Add Home financial correctness tests for refunds, reimbursement credits, matched allocations, non-reportable payments/transfers, and top-category reimbursement offsets.

### 3. Reimbursement allocation limits are not atomically enforced

**Severity:** CRITICAL

**Location:** `src/finance_app/database/tables.py:470` (`reimbursement_allocations`), `src/finance_app/modules/reimbursements/service.py:273` (`_save_reimbursement_allocation`), `src/finance_app/modules/reimbursements/repository.py:125` (`sum_allocations`), `src/finance_app/modules/reimbursements/repository.py:135` (`insert_allocation`), `src/finance_app/core/reporting.py:73` (`reimbursed_expense_allocation_amount`), `tests/integration/test_reimbursements_service.py:145`, `tests/integration/test_database_concurrency.py:22`

**Evidence:** The table enforces positive allocation amounts, distinct reimbursement/expense transaction IDs, and uniqueness of one reimbursement/expense pair. It does not enforce that total allocations for a reimbursement stay within the reimbursement credit, or that total allocations for an expense stay within the expense amount. `_save_reimbursement_allocation()` reads current totals, compares them with the requested amount, and then inserts or updates. The tests cover sequential over-allocation rejection, while the concurrency tests cover merchant and taxonomy unique-conflict fallbacks only.

**Impact:** Concurrent requests can each read the same pre-allocation total, each pass validation, and together over-allocate the same reimbursement credit or expense. Reporting subtracts the sum of allocations from expense rows, so over-allocation can produce negative spending, incorrect remaining balances, and inconsistent reimbursement tracking. This is a financial-integrity invariant and should not depend only on non-atomic service reads.

**Recommendation:** Make allocation-limit enforcement atomic for the involved reimbursement and expense rows. Suitable directions include serializing writes for the affected transactions, using row locks or conditional writes where the backend supports them, maintaining guarded allocated totals, or introducing another enforceable invariant boundary. Add concurrency tests that attempt simultaneous allocations against the same reimbursement and same expense.

### 4. Existing database validation does not verify constraints, indexes, types, or foreign keys

**Severity:** IMPORTANT

**Location:** `src/finance_app/database/connection.py:62` (`validate_core_schema`), `src/finance_app/database/connection.py:37` (`init_core_db`), `tests/integration/test_db_schema.py:215`, `tests/integration/test_sqlalchemy_tables.py:285`

**Evidence:** `validate_core_schema()` checks for expected table names, expected column names, and retired table names. It does not compare column types, nullability, defaults, generated columns, check constraints, unique constraints, foreign keys, foreign-key delete behavior, indexes, or primary-key details. The current schema tests verify metadata and a few legacy rejection cases, but they do not prove that startup rejects an existing database missing critical constraints such as `uq_transactions_fingerprint`, reimbursement allocation constraints, generated uniqueness keys, or foreign-key cascades.

**Impact:** An existing database can pass startup validation while missing the exact invariants FinScope relies on for deduplication, owner uniqueness, statement checksum uniqueness, reimbursement cleanup, enum-like state checks, and query performance. That undercuts the stated current-schema model: existing databases are treated as valid even if they are only column-compatible.

**Recommendation:** Extend current-schema validation to inspect and compare the constraints, foreign keys, generated columns, nullability, key indexes, and important column types that protect application invariants. Add negative tests that create column-compatible but constraint-deficient schemas and verify startup rejects them.

### 5. User-visible unique names are not normalized consistently at the database boundary

**Severity:** IMPORTANT

**Location:** `src/finance_app/database/tables.py:222` (`accounts.name`), `src/finance_app/database/tables.py:238` (`statement_types.name`), `src/finance_app/database/tables.py:256` (`categories.name`), `src/finance_app/database/tables.py:452` (`tags.name`), `src/finance_app/modules/accounts/repository.py:30` (`get_or_create_account`), `src/finance_app/modules/categories/taxonomy.py:218` and `:277`, `src/finance_app/modules/categories/repository.py:328`, `src/finance_app/modules/settings/runtime.py:209`

**Evidence:** Accounts, statement types, categories, and tags have exact `UniqueConstraint("name")` definitions. Repository lookups are mostly exact-name comparisons after trimming. Some service/form paths perform casefold checks for the submitted batch, such as statement type sync, and `resolve_category_id()` performs a casefold scan only after an exact lookup. There is no normalized generated key comparable to `users.username_key` for these names.

**Impact:** SQLite can allow labels such as `Visa` and `visa`, or `Food` and `food`, while MySQL behavior can vary with collation and may reject or match differently. Duplicate category/tag/account names split reports, filters, rules, and account-scoped imports. Ambiguous casefold resolution also means the first row returned by a full category scan can determine which category ID gets assigned.

**Recommendation:** Define the intended uniqueness semantics once at the schema boundary, such as normalized generated keys for lower/trimmed labels or explicit backend-portable collations. Update repositories to select by the same normalized key and add cross-backend-oriented tests for categories, tags, accounts, and statement types.

### 6. Statement import state transitions are not claimed atomically

**Severity:** IMPORTANT

**Location:** `src/finance_app/modules/upload/controller.py:326` (`queue_existing_statement_import`), `src/finance_app/modules/upload/controller.py:91` and `:202` (checksum pre-checks), `src/finance_app/modules/upload/workflow.py:494` (`import_statement_transactions_job`), `src/finance_app/modules/upload/workflow.py:598` (`reset_statement_import_state`), `src/finance_app/modules/upload/workflow.py:618` (`update_statement_import_state`), `src/finance_app/database/tables.py:348` (`statements.import_status`), `src/finance_app/background/runner.py:16`

**Evidence:** Controllers check `statement.import_status` for active statuses, then reset the row to `queued` and submit a background job. The job later unconditionally updates the same statement to `running` and then `completed` or `failed`. `update_statement_import_state()` performs `UPDATE statements SET import_status = ... WHERE id = ...` with no expected previous status, job token, row-count check, or compare-and-set. The in-memory background runner uses one worker per queue in this process, but the database state itself does not prevent duplicate claims. Duplicate upload detection similarly pre-checks `checksum` before insert and relies on the unique constraint if a concurrent insert races, without handling that integrity error for a controlled user outcome.

**Impact:** Simultaneous retry/reprocess/upload submissions can enqueue duplicate work or surface raw integrity failures. Serial duplicate jobs can still rerun import logic and overwrite statement counters/status. Reprocess is especially sensitive because it deletes existing statement transactions before queueing new work; without an atomic claim, stale or duplicate jobs can make statement status and ledger contents hard to reason about. The current process-local single worker reduces one concurrency shape but does not make the database transitions idempotent.

**Recommendation:** Treat import execution as a claimable database state transition. Use conditional updates with expected status and row-count checks, store a job token or import attempt ID, and have workers no-op when they cannot claim the statement. Handle checksum unique conflicts at insert time as duplicate uploads. Add tests for simultaneous retry/reprocess and concurrent duplicate uploads.

### 7. Monetary values enter import, rules, and recurring flows as floats before Numeric storage

**Severity:** IMPORTANT

**Location:** `src/finance_app/modules/statements/importer.py:208` (`parse_money`), `src/finance_app/modules/statements/importer.py:369`, `src/finance_app/modules/statements/importer.py:601`, `src/finance_app/modules/rules/forms.py:18`, `src/finance_app/modules/categories/rules_matching.py:126` and `:359`, `src/finance_app/modules/recurring/patterns.py:255`, `src/finance_app/database/tables.py:82`, `src/finance_app/core/money.py:22`

**Evidence:** The schema uses `Numeric(14, 2)` for money columns, and `core.money` provides Decimal-based rounding with `ROUND_HALF_UP`. However, statement parsing converts money text to `float`, import rows are rounded with Python `round()`, rule amount bounds are parsed with `float`, recurring pattern amounts/tolerances are normalized as floats, and rule matching repeatedly converts money to floats for comparisons. Fingerprints include the string representation of the float amount.

**Impact:** Binary floating-point and Python's rounding behavior can produce cent-level surprises at ingestion and rule-boundary decisions. A value such as `2.675` can round differently than Decimal half-up money handling. Since the fingerprint includes the parsed amount string, non-canonical float formatting can also affect deduplication identity. The database stores fixed-scale values later, but important decisions have already been made before that boundary.

**Recommendation:** Parse and normalize money as `Decimal` at all ingestion and rule/recurring persistence boundaries, quantize with the existing money helpers, and build fingerprints from canonical fixed-scale strings. Limit float conversion to presentation and JSON serialization.

### 8. Category text caches remain active query inputs alongside category IDs

**Severity:** IMPORTANT

**Location:** `src/finance_app/database/tables.py:280` and `:282` (`category_rules.category`/`category_id`), `src/finance_app/database/tables.py:380` and `:381` (`transactions.category`/`category_id`), `src/finance_app/modules/reports/queries.py:83` (`category_label_expression`), `src/finance_app/modules/reports/queries.py:89` (`category_lookup_join_condition`), `src/finance_app/modules/dashboard/queries.py:233`, `src/finance_app/modules/categories/repository.py:357`, `src/finance_app/modules/taxonomy_admin/service.py:618`

**Evidence:** Transactions and rules store both a category text label and a nullable `category_id`. Reports and dashboard previews use `coalesce(transactions.category, unknown)` and only fall back to category-table joins when `category_id` is null. The report join function explicitly refers to "legacy cached labels." Rename and delete paths manually update or inspect both the ID and text fields to keep caches aligned. The database has no constraint or generated relationship ensuring `transactions.category` matches `categories.name` when `category_id` is set.

**Impact:** A stale cached category label can drive report grouping, filters, built-in reimbursement semantics, and taxonomy usage checks even when the foreign key points elsewhere. This duplicates category state across tables and makes category correctness dependent on every write path updating both fields. It also makes case-variant category duplicates more dangerous because text matching and ID matching can disagree.

**Recommendation:** Make one representation canonical. Prefer using `category_id` for report semantics and deriving display names through joins, or explicitly define the text field as a constrained/generated cache with a single synchronization boundary. Add drift tests that intentionally create mismatched category text and ID values and verify the chosen canonical behavior.

### 9. Database schema documentation and generated diagrams are stale

**Severity:** IMPORTANT

**Location:** `docs/database.md:141`, `docs/database.md:148`, `docs/database.md:284`, `docs/diagrams/db-schema.dbs:202`, `docs/db-schema.html:900`, `src/finance_app/database/tables.py:263`, `tests/integration/test_db_schema.py:247`, `tests/integration/test_merchant_repository.py:27`

**Evidence:** The current schema defines `merchants` with `id`, `merchant_key`, `created_at`, and `updated_at`, and tests assert that `merchant_aliases` and old `canonical_key` fields are absent. The database documentation still describes merchant columns such as `canonical_key`, `system_name`, and `display_name_source`, plus a `merchant_aliases` table. The generated `.dbs` and HTML schema artifacts still include the removed table, columns, indexes, constraints, and foreign keys.

**Impact:** The source-of-truth schema, tests, and database documentation disagree. This misleads reviewers and maintainers about current relationships, delete behavior, indexing, and merchant identity responsibilities. It also weakens confidence that generated schema artifacts are kept current when the metadata changes.

**Recommendation:** Regenerate the schema documentation from current SQLAlchemy metadata and update the narrative merchant section. Consider a lightweight check that generated schema artifacts do not mention retired tables or columns.

### 10. Merchant breakdowns and transaction pagination fetch more rows than necessary

**Severity:** NICE TO HAVE

**Location:** `src/finance_app/modules/dashboard/queries.py:363` (`fetch_spending_merchant_totals`), `src/finance_app/modules/reports/queries.py:579` (`fetch_merchant_breakdown`), `src/finance_app/modules/transactions/queries.py:78` (`fetch_transaction_ids`)

**Evidence:** Dashboard and Reports merchant breakdown queries fetch every matching transaction row, group in Python via `merchant_identity_from_row()`, sort in Python, and then callers slice for top merchants. Transaction list pagination fetches all matching transaction IDs in sorted order for the current filter set. These query shapes bypass database grouping and limiting for common report and navigation paths.

**Impact:** A local finance database can grow quickly across years and accounts. These paths make response time and memory usage scale with all matching transactions rather than with the number of groups or page rows needed. The existing merchant indexes are less useful when aggregation and top-N selection happen after fetching all rows.

**Recommendation:** Push merchant grouping, ordering, and limiting into SQL where possible, using `merchant_id` and a deterministic fallback key for rows without a merchant. For transaction navigation, consider a narrower query, keyset navigation, or fetching only nearby IDs instead of the full filtered set.

### 11. Removed merchant-schema compatibility remains in the repository API

**Severity:** NICE TO HAVE

**Location:** `src/finance_app/modules/merchants/repository.py:44` (`get_or_create_merchant`)

**Evidence:** `get_or_create_merchant()` accepts `**_ignored_legacy_fields` and documents that extra keyword parameters are accepted for older callers but ignored because merchant identity now uses only a deterministic key. The schema and tests confirm the older merchant alias/display-name structure has been removed.

**Impact:** Silent acceptance of obsolete merchant fields can hide stale call sites and discarded data expectations. It also conflicts with the repository's stated preference for removing compatibility adapters once the current design changes.

**Recommendation:** Remove the ignored legacy keyword surface after confirming no current callers need it. Let unexpected merchant fields fail loudly so obsolete code paths are found during tests.

## Severity Counts

- CRITICAL: 3
- IMPORTANT: 6
- NICE TO HAVE: 2

## Five Highest-Priority Findings

1. Coarse transaction fingerprints can silently drop legitimate repeated transactions.
2. Home financial totals bypass the shared refund and reimbursement reporting logic.
3. Reimbursement allocation limits are not atomically enforced.
4. Existing database validation does not verify constraints, indexes, types, or foreign keys.
5. User-visible unique names are not normalized consistently at the database boundary.

## Recurring Patterns and Root Causes

- Some financial invariants are enforced by service-level read-before-write logic instead of an atomic database boundary.
- Compatibility-era cached fields and fallback query logic remain active after newer normalized structures were added.
- Shared reporting expressions exist and are well used in Dashboard, Reports, and Comparison, but not in every financial summary path.
- Tests often encode the current behavior but do not always include adversarial cases such as legitimate repeated transactions, concurrent reimbursement writes, drifted existing schemas, or Home-specific refund/reimbursement totals.
- Generated database documentation is not coupled tightly enough to metadata changes.

## Main Database Areas Inspected

- SQLAlchemy Core schema in `src/finance_app/database/tables.py`.
- Engine, connection, initialization, seeding, schema validation, and transaction helpers.
- Statement upload, parsing, transaction import, duplicate detection, retry, reprocess, undo, Interac enrichment, and background import state.
- Transaction, account, merchant, taxonomy, rule, review, recurring, reimbursement, dashboard, report, comparison, and Home data-access paths.
- Category/rule/tag persistence and query semantics.
- Reimbursement allocation schema, service validation, report offsets, and tests.
- LLM categorization and prompt payload persistence/privacy boundaries.
- Database-oriented tests under `tests/integration`, `tests/routes`, `tests/unit`, and shared support helpers.

## Areas Where No Material Problem Was Found

- Production database access is consistently based on SQLAlchemy Core; I did not find production `sqlite3`, legacy `db_conn`, or raw DB-API cursor use beyond SQLite connection PRAGMA setup.
- `db_core_transaction()` has clear top-level and nested transaction behavior, including rollback paths, and the engine tests cover important nested/savepoint cases.
- The schema defines useful enum-like checks, boolean checks, non-empty checks, fixed-scale money columns, and join-table cascade behavior in many high-value places.
- Dashboard, Reports, and Comparison share central reporting expressions for refunds, non-reportable payments/transfers, reimbursement credits, and reimbursement allocation offsets.
- The LLM categorization prompt payload path appears privacy-minimized: tests cover avoiding raw descriptions, exact dates, exact amounts, and account identifiers in provider-facing payloads.
- CSV export formula-neutralization and frontend escaping were not the focus of this database review, but I did not observe a database-layer issue requiring a finding there.

## Could Not Be Assessed Confidently

- Actual MySQL collation and generated-column behavior against a live MySQL database; review was based on code, mock DDL tests, and SQLite-oriented tests.
- Real statement-provider transaction IDs or row metadata availability; that affects the best replacement for the current fingerprint strategy.
- Production-scale query performance, because I did not run representative data-volume benchmarks or `EXPLAIN` plans.
- Multi-process deployment behavior for the in-memory background runner and statement import queue; the current code suggests local single-process use, but the database state itself does not encode that assumption.
- Whether any runtime development database already contains drift not caught by the current validator; I reviewed code and tests, not local sensitive runtime data.
