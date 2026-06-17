# <img src="src/finance_app/static/img/logo.png" alt="Logo" width="75"/> FinScope

FinScope is a local, single-tenant personal finance web application for importing statement files, categorizing transactions, reviewing unknown merchants, managing rules, and analyzing spending over time.

It is built with Flask, SQLAlchemy Core, SQLite or MySQL, Bootstrap, ECharts, Jinja templates, vanilla JavaScript, JSON translation catalogs, background processing, optional OpenAI AI categorization, and pytest.

> **DISCLAIMER:** *This application has been vibe coded using OpenAI's Codex: source code, documentation, tests. The content, code quality, and design has been manually reviewed, but code smells and other problems may still be present. Use with care.*

> Project owner: Eugene Syriani  
> Current deployment target: local desktop  
> License: [GNU General Public License v3.0](LICENSE) (`GPL-3.0-only`)

![Python](https://img.shields.io/badge/python-3.10%2B-blue)
![Flask](https://img.shields.io/badge/flask-3.1.3-lightgrey)
![Tests](https://img.shields.io/badge/tests-pytest-blue)
![Coverage](docs/coverage.svg)
![Database](https://img.shields.io/badge/database-SQLite%20%7C%20MySQL-blue)
![License](https://img.shields.io/badge/license-GPL--3.0--only-green)

![FinScope splash screen](docs/img/splash.png)

[Quick start](#quick-start) | [Features](#key-features) | [Screenshots](#screenshots) | [Docs](#documentation) | [Testing](#testing) | [License](#license)

## Key features

- Import CSV statements into SQLite or MySQL storage.
- Deduplicate transactions with account-aware fingerprints.
- Categorize transactions with rules, historical matches, manual edits, optional AI assistance, and persisted decision evidence.
- Review grouped unknown merchants and save reusable categorization rules.
- Check rule health and preview rule changes before applying them.
- Track reimbursements by linking repayment credits to covered expenses.
- Manage categories, tags, merchants, statement types, accounts, processing activity, and recurring activity.
- Analyze spending, income, transfers, tags, merchants, calendars, period changes, and year trends.
- Use owner-managed access for owner, editor, and viewer workflows.
- Switch between English and French, with dark and light interface modes.

## Screenshots

| Dashboard | Transactions |
| --- | --- |
| ![Dashboard page](docs/img/dashboard.png) | ![Transactions page](docs/img/transactions.png) |

| Calendar | Categories and tags |
| --- | --- |
| ![Calendar page](docs/img/calendar.png) | ![Categories and tags page](docs/img/taxonomy.png) |

## Quick start

Minimal local setup on Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item config.example.ini config.ini
finscope
```

Open `http://127.0.0.1:5000`, then create the owner account on the bootstrap page. See [Getting started](docs/getting-started.md) for the first-run walkthrough and non-PowerShell command variants.

## Development setup

The quick start installs FinScope and its runtime dependencies from `pyproject.toml` using the tested constraints in `constraints.txt`. For development, install the Python and frontend toolchains too:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
npm ci
```

Development uses Python 3.10+ and Node.js 20+ with npm. See [Developer guide](docs/developer-guide.md) for platform-specific setup, dependency details, and the full local quality gate.

## Documentation

### For users

- [Getting started](docs/getting-started.md): first-run walkthrough from install to the first dashboard review.
- [Tutorial](docs/tutorial.md): practical workflow and best-practices guide for the first month and beyond.
- [User guide](docs/user-guide.md): concise feature reference for each major application area.
- [Settings reference](docs/settings.md): all runtime settings available from the Settings page.
- [Categories, tags, and categorization](docs/taxonomy.md): detailed category, tag, rule, historical, and AI categorization reference.
- [Troubleshooting](docs/troubleshooting.md): problem/solution notes for local setup, imports, dates, duplicate uploads, and AI categorization.

### For developers

- [Developer guide](docs/developer-guide.md): development setup, repository layout, quality checks, and contribution expectations.
- [Architecture](docs/architecture.md): module structure, layering expectations, and runtime boundaries.
- [Database](docs/database.md): SQLite/MySQL backend behavior, schema responsibilities, and generated schema artifacts.
- [Testing](docs/testing.md): pytest markers, suite layout, quality commands, and recommended execution patterns.
- [Processing activity](docs/background-jobs.md): queued workflows, processing state lifecycle, cancellation, and undo behavior.
- [Authentication and authorization](docs/authentication.md): owner bootstrap, roles, password handling, settings permissions, and deployment notes.

## Testing

The default pytest configuration runs the suite in parallel with strict markers, warnings as errors, collection from [tests/](tests/), and no coverage slowdown by default.

```powershell
$env:PYTHONDONTWRITEBYTECODE = "1"
.\.venv\Scripts\python.exe -B -m pytest
```

See [Testing](docs/testing.md) for marker-specific runs, coverage, formatting, linting, type checking, and frontend quality checks.

Before broad changes or release work, run the same local quality gate as GitHub Actions; the command list is in [Developer guide](docs/developer-guide.md#quality-checks).

## License

This project is licensed under the GNU General Public License v3.0 only. See [LICENSE](LICENSE) for the full license text.

Unless a file states otherwise, source files in this repository are distributed under `GPL-3.0-only`.
