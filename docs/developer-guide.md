# Developer guide

This guide covers repository onboarding and development workflow. Detailed architecture, database, testing, and background processing behavior remains in [Architecture](architecture.md), [Database](database.md), [Testing](testing.md), and [Processing activity](background-jobs.md).

## Tech stack

- Python 3.10+; developed and tested with Python 3.11.9.
- Flask 3.1.3 and Flask-Login 0.6.3.
- SQLAlchemy Core 2.0.49 with SQLite 3.31+ or MySQL 8.0.16+ through PyMySQL 1.1.3.
- Jinja templates 3.1.6.
- Bootstrap 5.3.3 and ECharts 5.6.0.
- pytest 9.0.3 with pytest-xdist.
- OpenAI SDK 2.33.0 for optional AI categorization.
- Node.js 20+ with npm for frontend formatting and linting.

## Repository layout

- [src/finance_app/__init__.py](../src/finance_app/__init__.py): Flask application factory.
- [src/finance_app/app.py](../src/finance_app/app.py): application entry point used by the `finscope` console command.
- [src/finance_app/core/](../src/finance_app/core/): configuration, constants, SQLAlchemy query helpers, CSRF, filters, and i18n helpers.
- [src/finance_app/database/](../src/finance_app/database/): SQLAlchemy lifecycle, metadata, schema validation, and initialization seeds.
- [src/finance_app/background/](../src/finance_app/background/): in-memory background job runner and undo orchestration.
- [src/finance_app/modules/](../src/finance_app/modules/): feature modules, including auth, settings, upload, rules, review, and reporting.
- [src/finance_app/templates/](../src/finance_app/templates/): Jinja templates.
- [src/finance_app/static/](../src/finance_app/static/): CSS, JavaScript, image assets, and vendored browser libraries.
- [src/finance_app/translations/](../src/finance_app/translations/): JSON translation catalogs for user interface text.
- [config.example.ini](../config.example.ini): sample root runtime configuration copied to ignored `config.ini` for local runs.
- [tests/](../tests/): unit, integration, route, smoke, and shared support helpers.
- [docs/](./): project documentation.
- [runtime/](../runtime/): local runtime data, including the default SQLite database.

## Development setup

Start with the runtime setup in [Getting started](getting-started.md), then install the developer dependencies.

<details open>
<summary>Windows PowerShell</summary>

```powershell
python -m pip install -r requirements-dev.txt
npm ci
```

</details>

<details>
<summary>Windows cmd</summary>

```bat
python -m pip install -r requirements-dev.txt
npm ci
```

</details>

<details>
<summary>macOS</summary>

```bash
python -m pip install -r requirements-dev.txt
npm ci
```

</details>

<details>
<summary>Linux</summary>

```bash
python -m pip install -r requirements-dev.txt
npm ci
```

</details>

`requirements-dev.txt` installs the editable package with the Python development
extra, including pytest, Black, djlint, mypy, and Ruff. Python requirements
files use [constraints.txt](../constraints.txt) to install the tested dependency
resolution. `package-lock.json` pins the npm formatter and linting dependencies:
Prettier, ESLint, Stylelint, and their shared configs.

Python packaging and declared dependencies are configured in [pyproject.toml](../pyproject.toml). Runtime dependencies live in `[project].dependencies`; development tools live in the `dev` optional dependency extra. [requirements.txt](../requirements.txt) is a non-editable pip compatibility wrapper for normal installs, and [requirements-dev.txt](../requirements-dev.txt) is an editable wrapper for contributor installs. Do not duplicate Python dependency names in requirements files.

Contributors can also install the development extra directly with the same constraints:

```powershell
python -m pip install -e .[dev] -c constraints.txt
```

Update [constraints.txt](../constraints.txt) only after changing declared Python dependencies, intentionally refreshing the tested dependency resolution, or preparing a release. Refresh from a clean virtual environment so unrelated local packages are not captured:

```powershell
python -m pip install --upgrade --upgrade-strategy eager -e .[dev]
python -m pip freeze --exclude-editable | Sort-Object > constraints.txt
python -m pip install -r requirements-dev.txt
python -m pip check
.\.venv\Scripts\python.exe -B -m pytest
```

Review the generated file before committing it. Remove packages that are not part of the FinScope runtime or development dependency graph, and keep declared dependency ranges in `pyproject.toml` rather than adding direct dependency names to requirements files.

Pytest, frontend, and formatter tooling also use [pytest.ini](../pytest.ini), [package.json](../package.json), [eslint.config.mjs](../eslint.config.mjs), [stylelint.config.mjs](../stylelint.config.mjs), and [.prettierrc.json](../.prettierrc.json).

## Quality checks

Run the smallest useful pytest marker while iterating, then run the full local
quality gate before broad changes, release work, or pull requests:

<details open>
<summary>Windows PowerShell</summary>

```powershell
.\.venv\Scripts\python.exe -B -m black --check .
.\.venv\Scripts\python.exe -B -m djlint src/finance_app/templates --profile=jinja --lint
.\.venv\Scripts\python.exe -B -m ruff check .
.\.venv\Scripts\python.exe -B -m mypy
npm run lint:frontend
.\.venv\Scripts\python.exe -B -m pytest
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
.venv\Scripts\python.exe -B -m pytest
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
.venv/bin/python -B -m pytest
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
.venv/bin/python -B -m pytest
```

</details>

The GitHub Actions workflow in [.github/workflows/quality.yml](../.github/workflows/quality.yml)
runs the same formatter, linter, type-checker, frontend, and pytest gates. Use
[Testing](testing.md) for marker-specific pytest commands, coverage, and details
about each quality tool.

## Development principles

- Keep feature code modular under [src/finance_app/modules/](../src/finance_app/modules/).
- Keep route handlers thin.
- Put business logic in services, workflows, engines, or presenters.
- Keep SQL in query/repository helpers and use SQLAlchemy Core for production database access.
- Preserve category and tag integrity through taxonomy helpers and repository synchronization.
- Keep financial calculations in exact money types until display or serialization boundaries.
- Add or update tests with behavior changes.
- Do not commit local databases, secrets, uploaded statements, logs, or runtime files.
- Update docs when setup, architecture, schema, workflows, or user-visible behavior changes.

## Documentation map

- [Getting started](getting-started.md): short first-run walkthrough for new users.
- [Tutorial](tutorial.md): practical user workflow and best-practices guide.
- [User guide](user-guide.md): concise feature reference for day-to-day users.
- [Architecture](architecture.md): feature-module layering and runtime boundaries.
- [Database](database.md): backend selection, schema responsibilities, table documentation, and generated artifacts.
- [Testing](testing.md): pytest markers, command reference, suite layout, and quality gates.
- [Processing activity](background-jobs.md): queue behavior, state lifecycle, cancellation, and undo behavior.
- [Authentication and authorization](authentication.md): owner bootstrap, roles, password handling, settings permissions, and deployment notes.
- [Categories, tags, and categorization](taxonomy.md): category/tag storage, seed data, synchronization, and categorization flow.
- [Troubleshooting](troubleshooting.md): common local setup and runtime issues.

## Contribution baseline

1. Keep changes consistent with the modular architecture.
2. Add tests at the right layer.
3. Run the relevant marker subset before pushing.
4. Run the full suite before releases.
5. Update the README or docs when setup, architecture, or workflows change.
