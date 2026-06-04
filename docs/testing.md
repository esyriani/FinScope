# Testing

The FinScope test suite is organized by layer and uses strict pytest markers.
The default pytest configuration runs tests in parallel with pytest-xdist,
collects tests only from `tests/`, skips coverage by default for speed, and
treats warnings as errors.
An autouse network guard blocks socket connections in every test; external
integrations such as LLM providers must be exercised through injected fakes.

## Run the suite

Full suite:

```powershell
$env:PYTHONDONTWRITEBYTECODE = "1"
.\.venv\Scripts\python.exe -B -m pytest
```

By layer, during local iteration:

```powershell
.\.venv\Scripts\python.exe -B -m pytest -m unit
.\.venv\Scripts\python.exe -B -m pytest -m integration
.\.venv\Scripts\python.exe -B -m pytest -m route
.\.venv\Scripts\python.exe -B -m pytest -m smoke
```

Other useful selections:

```powershell
.\.venv\Scripts\python.exe -B -m pytest -m "not slow"
.\.venv\Scripts\python.exe -B -m pytest -m "db and not smoke"
.\.venv\Scripts\python.exe -B -m pytest tests\unit\test_merchant_normalization.py
```

Coverage and warning gates:

Warnings are always test failures. Run coverage deliberately when needed:

```powershell
.\.venv\Scripts\python.exe -B -m pytest --cov=finance_app --cov-report=term-missing --cov-fail-under=91
```

## Markers

- `unit`: isolated helper, parser, presenter, and domain tests.
- `integration`: database-backed service, repository, and workflow tests.
- `route`: Flask route and controller tests.
- `smoke`: high-value happy-path workflow checks. Keep these broad and light;
  put detailed route copy, exact HTML, pagination, sorting, and cleanup
  assertions in route or integration tests.
- `slow`: currently applied to smoke tests.
- `db`: tests using the database fixture.
- `flask`: tests using Flask app, request context, or test client.

## Layout

```text
tests/
  unit/
  integration/
  routes/
  smoke/
  support/
```

Prefer the smallest useful test layer. Use smoke tests for critical cross-layer workflows, not for behavior already covered cleanly by unit, integration, or route tests.

Use shared helpers from `tests/support` for common route setup, Core-backed row
factories, and deterministic LLM payloads. Database tests should use the raw
`core_conn` fixture or the `data_factory` builders, which cover users,
accounts, statements, transactions, rules, and tags. Route tests that post forms
or JSON should prefer `csrf_client` or `anonymous_csrf_client`, which inject
CSRF data automatically. Route authorization tests should prefer the explicit
`owner_client`, `editor_client`, `viewer_client`, `anonymous_client`,
`stale_session_client`, and `must_change_password_client` fixtures instead of
manually creating Flask sessions.

LLM and other integration tests must not call real services. Use helpers from
`tests/support/llm.py` or inject a fake client/request function so the global
network guard can keep the suite hermetic.

HTML route tests should prefer parser-backed helpers from `tests/support/html.py`.
Use `assert_visible_text`, `assert_has_element`, `assert_link`, `assert_form`,
`assert_input`, and `assert_option` for page semantics instead of broad
`response.data` byte checks. Raw markup checks should be reserved for asset
fingerprints or cases where the exact serialized HTML is the behavior.

See `tests/README.md` for more detail.
