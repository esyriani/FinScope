# Testing

The FinScope test suite is organized by layer and uses strict pytest markers.
The default pytest configuration runs tests in parallel with pytest-xdist,
collects tests only from [tests/](../tests/), skips coverage by default for
speed, excludes `optional` tests, and treats warnings as errors.
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
.\.venv\Scripts\python.exe -B -m pytest --cov=finance_app --cov-report=term-missing --cov-fail-under=93
```

</details>

<details>
<summary>Windows cmd</summary>

```bat
.venv\Scripts\python.exe -B -m pytest --cov=finance_app --cov-report=term-missing --cov-fail-under=93
```

</details>

<details>
<summary>macOS</summary>

```bash
.venv/bin/python -B -m pytest --cov=finance_app --cov-report=term-missing --cov-fail-under=93
```

</details>

<details>
<summary>Linux</summary>

```bash
.venv/bin/python -B -m pytest --cov=finance_app --cov-report=term-missing --cov-fail-under=93
```

</details>

## LLM prompt evals

The ordinary pytest suite remains network-free. Sanitized categorization prompt
examples live in
[evals/llm_categorization/datasets/validation.jsonl](../evals/llm_categorization/datasets/validation.jsonl),
and the opt-in harness reuses the production prompt builder with an injected
provider.

Use dry-run mode to validate the dataset, scoring, and report generation without
calling a model:

<details open>
<summary>Windows PowerShell</summary>

```powershell
.\.venv\Scripts\python.exe -B evals\llm_categorization\run_prompt_eval.py --dry-run
```

</details>

<details>
<summary>Windows cmd</summary>

```bat
.venv\Scripts\python.exe -B evals\llm_categorization\run_prompt_eval.py --dry-run
```

</details>

<details>
<summary>macOS</summary>

```bash
.venv/bin/python -B evals/llm_categorization/run_prompt_eval.py --dry-run
```

</details>

<details>
<summary>Linux</summary>

```bash
.venv/bin/python -B evals/llm_categorization/run_prompt_eval.py --dry-run
```

</details>

To run against OpenAI, set `OPENAI_API_KEY` or the app setting, pass the model
with `--model`, and review the generated `report.md` and `raw_outputs.jsonl`
under `evals/llm_categorization/runs/`. Do not add real financial examples or
private model outputs to the repository.

## Optional test lanes

The default suite stays fast and SQLite-backed. Slower or environment-specific
checks live under [tests/optional/](../tests/optional/) and must be marked
`optional` plus a capability marker such as `mysql` or `mutation`. Use this
pattern for live backend checks, mutation testing, or future long-running gates
that should be run deliberately rather than on every local edit.

Live MySQL coverage requires a server URL with permission to create and drop
temporary databases. The configured database name is used only as a safe prefix;
each test creates a disposable database with a unique suffix and drops it during
teardown.

<details open>
<summary>Windows PowerShell</summary>

```powershell
$env:FINSCOPE_TEST_MYSQL_URL = "mysql+pymysql://root:password@127.0.0.1:3306/finscope_test?charset=utf8mb4"
.\.venv\Scripts\python.exe -B -m pytest -n 0 -m "optional and mysql"
```

</details>

<details>
<summary>Windows cmd</summary>

```bat
set "FINSCOPE_TEST_MYSQL_URL=mysql+pymysql://root:password@127.0.0.1:3306/finscope_test?charset=utf8mb4"
.venv\Scripts\python.exe -B -m pytest -n 0 -m "optional and mysql"
```

</details>

<details>
<summary>macOS</summary>

```bash
export FINSCOPE_TEST_MYSQL_URL="mysql+pymysql://root:password@127.0.0.1:3306/finscope_test?charset=utf8mb4"
.venv/bin/python -B -m pytest -n 0 -m "optional and mysql"
```

</details>

<details>
<summary>Linux</summary>

```bash
export FINSCOPE_TEST_MYSQL_URL="mysql+pymysql://root:password@127.0.0.1:3306/finscope_test?charset=utf8mb4"
.venv/bin/python -B -m pytest -n 0 -m "optional and mysql"
```

</details>

GitHub Actions keeps the normal quality and test jobs on every push and pull
request. The optional MySQL job runs only from manual workflow dispatches and
the scheduled weekly workflow.

### Rule-engine mutation experiment

FinScope includes a first on-demand Cosmic Ray mutation-testing experiment for
the deterministic rule-based categorization engine. It mutates only
[src/finance_app/modules/rules/engine.py](../src/finance_app/modules/rules/engine.py)
and runs the existing rule-engine focused tests from
[tests/mutation/cosmic-ray-rules-engine.toml](../tests/mutation/cosmic-ray-rules-engine.toml). This campaign
is a diagnostic tool for test-suite analysis, not a CI gate.

Activate the development virtual environment before running Cosmic Ray so the
`cosmic-ray` command itself resolves to the project install. The config invokes
[tools/cosmic_ray_rule_engine_tests.py](../tools/cosmic_ray_rule_engine_tests.py),
which locates the repo virtualenv and runs the focused pytest selection.
Session databases and dumps should stay under `runtime/mutation/`, which is not
committed.

<details open>
<summary>Windows PowerShell</summary>

```powershell
.\.venv\Scripts\Activate.ps1
cosmic-ray baseline tests/mutation/cosmic-ray-rules-engine.toml
New-Item -ItemType Directory -Force -Path runtime\mutation | Out-Null
cosmic-ray init --force tests/mutation/cosmic-ray-rules-engine.toml runtime\mutation\rules-engine.sqlite
cosmic-ray exec tests/mutation/cosmic-ray-rules-engine.toml runtime\mutation\rules-engine.sqlite
cosmic-ray dump runtime\mutation\rules-engine.sqlite > runtime\mutation\rules-engine-dump.jsonl
python tools\cosmic_ray_summary.py runtime\mutation\rules-engine-dump.jsonl
```

</details>

<details>
<summary>Windows cmd</summary>

```bat
.venv\Scripts\activate.bat
cosmic-ray baseline tests/mutation/cosmic-ray-rules-engine.toml
if not exist runtime\mutation mkdir runtime\mutation
cosmic-ray init --force tests/mutation/cosmic-ray-rules-engine.toml runtime\mutation\rules-engine.sqlite
cosmic-ray exec tests/mutation/cosmic-ray-rules-engine.toml runtime\mutation\rules-engine.sqlite
cosmic-ray dump runtime\mutation\rules-engine.sqlite > runtime\mutation\rules-engine-dump.jsonl
python tools\cosmic_ray_summary.py runtime\mutation\rules-engine-dump.jsonl
```

</details>

<details>
<summary>macOS</summary>

```bash
source .venv/bin/activate
cosmic-ray baseline tests/mutation/cosmic-ray-rules-engine.toml
mkdir -p runtime/mutation
cosmic-ray init --force tests/mutation/cosmic-ray-rules-engine.toml runtime/mutation/rules-engine.sqlite
cosmic-ray exec tests/mutation/cosmic-ray-rules-engine.toml runtime/mutation/rules-engine.sqlite
cosmic-ray dump runtime/mutation/rules-engine.sqlite > runtime/mutation/rules-engine-dump.jsonl
python tools/cosmic_ray_summary.py runtime/mutation/rules-engine-dump.jsonl
```

</details>

<details>
<summary>Linux</summary>

```bash
source .venv/bin/activate
cosmic-ray baseline tests/mutation/cosmic-ray-rules-engine.toml
mkdir -p runtime/mutation
cosmic-ray init --force tests/mutation/cosmic-ray-rules-engine.toml runtime/mutation/rules-engine.sqlite
cosmic-ray exec tests/mutation/cosmic-ray-rules-engine.toml runtime/mutation/rules-engine.sqlite
cosmic-ray dump runtime/mutation/rules-engine.sqlite > runtime/mutation/rules-engine-dump.jsonl
python tools/cosmic_ray_summary.py runtime/mutation/rules-engine-dump.jsonl
```

</details>

## Code quality checks

Install the developer tools before running formatter, linter, or type-checker
commands. Frontend checks require Node.js 20+ with npm:

<details open>
<summary>Windows PowerShell</summary>

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
npm ci
```

