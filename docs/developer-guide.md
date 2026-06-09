# Developer Guide

This guide covers repository onboarding and development workflow. Detailed architecture, database, testing, and background-job behavior remains in [Architecture](architecture.md), [Database](database.md), [Testing](testing.md), and [Background Jobs](background-jobs.md).

## Tech Stack

- Python 3.10+; developed and tested with Python 3.11.9.
- Flask 3.1.3 and Flask-Login 0.6.3.
- SQLAlchemy Core 2.0.49 with SQLite 3.31+ or MySQL 8.0.16+ through PyMySQL 1.1.3.
- Jinja templates 3.1.6.
- Bootstrap 5.3.3 and ECharts 5.6.0.
- pytest 9.0.3 with pytest-xdist.
- OpenAI SDK 2.33.0 for optional LLM categorization.
- Node.js 20+ with npm for frontend formatting and linting.

## Repository Layout

- [src/finance_app/__init__.py](../src/finance_app/__init__.py): Flask application factory.
- [src/finance_app/app.py](../src/finance_app/app.py): application entry point.
- [src/finance_app/core/](../src/finance_app/core/): configuration, constants, SQLAlchemy query helpers, CSRF, filters, and i18n helpers.
- [src/finance_app/database/](../src/finance_app/database/): SQLAlchemy lifecycle, metadata, schema validation, and initialization seeds.
- [src/finance_app/background/](../src/finance_app/background/): in-memory background job runner and undo orchestration.
- [src/finance_app/modules/](../src/finance_app/modules/): feature modules, including auth, settings, upload, rules, review, and reporting.
- [src/finance_app/templates/](../src/finance_app/templates/): Jinja templates.
- [src/finance_app/static/](../src/finance_app/static/): CSS, JavaScript, image assets, and vendored browser libraries.
- [src/finance_app/translations/](../src/finance_app/translations/): JSON translation catalogs for user interface text.
- [tests/](../tests/): unit, integration, route, smoke, and shared support helpers.
- [docs/](./): project documentation.
- [runtime/](../runtime/): local runtime data, including the default SQLite database.

## Development Setup

Start with the runtime setup in the [User Guide](user-guide.md), then install the developer dependencies.

<details open>
<summary>Windows PowerShell</summary>

```powershell
python -m pip install -r requirements-dev.txt
npm install
```

</details>

<details>
<summary>Windows cmd</summary>

```bat
python -m pip install -r requirements-dev.txt
npm install
```

</details>

<details>
<summary>macOS</summary>

```bash
python -m pip install -r requirements-dev.txt
npm install
```

</details>

<details>
<summary>Linux</summary>

```bash
python -m pip install -r requirements-dev.txt
npm install
```

</details>

Developer tooling is configured in [pyproject.toml](../pyproject.toml), [pytest.ini](../pytest.ini), [package.json](../package.json), [eslint.config.mjs](../eslint.config.mjs), [stylelint.config.mjs](../stylelint.config.mjs), and [.prettierrc.json](../.prettierrc.json).

## Quality Checks

Use [Testing](testing.md) as the source of truth for pytest markers, coverage, formatter, linter, type-checker, and frontend quality commands. Run the smallest useful marker while iterating, then run the full suite before broad changes or release work.

## Development Principles

- Keep feature code modular under [src/finance_app/modules/](../src/finance_app/modules/).
- Keep route handlers thin.
- Put business logic in services, workflows, engines, or presenters.
- Keep SQL in query/repository helpers and use SQLAlchemy Core for production database access.
- Preserve category and tag integrity through taxonomy helpers and repository synchronization.
- Keep financial calculations in exact money types until display or serialization boundaries.
- Add or update tests with behavior changes.
- Do not commit local databases, secrets, uploaded statements, logs, or runtime files.
- Update docs when setup, architecture, schema, workflows, or user-visible behavior changes.

## Documentation Map

- [User Guide](user-guide.md): end-user setup and workflows.
- [Architecture](architecture.md): feature-module layering and runtime boundaries.
- [Database](database.md): backend selection, schema responsibilities, table documentation, and generated artifacts.
- [Testing](testing.md): pytest markers, command reference, suite layout, and quality gates.
- [Background Jobs](background-jobs.md): queue behavior, state lifecycle, cancellation, and undo behavior.
- [Authentication](authentication.md): owner bootstrap, roles, password handling, settings permissions, and deployment notes.
- [Taxonomy and Categorization](taxonomy.md): category/tag storage, seed data, synchronization, and categorization flow.
- [Troubleshooting](troubleshooting.md): common local setup and runtime issues.

## Contribution Baseline

1. Keep changes consistent with the modular architecture.
2. Add tests at the right layer.
3. Run the relevant marker subset before pushing.
4. Run the full suite before releases.
5. Update the README or docs when setup, architecture, or workflows change.
