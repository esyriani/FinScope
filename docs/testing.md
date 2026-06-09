# Testing

The FinScope test suite is organized by layer and uses strict pytest markers.
The default pytest configuration runs tests in parallel with pytest-xdist,
collects tests only from [tests/](../tests/), skips coverage by default for speed, and
treats warnings as errors.
An autouse network guard blocks socket connections in every test; external
integrations such as LLM providers must be exercised through injected fakes.

## Run the suite

Full suite:

<details open>
<summary>Windows PowerShell</summary>

```powershell
$env:PYTHONDONTWRITEBYTECODE = "1"
.\.venv\Scripts\python.exe -B -m pytest
```

</details>

<details>
<summary>Windows cmd</summary>

```bat
set "PYTHONDONTWRITEBYTECODE=1"
.venv\Scripts\python.exe -B -m pytest
```

</details>

<details>
<summary>macOS</summary>

```bash
export PYTHONDONTWRITEBYTECODE=1
.venv/bin/python -B -m pytest
```

</details>

<details>
<summary>Linux</summary>

```bash
export PYTHONDONTWRITEBYTECODE=1
.venv/bin/python -B -m pytest
```

</details>

By layer, during local iteration:

<details open>
<summary>Windows PowerShell</summary>

```powershell
.\.venv\Scripts\python.exe -B -m pytest -m unit
.\.venv\Scripts\python.exe -B -m pytest -m integration
.\.venv\Scripts\python.exe -B -m pytest -m route
.\.venv\Scripts\python.exe -B -m pytest -m smoke
```

</details>

<details>
<summary>Windows cmd</summary>

```bat
.venv\Scripts\python.exe -B -m pytest -m unit
.venv\Scripts\python.exe -B -m pytest -m integration
.venv\Scripts\python.exe -B -m pytest -m route
.venv\Scripts\python.exe -B -m pytest -m smoke
```

</details>

<details>
<summary>macOS</summary>

```bash
.venv/bin/python -B -m pytest -m unit
.venv/bin/python -B -m pytest -m integration
.venv/bin/python -B -m pytest -m route
.venv/bin/python -B -m pytest -m smoke
```

</details>

<details>
<summary>Linux</summary>

```bash
.venv/bin/python -B -m pytest -m unit
.venv/bin/python -B -m pytest -m integration
.venv/bin/python -B -m pytest -m route
.venv/bin/python -B -m pytest -m smoke
```

</details>

Other useful selections:

<details open>
<summary>Windows PowerShell</summary>

```powershell
.\.venv\Scripts\python.exe -B -m pytest -m "not slow"
.\.venv\Scripts\python.exe -B -m pytest -m "db and not smoke"
.\.venv\Scripts\python.exe -B -m pytest tests\unit\test_merchant_normalization.py
```

</details>

<details>
<summary>Windows cmd</summary>

```bat
.venv\Scripts\python.exe -B -m pytest -m "not slow"
.venv\Scripts\python.exe -B -m pytest -m "db and not smoke"
.venv\Scripts\python.exe -B -m pytest tests\unit\test_merchant_normalization.py
```

</details>

<details>
<summary>macOS</summary>

```bash
.venv/bin/python -B -m pytest -m "not slow"
.venv/bin/python -B -m pytest -m "db and not smoke"
.venv/bin/python -B -m pytest tests/unit/test_merchant_normalization.py
```

</details>

<details>
<summary>Linux</summary>

```bash
.venv/bin/python -B -m pytest -m "not slow"
.venv/bin/python -B -m pytest -m "db and not smoke"
.venv/bin/python -B -m pytest tests/unit/test_merchant_normalization.py
```

</details>

Coverage and warning gates:

Warnings are always test failures. Run coverage deliberately when needed:

<details open>
<summary>Windows PowerShell</summary>

```powershell
.\.venv\Scripts\python.exe -B -m pytest --cov=finance_app --cov-report=term-missing --cov-fail-under=91
```

</details>

<details>
<summary>Windows cmd</summary>

```bat
.venv\Scripts\python.exe -B -m pytest --cov=finance_app --cov-report=term-missing --cov-fail-under=91
```

</details>

<details>
<summary>macOS</summary>

```bash
.venv/bin/python -B -m pytest --cov=finance_app --cov-report=term-missing --cov-fail-under=91
```