</details>

<details>
<summary>Windows cmd</summary>

```bat
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
npm ci
```

</details>

<details>
<summary>macOS</summary>

```bash
.venv/bin/python -m pip install -r requirements-dev.txt
npm ci
```

</details>

<details>
<summary>Linux</summary>

```bash
.venv/bin/python -m pip install -r requirements-dev.txt
npm ci
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
first-party static JavaScript, CSS, and frontend runtime tests; ESLint checks
browser JavaScript and the frontend tests; Stylelint checks first-party CSS; and
Vitest runs jsdom browser-behavior tests for selected static assets. Vendored
browser libraries are excluded.

## Markers

- `unit`: isolated helper, parser, presenter, and domain tests.
- `integration`: database-backed service, repository, and workflow tests.
- `route`: Flask route and controller tests.
- `smoke`: high-value happy-path workflow checks. Keep these broad and light;
  put detailed route copy, exact HTML, pagination, sorting, and cleanup
  assertions in route or integration tests.
- `slow`: currently applied to smoke tests.
- `optional`: opt-in tests excluded from default local and pull request runs.
- `mysql`: live MySQL runtime tests requiring `FINSCOPE_TEST_MYSQL_URL`.
- `mutation`: reserved for optional future mutation-testing checks.
- `db`: tests using the database fixture.
- `flask`: tests using Flask app, request context, or test client.

## Layout

- [tests/unit/](../tests/unit/)
- [tests/integration/](../tests/integration/)
- [tests/routes/](../tests/routes/)
- [tests/smoke/](../tests/smoke/)
- [tests/optional/](../tests/optional/)
- [tests/frontend/](../tests/frontend/)
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
