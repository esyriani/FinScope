# AGENTS.md

## Project context

FinScope is a local, single-tenant Flask personal finance application. It uses
Python, SQLAlchemy Core, SQLite or MySQL, Bootstrap, ECharts, Jinja templates,
vanilla JavaScript, JSON translation catalogs, background jobs, optional OpenAI
LLM categorization, and pytest with pytest-xdist.

Treat this repository as an actively curated application, not a legacy system to
preserve by compatibility patches. The local database and runtime data are
development/test data. Schema, seeds, generated schema documentation, fixtures,
and tests may be changed when the current clean design requires it. Do not add
migrations or legacy compatibility tables; update the current schema and the
tests/docs that describe it.

Read these files before broad changes:

- `README.md` for setup, workflows, database selection, and current features.
- `docs/architecture.md` for module layering and runtime boundaries.
- `docs/database.md` for schema responsibilities and SQLAlchemy Core rules.
- `docs/testing.md` and `tests/README.md` for test-layer expectations.

## Working principles

- Prefer clean, current design over workaround code. Do not add runtime
  monkey-patches, compatibility aliases, "temporary" fallbacks, broad silent
  exception handlers, or legacy helper wrappers to make old call sites pass.
- If code becomes obsolete, remove it and run tests. Do not leave dead
  production functions, unused compatibility adapters, retired settings, or
  commented-out implementation behind.
- Keep changes focused, but fix the real boundary or abstraction causing the
  issue. Avoid narrow patches that make a test pass while preserving the
  underlying leak.
- Preserve user-visible behavior unless the task explicitly changes it.
- Use existing module patterns, naming, and helper APIs before introducing new
  abstractions.
- Keep financial data handling conservative: exact money arithmetic should stay
  Decimal/Numeric until display or serialization boundaries.
- Treat built-in taxonomy semantics as product behavior, not ad hoc labels.
  Protected categories and tags such as `UNKNOWN`, `Transfers`,
  `Reimbursement`, `Income`, `Rental`, and `Reimbursable` can affect analytics,
  imports, reimbursement workflows, and reporting. Model those effects through
  explicit metadata, semantic types, or strategy/registry objects rather than
  scattered string comparisons or one-off patches.

## Architecture rules

FinScope uses a layered feature-module architecture under
`src/finance_app/modules/<feature>/`. New modules and new behavior must keep
HTTP, business logic, persistence, and presentation shaping separated.

Common responsibilities:

- `controller.py`: Flask routes, authentication/authorization assumptions,
  request data collection, redirects, rendered responses, flashes, JSON response
  shape. Controllers should not issue SQL or own business decisions.
- `forms.py`: form parsing, normalization, and validation.
- `service.py`: use-case orchestration and page context assembly.
- `workflow.py`: multi-step application workflows, background-job work, import
  and undo flows.
- `engine.py`: pure domain logic that does not depend on Flask.
- `presenter.py`: view-model shaping for templates and JSON-safe payloads.
- `queries.py`: read-side SQLAlchemy Core statements and result mapping.
- `repository.py`: write-side persistence helpers and transaction-aware
  mutations.
- `urls.py`: URL/query-string construction shared by controllers and presenters.
- `client_i18n.py`: browser-facing message ids owned by the feature's static
  scripts.

Not every module needs every file. Small features can stay compact, but do not
let a controller or service grow by mixing form parsing, SQL, business rules,
URL building, and template dictionaries in one flow. When a module becomes hard
to scan, first look for a real responsibility boundary. Split only when the new
module has a durable reason to exist, such as a distinct layer, workflow,
external boundary, reusable policy, or independently testable domain concept.
Do not create one-function, constants-only, or dataclass-only production modules
just to reduce file length; a larger cohesive file is preferable to a package
full of artificial fragments.

Avoid import cycles. Shared constants, permissions, runtime settings, and helper
functions should live in neutral modules rather than relying on delayed imports
inside functions.

Current architecture boundaries to preserve:

- `src/finance_app/core/` and `src/finance_app/database/` are foundation
  packages. They must not import `finance_app.modules.*`. Put neutral domain
  semantics in `core` and database-specific SQL/seed orchestration in
  `database`.
- The Flask app factory in `src/finance_app/__init__.py` should stay focused on
  app construction, security cookie configuration, registration of the database,
  auth, filters, assets, CSRF, client-i18n catalogs, and blueprints. Do not add
  feature-owned database work or seeding to app-factory context processors.
- Request-wide UI template context belongs in `src/finance_app/runtime_context.py`.
  Load database-backed UI settings and built-in category exclusions once per
  request, cache them on `g`, and keep template context processors as dictionary
  assembly. Template rendering must not seed taxonomy or open feature-owned
  transactions.
- Runtime setting key ownership lives in `src/finance_app/core/runtime_settings.py`.
  Personal UI preferences such as `theme_mode`, `ui_language`, and table/page
  limits are read for the active user. Owner-managed application behavior such
  as AI model/thresholds, token confirmation, AI rerun behavior, and recurrence
  detection settings must be read through owner/global runtime-setting helpers.
- Built-in taxonomy product semantics live in
  `src/finance_app/core/builtin_taxonomy.py`. Database taxonomy seeding and
  upserts live in `src/finance_app/database/taxonomy.py`. Feature code should
  ask the taxonomy metadata/behavior boundary instead of hard-coding category or
  tag labels.
- Shared dashboard/report analytics concepts belong in neutral helpers such as
  `src/finance_app/core/analytics.py`, `src/finance_app/core/reporting.py`, and
  `src/finance_app/core/urls.py`. Do not reintroduce direct dashboard-to-reports
  or reports-to-dashboard imports.
- Recurring activity is owned by the recurring feature. Keep recurrence
  detection, recurring activity queries, parsing, activity context construction,
  and recurring presentation helpers under `src/finance_app/modules/recurring/`.
  Calendar and Home may consume the recurring read model, but recurring should
  not depend on calendar internals.
- Controllers should not open `db_core_transaction()` or own multi-step business
  workflows. Route modules collect HTTP inputs and handle Flask concerns;
  services/workflows own validation decisions, transaction scopes, persistence
  orchestration, and reusable result objects.
- Background job execution is intentionally process-local and in memory, but
  job lifecycle history is persisted in the database for user visibility and
  recovery diagnostics. Durable job rows that still point at queued/running
  in-process work must have a startup/runtime reconciliation path. Statement
  import recovery and interrupted job repair belong with the database startup
  repair and upload/background workflow boundary, not route-only checks.
- External provider boundaries must be injectable. LLM categorization and
  settings model validation should depend on passed request/client/provider
  collaborators or small adapters, never route-level direct network calls.
- Default LLM categorization requests must use the split
  prepare/request/apply workflow in `src/finance_app/modules/categories/llm_workflow.py`.
  Prepare rule/history and prompt context in a short transaction, release the
  database connection while the provider request runs, then apply validated
  results and persistence updates in a short caller-owned write transaction.
  Do not call the default provider through `classify_unknowns_with_llm()` or
  `categorize_transactions(..., use_llm=True)` inside `db_core_transaction()`.
- Statement import type configuration lives in
  `src/finance_app/modules/statements/types.py`. Settings may edit those rows
  and Upload may read them, but `modules/settings/runtime.py` should stay
  focused on user and owner-managed runtime setting resolution.
- Large modules should be evaluated for mixed responsibilities before adding
  more branches. Physical line count is not an architectural boundary. It is
  acceptable for a cohesive module to be longer when splitting would obscure
  ownership or force readers to hop through many shallow files. Split only when
  the file owns too many responsibilities or crosses an established layer
  boundary.

Special taxonomy behavior should have one clear specialization boundary. Code
that needs to know whether something is income, a transfer, reimbursable,
unknown, or excluded from ordinary reporting should ask that boundary instead
of checking category or tag names inline. Keep the implementation modular enough
that adding a new built-in semantic category or tag changes the taxonomy
metadata and one specialization point, not every report, route, and template.