</details>

<details>
<summary>Linux</summary>

```bash
.venv/bin/python -B -m pytest --cov=finance_app --cov-report=term-missing --cov-fail-under=91
```

</details>

## Code quality checks

Install the developer tools before running formatter, linter, or type-checker
commands. Frontend checks require Node.js 20+ with npm:

<details open>
<summary>Windows PowerShell</summary>

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
npm install
```

</details>

<details>
<summary>Windows cmd</summary>

```bat
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
npm install
```

</details>

<details>
<summary>macOS</summary>

```bash
.venv/bin/python -m pip install -r requirements-dev.txt
npm install
```

</details>

<details>
<summary>Linux</summary>

```bash
.venv/bin/python -m pip install -r requirements-dev.txt
npm install
```

</details>

Run the current quality checks from the repository root:

<details open>
<summary>Windows PowerShell</summary>

```powershell
.\.venv\Scripts\python.exe -B -m black --check .
.\.venv\Scripts\python.exe -B -m djlint src/finance_app/templates --profile=jinja --lint
.\.venv\Scripts\python.exe -B -m ruff check .
.\.venv\Scripts\python.exe -B -m mypy
npm run lint:frontend
```

</details>

<details>
<summary>Windows cmd</summary>

```bat
.venv\Scripts\python.exe -B -m black --check .
.venv\Scripts\python.exe -B -m djlint src/finance_app/templates --profile=jinja --lint
.venv\Scripts\python.exe -B -m ruff check .
.venv\Scripts\python.exe -B -m mypy
npm run lint:frontend
```

</details>

<details>
<summary>macOS</summary>

```bash
.venv/bin/python -B -m black --check .
.venv/bin/python -B -m djlint src/finance_app/templates --profile=jinja --lint
.venv/bin/python -B -m ruff check .
.venv/bin/python -B -m mypy
npm run lint:frontend
```

</details>

<details>
<summary>Linux</summary>

```bash
.venv/bin/python -B -m black --check .
.venv/bin/python -B -m djlint src/finance_app/templates --profile=jinja --lint
.venv/bin/python -B -m ruff check .
.venv/bin/python -B -m mypy
npm run lint:frontend
```

</details>

The same quality gates run in GitHub Actions for every push and pull request.

Mypy is configured in [pyproject.toml](../pyproject.toml) for [sitecustomize.py](../sitecustomize.py) and the production
application package. Djlint checks Jinja templates in lint-only mode. Frontend
checks are configured through npm scripts: Prettier checks formatting for
first-party static JavaScript and CSS, ESLint checks browser JavaScript, and
Stylelint checks first-party CSS. Vendored browser libraries are excluded.

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

- [tests/unit/](../tests/unit/)
- [tests/integration/](../tests/integration/)
- [tests/routes/](../tests/routes/)
- [tests/smoke/](../tests/smoke/)
- [tests/support/](../tests/support/)

Prefer the smallest useful test layer. Use smoke tests for critical cross-layer workflows, not for behavior already covered cleanly by unit, integration, or route tests.

Use shared helpers from [tests/support/](../tests/support/) for common route setup, Core-backed row
factories, and deterministic LLM payloads. Database tests should use the raw
`core_conn` fixture or the `data_factory` builders, which cover users,
accounts, statements, transactions, rules, and tags. Route tests that post forms
or JSON should prefer `csrf_client` or `anonymous_csrf_client`, which inject
CSRF data automatically. Route authorization tests should prefer the explicit
`owner_client`, `editor_client`, `viewer_client`, `anonymous_client`,
`stale_session_client`, and `must_change_password_client` fixtures instead of
manually creating Flask sessions.

LLM and other integration tests must not call real services. Use helpers from
[tests/support/llm.py](../tests/support/llm.py) or inject a fake client/request function so the global
network guard can keep the suite hermetic.

HTML route tests should prefer parser-backed helpers from [tests/support/html.py](../tests/support/html.py).
Use `assert_visible_text`, `assert_has_element`, `assert_link`, `assert_form`,
`assert_input`, and `assert_option` for page semantics instead of broad
`response.data` byte checks. Raw markup checks should be reserved for asset
fingerprints or cases where the exact serialized HTML is the behavior.

See [tests/README.md](../tests/README.md) for more detail.
