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

Not every module needs every file. Small features can stay compact, but do not
let a controller or service grow by mixing form parsing, SQL, business rules,
URL building, and template dictionaries in one flow. When a module becomes hard
to scan, split by responsibility before adding more branches.

Avoid import cycles. Shared constants, permissions, runtime settings, and helper
functions should live in neutral modules rather than relying on delayed imports
inside functions.

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
- Do not increase test density for trivial refactors, pure renames, or behavior
  already protected at a better layer.
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

## Internationalization

- English source strings are the canonical message ids.
- Python user-facing strings must use `gettext()` or the template `_()` helper.
- Browser-facing strings must use `window.financeTranslate()` and be listed in
  `CLIENT_TRANSLATION_MESSAGES`.
- Add or update French translations in `src/finance_app/translations/fr.json`
  for all new user-facing text.
- Do not translate user data: merchant names, account names, category names,
  tag names, statement filenames, uploaded statement contents, and transaction
  descriptions remain user data.

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