## Database rules

- SQLAlchemy Core is the only production database API. Do not use `sqlite3`,
  legacy `db_conn`, DB-API cursors, raw connection adapters, or backend-specific
  SQL when a Core expression is practical.
- Runtime persistence helpers must accept or obtain SQLAlchemy Core connections.
  Support both SQLite and MySQL unless a test-only helper is explicitly scoped.
- `src/finance_app/database/tables.py` is the schema source of truth. Update it
  directly for schema changes, along with seeds, constants, docs, and tests.
- Empty databases are created from Core metadata. Existing databases are
  validated against the current schema. Do not add versioned migrations for this
  repo's current development data.
- Database initialization validates the current schema, seeds runtime settings,
  statement types, and built-in taxonomy defaults, then repairs persisted
  runtime state that cannot survive process-local workers. Keep this startup
  repair explicit in `src/finance_app/database/runtime_repair.py`.
- Built-in taxonomy persistence should be seeded through
  `src/finance_app/database/taxonomy.py` from the neutral metadata in
  `src/finance_app/core/builtin_taxonomy.py`. Do not make database seeding import
  feature services.
- Request-time read helpers must not silently create seed-owned data. Category
  option readers, settings page context builders, template context helpers, and
  JSON read paths should return explicit fallbacks or surface initialization
  errors rather than seeding runtime settings, statement types, or built-in
  taxonomy rows.
- Runtime settings readers should receive a caller-owned SQLAlchemy Core
  connection or be accessed through an explicit request-scoped context provider.
  Do not add low-level settings convenience helpers that open their own database
  connection and hide transaction ownership from the caller.
- Prefer database constraints for invariants that must survive concurrency:
  uniqueness, ownership, generated normalized keys, foreign keys, enum-like
  checks, non-empty values, and nullable uniqueness helpers.
- Use `db_core_transaction()` for runtime writes. When a caller already has a
  connection, pass it through so nested work uses the same transaction/savepoint.
- Keep schema artifacts current when the data model changes:
  `docs/database.md`, `docs/db-schema.html`, and `docs/diagrams/db-schema.dbs`
  should describe the current metadata. Regenerate generated artifacts instead
  of hand-editing them.

## Testing rules

Run tests with pytest-xdist. The default command already enforces collection
from `tests/`, strict markers, warnings as errors, parallel execution, and no
coverage slowdown:

```powershell
.\.venv\Scripts\python.exe -B -m pytest
```

For iteration, use the smallest useful marker or file selection:

```powershell
.\.venv\Scripts\python.exe -B -m pytest -m unit
.\.venv\Scripts\python.exe -B -m pytest -m integration
.\.venv\Scripts\python.exe -B -m pytest -m route
.\.venv\Scripts\python.exe -B -m pytest -m smoke
```

Run the full local quality gate before finishing code changes that affect
production code, templates, browser assets, tests, or tooling:

```powershell
.\.venv\Scripts\python.exe -B -m black --check .
.\.venv\Scripts\python.exe -B -m djlint src/finance_app/templates --profile=jinja --lint
.\.venv\Scripts\python.exe -B -m ruff check .
.\.venv\Scripts\python.exe -B -m mypy
npm run lint:frontend
.\.venv\Scripts\python.exe -B -m pytest
```

The GitHub Actions workflow in `.github/workflows/quality.yml` runs the same
quality gates on every push and pull request. See `docs/testing.md` for
installation commands and platform-specific variants.

Testing expectations:

- Add or update tests when behavior, schema, security, financial calculations,
  imports, route contracts, or edge cases change.
- Do not add new test cases for trivial work: copy-only changes, pure CSS
  polish, simple renames, markup reshuffling with no behavioral contract, or
  behavior already protected at a better layer. Update existing assertions only
  when the user-visible contract intentionally changes.
- Prefer the lowest layer that proves the behavior: unit for pure logic,
  integration for database/services/workflows, route for HTTP/rendered state,
  smoke for high-value happy paths only.
