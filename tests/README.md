# Test suite layout

Tests are organized by the layer they primarily exercise:

- [unit/](unit/): isolated helpers, parsers, presenters, and pure domain logic.
- [integration/](integration/): database-backed workflows, repositories, services, and cross-module behavior.
- [routes/](routes/): Flask route/controller tests using the test client.
- [smoke/](smoke/): high-value happy-path workflows across routes, background jobs, and persistence.
- [support/](support/): shared test helpers for CSRF setup, database row factories, background job capture, and deterministic LLM stubs.

This file is intentionally scoped to test-suite structure, pytest selection,
fixtures, and curation rules. For development dependency setup and the full
formatter, linter, type-checker, frontend, and pytest gate, see
[Developer guide](../docs/developer-guide.md#quality-checks).

## Curation strategy

The curated suite should maximize confidence per test, not preserve a historical
test count. Keep or add a test when it protects meaningful behavior, a layer
boundary, a financial invariant, or a regression that a maintainer would expect
to catch locally.

Use the lowest layer that can prove the behavior:

- Unit tests cover pure helpers, presenters, parsers, validation, formatting,
  and query-building logic without database or Flask side effects.
- Integration tests cover repositories, services, database constraints,
  imports, financial calculations, transaction boundaries, and cross-module
  workflows.
- Route tests cover HTTP behavior, authentication and authorization, redirects,
  submitted payloads, JSON responses, and rendered state visible to users.
- Smoke tests cover end-to-end happy paths only. One smoke test should usually
  replace many duplicated route-level happy-path checks.

During curation, classify each file or related cluster with one of these
outcomes:

- Keep: the test protects clear behavior with stable assertions and little
  duplication.
- Consolidate: multiple tests protect the same behavior and can share factories,
  fixtures, assertions, or parametrization.
- Move: the behavior is valuable, but the test belongs in a different layer.
- Rewrite: the behavior is valuable, but the test is brittle, reaches across
  layers, bypasses production boundaries, or relies on low-value implementation
  details.
- Delete candidate: the test duplicates stronger coverage, protects obsolete
  behavior, exists only to exercise a fixture, or asserts details with no clear
  user or business value.

Guardrails for pruning:

- Do not remove coverage for financial correctness, imports, authentication and
  authorization, CSRF, schema and migration invariants, LLM failure handling, or
  destructive background jobs until equivalent or better coverage exists.
- Keep presentation, logic, and data concerns separated in tests. Cross-layer
  tests should be intentional route, integration, or smoke coverage.
- Prefer shared helpers from [tests/support](support/) over ad hoc setup. Raw SQL belongs
  in repository or schema tests, or inside support helpers used by higher-level
  tests.
- Prefer behavior-focused assertions over copy, layout, or implementation
  snapshots. Exact HTML, JavaScript, and CSS assertions should protect escaping,
  asset contracts, or serialization regressions.
- Record deleted or merged coverage in the curation inventory created in the
  next step so future maintainers understand what replaced it.

When a curation inventory is maintained, link it from this section so future maintainers can see what replaced deleted or merged coverage.

Prefer helpers from [tests/support](support/) for common setup. Database helpers there use
SQLAlchemy Core table metadata and work with the raw `core_conn` fixture. The
`data_factory` fixture exposes shared builders for users, accounts, statements,
transactions, rules, and tags.

Keep smoke tests broad and light. They should prove that an important workflow
still reaches its happy-path outcome, while detailed route copy, exact HTML,
pagination, sorting, and cleanup assertions belong in route or integration
tests.

For HTML route assertions, prefer parser-backed helpers from
[tests/support/html.py](support/html.py) over broad `response.data` byte checks. Use raw markup
assertions only when exact serialization is what the test is protecting.
Asset reference assertions should also use [tests/support/html.py](support/html.py) so hashed
asset checks stay parser-backed and shared across route files.

Route tests that submit forms or JSON should prefer `csrf_client` or
`anonymous_csrf_client`; those wrappers inject CSRF form fields or JSON headers
automatically. Route authorization tests should prefer the named client fixtures
for common user states: `owner_client`, `editor_client`, `viewer_client`,
`anonymous_client`, `stale_session_client`, and
`must_change_password_client`.

Route and workflow tests that assert queued background work should prefer
`capture_background_jobs` from [tests/support/jobs.py](support/jobs.py) instead of local
monkeypatch recorders.

Pytest markers are assigned automatically from the directory layout. The plain
full-suite command enforces strict markers, warnings as errors, parallel
execution, collection from [tests/](./), and no coverage run.
The suite also blocks socket connections globally; LLM and other external
integration tests should inject fake clients or request functions.

Quality gates in [tests/unit](unit/) keep the curated structure from drifting. They
verify pytest defaults, documented layer directories, the remaining catch-all
route file size, shared background-job recorder usage in route tests, and
selected production module size boundaries.

<details open>
<summary>Windows PowerShell</summary>

```powershell
.\.venv\Scripts\python.exe -B -m pytest
.\.venv\Scripts\python.exe -B -m pytest -m unit
.\.venv\Scripts\python.exe -B -m pytest -m integration
.\.venv\Scripts\python.exe -B -m pytest -m route
.\.venv\Scripts\python.exe -B -m pytest -m "not slow"
.\.venv\Scripts\python.exe -B -m pytest tests\smoke
```

</details>

<details>
<summary>Windows cmd</summary>

```bat
.venv\Scripts\python.exe -B -m pytest
.venv\Scripts\python.exe -B -m pytest -m unit
.venv\Scripts\python.exe -B -m pytest -m integration
.venv\Scripts\python.exe -B -m pytest -m route
.venv\Scripts\python.exe -B -m pytest -m "not slow"
.venv\Scripts\python.exe -B -m pytest tests\smoke
```

</details>

<details>
<summary>macOS</summary>

```bash
.venv/bin/python -B -m pytest
.venv/bin/python -B -m pytest -m unit
.venv/bin/python -B -m pytest -m integration
.venv/bin/python -B -m pytest -m route
.venv/bin/python -B -m pytest -m "not slow"
.venv/bin/python -B -m pytest tests/smoke
```

</details>

<details>
<summary>Linux</summary>

```bash
.venv/bin/python -B -m pytest
.venv/bin/python -B -m pytest -m unit
.venv/bin/python -B -m pytest -m integration
.venv/bin/python -B -m pytest -m route
.venv/bin/python -B -m pytest -m "not slow"
.venv/bin/python -B -m pytest tests/smoke
```

</details>

Capability markers are also added automatically:

- `db`: tests using the database fixture.
- `flask`: tests using the Flask app, request context, or test client.
- `slow`: currently applied to smoke tests.
