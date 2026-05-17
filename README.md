# <img src="src/finance_app/static/img/favicon.png" alt="Logo" width="75"/> FinScope

FinScope is a local personal finance web application for importing statement files, categorizing transactions, reviewing unknown merchants, managing category rules, and analyzing spending over time.

FinScope is a single-user Flask application with SQLite persistence, background jobs, feature modules, and a layered pytest suite.

> 
> **DISCLAIMER:** *This application has been vibe coded using OpenAI's Codex: source code, documentation, tests. The content, code quality, and design has been manually reviewed, but code smells and other problems may still be present. Use with care.*


> Project owner: Eugene Syriani  
> Current deployment target: local desktop  
> License: [GNU General Public License v3.0](LICENSE) (`GPL-3.0-only`)  
> Last modification: 2026-05-17

![FinScope splash screen](docs/img/splash.png)

[Quick start](#quick-start) | [Features](#main-features) | [Setup](#setup) | [Run](#running-the-app) | [Workflows](#typical-user-workflows) | [Docs](#project-docs) | [Testing](#testing) | [License](#license)

## Quick start

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item src\finance_app\config.example.ini src\finance_app\config.ini
.\.venv\Scripts\python.exe -B src\finance_app\app.py
```

Open:

```text
http://127.0.0.1:5000
```

For another port:

```powershell
$env:FINANCE_PORT = "5001"
.\.venv\Scripts\python.exe -B src\finance_app\app.py
```

## Main features

- Import CSV statements into local SQLite storage.
- Capture PDF statement text for review.
- Deduplicate transactions with account-aware fingerprints.
- Categorize transactions with rules, historical matches, manual edits, optional LLM assistance, and persisted decision evidence.
- Manage categories and tags from the admin taxonomy page.
- Preview, import, export, apply, and undo supported rules and jobs.
- Retry or reprocess stored statement imports without uploading the same file again.
- Use a dark-first interface with a light mode available from Settings.
- Switch the user interface language between English and French from Settings.
- Analyze spending with dashboards, calendars, recurring activity, durable merchant identities, categories, and comparison views.
- Review unknown merchants and unresolved transactions.

## Screenshots

| Dashboard | Transactions |
| --- | --- |
| ![Dashboard page](docs/img/dashboard.png) | ![Transactions page](docs/img/transactions.png) |

| Calendar | Taxonomy admin |
| --- | --- |
| ![Calendar page](docs/img/calendar.png) | ![Taxonomy admin page](docs/img/taxonomy.png) |

## Tech stack

<ul style="list-style: none; padding-left: 0;">
  <li><img src="https://cdn.jsdelivr.net/gh/devicons/devicon/icons/python/python-original.svg" width="18" /> Python 3.10+ <em>(developed and tested with Python 3.11.9)</em></li>
  <li><img src="https://cdn.jsdelivr.net/gh/devicons/devicon/icons/flask/flask-original.svg" width="18" /> Flask 3.1.3</li>
  <li><img src="https://cdn.jsdelivr.net/gh/devicons/devicon/icons/sqlite/sqlite-original.svg" width="18" /> SQLite 3.45.1</li>
  <li><img src="https://upload.wikimedia.org/wikipedia/commons/d/d7/SQLAlchemy.svg" width="18" /> SQLAlchemy Core 2.0.49</li>
  <li><img src="https://cdn.simpleicons.org/jinja/black" width="18" /> Jinja templates 3.1.6</li>
  <li><img src="https://cdn.jsdelivr.net/gh/devicons/devicon/icons/bootstrap/bootstrap-original.svg" width="18" /> Bootstrap 5.3.3</li>
  <li><img src="https://cdn.simpleicons.org/apacheecharts/AA344D" width="18" /> ECharts 5.6.0</li>
  <li><img src="https://cdn.simpleicons.org/pytest/0A9EDC" width="18" /> pytest 9.0.3</li>
  <li><img src="https://upload.wikimedia.org/wikipedia/commons/6/60/Adobe_Acrobat_Reader_icon_(2020).svg" width="18" /> pypdf 6.10.2</li>
  <li><img src="https://upload.wikimedia.org/wikipedia/commons/6/66/OpenAI_logo_2025_%28symbol%29.svg" width="18" /> OpenAI SDK 2.33.0 <em>(optional)</em></li>
</ul>

## Repository layout

```text
src/
  finance_app/
    __init__.py          Flask application factory.
    app.py               Application entry point.
    core/                Configuration, constants, SQLAlchemy query helpers, CSRF, filters.
    database/            SQLAlchemy lifecycle, metadata, and initialization seeds.
    background/          In-memory background job runner and undo orchestration.
    modules/             Feature modules.
    templates/           Jinja templates.
    static/              CSS, JavaScript, image assets, and vendored browser libraries.
    translations/        JSON translation catalogs for user interface text.
tests/                   Unit, integration, route, and smoke tests.
docs/                    Deeper project documentation.
runtime/                 Local runtime data, including the SQLite database.
```

## Setup

Create a virtual environment and install dependencies.

PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

cmd.exe:

```bat
python -m venv .venv
.venv\Scripts\activate.bat
python -m pip install -r requirements.txt
```

macOS/Linux:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

Copy the example configuration for local development:

```powershell
Copy-Item src\finance_app\config.example.ini src\finance_app\config.ini
```

Common settings:

| Setting | Environment variable | Purpose |
| --- | --- | --- |
| `app.secret_key` | `FINANCE_SECRET_KEY` | Flask session signing key. |
| `database.url` | `FINANCE_DATABASE_URL` | SQLAlchemy database URL used by the runtime database layer. Leave blank to derive a SQLite URL from `database.path`. |
| `database.path` | `FINANCE_DB_PATH` | SQLite database path. |
| `server.host` | `FINANCE_HOST` | Flask bind host. |
| `server.port` | `FINANCE_PORT` | Flask port. |
| `server.debug` | `FINANCE_DEBUG` | Debug mode. Keep false outside development. |
| `uploads.allowed_extensions` | `FINANCE_ALLOWED_EXTENSIONS` | Supported statement upload extensions. |
| `api_keys.openai_api_key` | `OPENAI_API_KEY` | Enables optional LLM categorization. |
| `setting_defaults.categorization_model` | `FINANCE_DEFAULT_CATEGORIZATION_MODEL` | Default LLM model name. |

FinScope loads `src/finance_app/config.example.ini`, overlays `src/finance_app/config.ini` when present, then applies environment variable overrides.

### Database selection

Choose the active database with the SQLAlchemy URL in `database.url`:

```ini
[database]
url =
path = ../../runtime/finance.db
```

Selection priority:

1. `FINANCE_DATABASE_URL`, when set.
2. `database.url` in `src/finance_app/config.ini`, when non-empty.
3. A generated SQLite URL from the configured database path.

The database path used for the generated SQLite URL is chosen in this order:

1. `FINANCE_DB_PATH`, when set.
2. `database.path` in `src/finance_app/config.ini`, when present.
3. `database.path` in `src/finance_app/config.example.ini`.

Leave `database.url` blank for the default SQLite database. Set it to a SQLAlchemy URL such as `sqlite:///D:/path/to/finance.db` or `mysql+pymysql://user:password@127.0.0.1:3306/finscope` to select that database. When a non-SQLite URL is active, `database.path` is not the active database; it is only used as the fallback path if the URL is later removed.

Interface language is a runtime setting stored in SQLite and managed from Settings. English source strings are the canonical message ids; French translations live in `src/finance_app/translations/fr.json`.

## Running the app

From the repository root:

```powershell
.\.venv\Scripts\python.exe -B src\finance_app\app.py
```

`src/finance_app/app.py` initializes the database before starting Flask. By default, FinScope uses `runtime/finance.db`.

Imported transactions normally keep their original statement descriptions for auditability. Ledger uploads create transaction rows; enrichment uploads, such as Interac e-Transfer history, update matched rows without adding duplicate ledger activity. Merchant grouping is persisted separately through `merchants` and `merchant_aliases`, which gives recurring activity, merchant filters, categorization rules, and analytics a stable merchant identity. Rules and recurring patterns can still remain keyword-fuzzy when no merchant ID is stored.

Statement import type and account reporting role are intentionally separate. The statement import type controls the parser and whether the upload creates ledger rows or enriches existing rows. The account reporting role controls how the account behaves in reports, with roles such as checking, savings, or credit card. Credit card statements are treated as ledger sources because they contain purchase-level detail, while the matching checking-account card payments are marked as payments/transfers so reports do not double-count spending.

## Typical user workflows

### Upload a statement

1. Go to Upload.
2. Choose an account name. This is the account that will own the imported or enriched rows.
3. Choose the statement import type. This controls the file parser and import behavior.
4. Review the account reporting role. FinScope suggests a role from the statement import type; usually keep the suggestion unless the account should behave differently in reports.
5. For Interac e-Transfer history, import the matching checking statements first. Interac history is enrichment-only: it matches generic checking rows such as `Envoi - VFC` or `Recept - VFC` and replaces them with the real counterparty. Rows are ignored until the matching checking transaction exists. Leave Interac direction on Auto-detect unless the file uses generic columns and all amounts are positive. In that case, choose Sent or Received so FinScope can sign the amounts before matching existing checking rows.
6. For credit cards, optionally enter the checking or savings account that pays the card.
7. Upload a CSV or PDF.
8. Use Transactions, Review, and Jobs to inspect the result.
9. If import processing fails, use Upload > Uploaded statements to retry from stored statement text.
10. If parser behavior or statement settings changed, use Reprocess to clear that statement's imported transactions and import them again.

Statement import type examples:

| Statement import type | Typical account reporting role | Import behavior |
| --- | --- | --- |
| Checking account | Checking account | Adds checking transactions as ledger rows. |
| Credit card | Credit card | Adds card purchases as ledger rows and marks card payments as payments/transfers. |
| Interac e-Transfer | Checking account | Matches existing checking e-transfer rows and enriches their descriptions without adding duplicate ledger rows. |

Interac e-Transfer history uses the Interac e-Transfer statement import type. It does not add duplicate ledger rows; it matches existing checking-account e-transfer rows and replaces the generic bank description with the actual counterparty when the match is unambiguous. Always import the matching checking statements first, then import Interac Sent and Interac Received history. In Interac import reports, skipped rows are ambiguous matches, while ignored rows are cancelled, non-deposited, or do not yet have a matching checking ledger transaction.

Credit card uploads create purchase-level ledger rows. Payment rows from the card statement and matched payment rows from the funding account are kept visible as payments/transfers but excluded from spending and income totals.

Tagged reimbursement credits remain categorized as Transfers. When a dashboard or comparison cash-flow view is filtered by included tags, matching negative transfer rows are included as credits so reimbursable travel, work, insurance, or shared-expense tags can show a net amount after repayment.

### Create a rule

1. Go to Rules.
2. Enter a merchant keyword, category, optional amount bounds, and optional tags.
3. Preview matches.
4. Save and apply the rule.

Rules created from the Rules page are keyword-fuzzy by default. Rules saved while editing a transaction are merchant-bound when that transaction has a durable merchant identity. Rules CSV import/export includes an optional `merchant_name` column for merchant-bound rules.

Fuzzy keywords are normalized substring matches. If two fuzzy rules both match, the more specific longer keyword wins when the other priority fields are equal. For example, `VIREMENT INTERAC` applies before `VIREMENT` for a transaction normalized as `VIREMENT INTERAC 2`.

Rule sources are persisted as `manual`, `automatic`, or `default`. The UI labels these as Manual, Auto/Automatic, and Default.

### Review unknown transactions

1. Go to Review.
2. Review grouped merchants or individual rows.
3. Open a group and use Show all transactions when only some rows should be categorized differently.
4. Assign categories and tags.
5. Save reusable mappings as rules when appropriate.

### Manage taxonomy

1. Go to Admin > Taxonomy.
2. Create or edit categories and tags.
3. Delete unused taxonomy values when they are no longer referenced.

## Project docs

- [Architecture](docs/architecture.md): module structure, layering expectations, and data model overview.
- [Database](docs/database.md): SQLite schema notes and the interactive DBSchema export.
- [Taxonomy and categorization](docs/taxonomy.md): category/tag storage, seed data, synchronization, and categorization flow.
- [Background jobs](docs/background-jobs.md): queued workflows, state lifecycle, undo behavior, and current limitations.
- [Testing](docs/testing.md): pytest markers, suite layout, and recommended execution patterns.
- [Troubleshooting](docs/troubleshooting.md): common local setup and runtime issues.

## Testing

Run the full suite:

```powershell
$env:PYTHONDONTWRITEBYTECODE = "1"
.\.venv\Scripts\python.exe -B -m pytest
```

Run a layer:

```powershell
.\.venv\Scripts\python.exe -B -m pytest -m unit
.\.venv\Scripts\python.exe -B -m pytest -m integration
.\.venv\Scripts\python.exe -B -m pytest -m route
.\.venv\Scripts\python.exe -B -m pytest -m smoke
```

Run a coverage report:

```powershell
.\.venv\Scripts\python.exe -B -m pytest --cov=finance_app --cov-report=term-missing
```

See [testing](docs/testing.md) and [tests/README.md](tests/README.md) for marker details and suite structure.

## Development guidelines

- Keep feature code modular under `src/finance_app/modules/<feature>/`.
- Keep route handlers thin.
- Put business logic in services, workflows, engines, or presenters.
- Keep SQL in query/repository helpers.
- Preserve category and tag integrity through taxonomy helpers and repository synchronization.
- Add or update tests with behavior changes.
- Do not commit local databases, secrets, uploaded statements, logs, or runtime files.
- Run tests with `-B` or `PYTHONDONTWRITEBYTECODE=1` to avoid bytecode artifacts.

## Privacy and security

FinScope handles financial data. Treat the local database and uploaded content as sensitive.

Current security model:

- Single-user local application.
- No built-in user accounts or authentication.
- SQLite database stored on local disk.
- No encryption at rest implemented by FinScope.
- CSRF protection is enabled for mutating Flask routes.
- OpenAI integration is optional and only active when an API key is configured.

Operational recommendations:

- Keep `FINANCE_SECRET_KEY` private.
- Keep `OPENAI_API_KEY` out of source control.
- Store `runtime/finance.db` in a protected location.
- Back up the SQLite database regularly.
- Do not run with debug mode enabled on a shared network.
- Review data-sharing implications before enabling LLM categorization.

## License

This project is licensed under the GNU General Public License v3.0 only. See [LICENSE](LICENSE) for the full license text.

Unless a file states otherwise, source files in this repository are distributed under `GPL-3.0-only`.

## Known limitations

- Single-user app. FinScope is not designed for concurrent multi-user hosting.
- Background job state is in memory and is lost on process restart.
- PDF text is captured, but automatic PDF transaction parsing is not enabled.
- No bank synchronization.
- No built-in authentication.
- No built-in encryption at rest.
- SQLite is appropriate for local use, not high-concurrency workloads.

## Roadmap

- Authentication for shared deployments.
- Encrypted database or encrypted backups.
- More statement parsers.
- PDF transaction extraction.
- Support more LLM providers using https://openrouter.ai/

## Contributing

TODO: add contribution policy.

Baseline expectations:

1. Keep changes consistent with the modular architecture.
2. Add tests at the right layer.
3. Run the relevant marker subset before pushing.
4. Run the full suite before releases.
5. Update the README or docs when setup, architecture, or workflows change.