- Use shared helpers from `tests/support`: `data_factory`, Core insert builders,
  `csrf_client`, `anonymous_csrf_client`, named auth clients, parser-backed HTML
  assertions, background-job recorders, and deterministic LLM fakes.
- Do not duplicate helper setup such as CSRF token insertion, row factories,
  LLM request stubs, or background-job capture in individual test files.
- Use parser-backed HTML assertions from `tests/support/html.py` for route
  semantics. Raw `response.data` or exact markup assertions should be reserved
  for escaping, asset fingerprints, serialization, and other exact-output
  contracts.
- Tests must not make network calls. The global network guard is intentional;
  inject fake clients/request functions for LLM and provider behavior.
- Prefer dependency injection and shared fakes over monkeypatching module
  globals. When monkeypatching is necessary, keep it local to tests and do not
  create production test seams for it.
- Keep smoke tests broad and light. They should prove a workflow reaches a
  useful outcome, not duplicate route and integration assertions.
- Keep static architecture tests current when boundaries move. In particular,
  `tests/unit/test_import_boundaries.py` guards low-level package imports,
  controller transaction ownership, recurring/calendar ownership, dashboard and
  reports independence, and app-factory render-time dependencies.
- Do not add tests that assert production modules stay under a fixed number of
  lines. Prefer semantic boundary tests, dependency-direction checks, and
  behavior tests over size budgets.

Documentation-only or editorial-only changes do not require a full test run, but
the final response must say that tests were not run and why.

## Frontend and templates

- Keep page assets scoped through `page_stylesheets`, `page_scripts`,
  `page_vendor_stylesheets`, and `page_vendor_scripts`. Do not load every app
  script from `base.html`.
- Use `window.financeApp.registerInitializer(...)` for page behavior and
  `window.financeApp.runInitializers(root)` after AJAX replacements. Do not
  export new `window.setup...` globals or make `ajax-actions.js` know about
  page-specific initializers.
- Keep shared browser helpers centralized: `app-boot.js` owns locale,
  translations, and money formatting; `chart-utils.js` owns common ECharts
  helpers.
- Avoid dynamic `innerHTML` for user, imported, merchant, account, transaction,
  rule, or job data. Build nodes with DOM APIs and `textContent`. Static modal
  skeletons or trusted icon markup can use HTML strings only when the values are
  controlled and tests cover the contract.
- Render imported/user data escaped in templates. Do not mark merchant,
  description, account, category, tag, or statement values with `|safe`.
- CSV exports must neutralize spreadsheet formulas for values beginning with
  `=`, `+`, `-`, `@`, tab, or carriage return.
- Navigation links should use `url_for(...)`; active state should use endpoint
  or blueprint data rather than hard-coded `request.path` checks.
- Clickable rows and controls must be keyboard-accessible with appropriate
  roles, focus behavior, and Enter/Space handling.
- Reuse shared macros/partials such as pagination instead of duplicating
  template structures.
- Keep cautious workflows close to the user's starting point. For rule editing
  and audit-heavy flows, prefer modal dialogs that preview impact, confirm
  risky changes, apply the action, and return the user to the same filtered list
  state. Do not introduce side drawers unless the app adopts them as a broader
  design pattern.
- Name exports by their actual artifact and purpose. If a top-level export
  produces an importable rules file, label it `Export rules`, not generic
  `Export CSV`. Avoid showing generic table CSV/Excel exports beside a domain
  export when the outputs differ enough to confuse users.
- Responsive report and dashboard layouts should be driven by usable content
  width, not only by device classes. Charts and tables should span the full
  available width once a two-column layout would force horizontal scrolling,
  clip labels/actions, or make table columns cramped. Prefer container queries
  on the page or report content area with viewport fallbacks; collapse
  chart/table pairs earlier than lightweight KPI cards. Keep horizontal table
  scrolling for genuinely wide tables after the table card itself is full
  width.

## Product terminology

- Use `Spending` for outflows and expense totals. Use `Income and credits` for
  credits that may include ordinary income, reimbursements, refunds, and other
  incoming money. Use `Net cash flow` for income minus spending.
