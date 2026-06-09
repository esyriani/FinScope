# <img src="src/finance_app/static/img/logo.png" alt="Logo" width="75"/> FinScope

FinScope is a local personal finance web application for importing statement files, categorizing transactions, reviewing unknown merchants, managing category rules, and analyzing spending over time.

FinScope is a single-tenant Flask application with owner-managed user access, shared finance data, SQLite or MySQL persistence, background jobs, feature modules, and a layered pytest suite.

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
- Categorize transactions with rules, historical matches, manual edits, optional LLM assistance, and persisted decision evidence.
- Manage categories, tags, merchant review, category rules, and recurring activity.
- Audit overlapping rules and preview rule changes before applying them.
- Analyze spending with dashboards, calendars, comparison views, categories, tags, and merchant identities.
- Use owner-managed access for owner, editor, and viewer workflows.
- Switch between English and French, with dark and light interface modes.

## Screenshots

| Dashboard | Transactions |
| --- | --- |
| ![Dashboard page](docs/img/dashboard.png) | ![Transactions page](docs/img/transactions.png) |

| Calendar | Taxonomy admin |
| --- | --- |
| ![Calendar page](docs/img/calendar.png) | ![Taxonomy admin page](docs/img/taxonomy.png) |

## Quick start

<details open>
<summary>Windows PowerShell</summary>

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item src\finance_app\config.example.ini src\finance_app\config.ini
.\.venv\Scripts\python.exe -B src\finance_app\app.py
```

</details>

<details>
<summary>Windows cmd</summary>

```bat
python -m venv .venv
.venv\Scripts\activate.bat
python -m pip install -r requirements.txt
copy /Y src\finance_app\config.example.ini src\finance_app\config.ini
.venv\Scripts\python.exe -B src\finance_app\app.py
```

</details>

<details>
<summary>macOS</summary>

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp src/finance_app/config.example.ini src/finance_app/config.ini
.venv/bin/python -B src/finance_app/app.py
```

</details>

<details>
<summary>Linux</summary>

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp src/finance_app/config.example.ini src/finance_app/config.ini
.venv/bin/python -B src/finance_app/app.py
```

</details>

Open `http://127.0.0.1:5000`, then create the owner account on the bootstrap page.

## Documentation

- [User Guide](docs/user-guide.md): first run, configuration, database selection, privacy notes, and day-to-day workflows.
- [Developer Guide](docs/developer-guide.md): development setup, repository layout, quality checks, and contribution expectations.
- [Architecture](docs/architecture.md): module structure, layering expectations, and runtime boundaries.
- [Database](docs/database.md): SQLite/MySQL backend behavior, schema responsibilities, and generated schema artifacts.
- [Testing](docs/testing.md): pytest markers, suite layout, quality commands, and recommended execution patterns.
- [Background Jobs](docs/background-jobs.md): queued workflows, job state lifecycle, cancellation, and undo behavior.
- [Authentication](docs/authentication.md): owner bootstrap, roles, password handling, settings permissions, and deployment notes.
- [Taxonomy and Categorization](docs/taxonomy.md): category/tag storage, seed data, synchronization, and categorization flow.
- [Troubleshooting](docs/troubleshooting.md): common local setup and runtime issues.

## Testing

The default pytest configuration runs the suite in parallel with strict markers, warnings as errors, collection from [tests/](tests/), and no coverage slowdown by default.

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

See [Testing](docs/testing.md) for marker-specific runs, coverage, and frontend quality checks.

## License

This project is licensed under the GNU General Public License v3.0 only. See [LICENSE](LICENSE) for the full license text.

Unless a file states otherwise, source files in this repository are distributed under `GPL-3.0-only`.
