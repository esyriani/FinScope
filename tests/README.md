# Test suite layout

Tests are organized by the layer they primarily exercise:

- `unit/`: isolated helpers, parsers, presenters, and pure domain logic.
- `integration/`: database-backed workflows, repositories, services, and cross-module behavior.
- `routes/`: Flask route/controller tests using the test client.
- `smoke/`: high-value happy-path workflows across routes, background jobs, and persistence.
- `support/`: shared test helpers for CSRF setup, database row factories, and deterministic LLM stubs.

Prefer helpers from `tests/support` for common setup. Database helpers there use
SQLAlchemy Core table metadata and work with the raw `core_conn` fixture. The
`data_factory` fixture exposes shared builders for users, accounts, statements,
transactions, rules, and tags.

Keep smoke tests broad and light. They should prove that an important workflow
still reaches its happy-path outcome, while detailed route copy, exact HTML,
pagination, sorting, and cleanup assertions belong in route or integration
tests.

For HTML route assertions, prefer parser-backed helpers from
`tests/support/html.py` over broad `response.data` byte checks. Use raw markup
assertions only when exact serialization is what the test is protecting.

Route tests that submit forms or JSON should prefer `csrf_client` or
`anonymous_csrf_client`; those wrappers inject CSRF form fields or JSON headers
automatically. Route authorization tests should prefer the named client fixtures
for common user states: `owner_client`, `editor_client`, `viewer_client`,
`anonymous_client`, `stale_session_client`, and
`must_change_password_client`.

Pytest markers are assigned automatically from the directory layout. The plain
full-suite command enforces strict markers, warnings as errors, parallel
execution, collection from `tests/`, and no coverage run.
The suite also blocks socket connections globally; LLM and other external
integration tests should inject fake clients or request functions.

```powershell
.\.venv\Scripts\python.exe -B -m pytest
.\.venv\Scripts\python.exe -B -m pytest -m unit
.\.venv\Scripts\python.exe -B -m pytest -m integration
.\.venv\Scripts\python.exe -B -m pytest -m route
.\.venv\Scripts\python.exe -B -m pytest -m "not slow"
.\.venv\Scripts\python.exe -B -m pytest tests\smoke
```

Capability markers are also added automatically:

- `db`: tests using the database fixture.
- `flask`: tests using the Flask app, request context, or test client.
- `slow`: currently applied to smoke tests.