- Merchant summary headings should include the configured limit, for example
  `Top 10 merchant analytics`, instead of the vague `Merchant analytics`.
- Built-in taxonomy semantics are internal functioning unless they directly
  explain an available action. Do not show badges such as `Affects reports`,
  `Ordinary income`, or `Workflow ready` in normal taxonomy lists. Document what
  each built-in category or tag affects in user documentation instead.
- Use canonical taxonomy names consistently in code, docs, and UI where they
  are data values: `UNKNOWN`, `Transfers`, `Reimbursement`, `Income`, `Rental`,
  and `Reimbursable`.

## Internationalization

- English source strings are the canonical message ids.
- Python user-facing strings must use `gettext()` or the template `_()` helper.
- Browser-facing strings must use `window.financeTranslate()` and be listed in
  the appropriate client-i18n catalog. Shared browser messages live in
  `src/finance_app/core/client_i18n.py`; feature-owned browser messages live in
  `src/finance_app/modules/<feature>/client_i18n.py`; the aggregate registration
  lives in `src/finance_app/modules/client_i18n.py`. Do not add feature-specific
  browser messages to the Flask app factory.
- Add or update French translations in `src/finance_app/translations/fr.json`
  for all new user-facing text.
- Keep `src/finance_app/translations/fr.json` ASCII-only. Encode French
  accents, apostrophes, guillemets, non-breaking spaces, and other non-ASCII
  punctuation with JSON Unicode escapes such as `\u00e9`, `\u00e8`, `\u00e0`,
  `\u00e7`, and `\u2019`.
- Do not translate user data: merchant names, account names, category names,
  tag names, statement filenames, uploaded statement contents, and transaction
  descriptions remain user data.
- In user-facing vocabulary, avoid computer-technical terms. For example, use
  "categories & tags" instead of "taxonomy", "processing" instead of "jobs",
  and "AI" instead of LLM or model.

## Security and privacy

- Treat financial data, uploaded statements, runtime databases, backups, API
  keys, and logs as sensitive. Do not commit runtime data or secrets.
- Preserve CSRF protection on mutating routes and tests.
- Keep authorization checks explicit and covered for owner, editor, viewer,
  anonymous, stale-session, and must-change-password states when behavior
  differs.
- LLM categorization must remain privacy-minimized. Do not send raw transaction
  descriptions, exact dates, exact amounts, account names/types/IDs, or similar
  transaction examples to external providers.
- Optional provider integrations must be injectable so tests use fakes and the
  global network guard remains effective.
- Provider availability checks, including OpenAI model validation in Settings,
  must use an adapter or injected client factory and return typed outcomes.
  Route-facing services should not construct provider clients directly.

## Documentation and comments

New Python modules must start with a concise module docstring explaining the
module responsibility, main collaborators, and important side effects or
assumptions. Do not include author names, dates, or changelog prose.

Document new functions, classes, Flask routes, database helpers, and service
logic with concise docstrings. For routes, include HTTP behavior and important
session/auth/redirect/response assumptions. For database helpers, include schema
and transaction assumptions when relevant.

Use comments sparingly. Explain why non-trivial logic exists; do not restate
what the code says. Update stale comments near changed code.

Update README or docs when setup, dependencies, architecture, schema,
configuration, workflows, or user-facing behavior changes. Favor sentence case
in documentation and UI headings.

## Review checklist

Before finishing a code change, verify:

- The layer boundaries are still clean.
- Obsolete code was removed rather than kept as a compatibility path.
- SQL uses SQLAlchemy Core and remains portable between SQLite and MySQL.
- Financial calculations avoid unnecessary float conversion.
- User-facing text is translated in English and French.
- Frontend behavior uses registered initializers and accessible controls.
- Related tests were added, updated, or intentionally left unchanged.
- The relevant quality gate was run and reported. Prefer the full local quality
  gate for code changes; for docs-only or tightly scoped edits, report the
  narrower command and why it is sufficient.
