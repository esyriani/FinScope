# <img src="src/finance_app/static/img/logo.png" alt="Logo" width="75"/> FinScope

FinScope is a local personal finance web application for importing statement files, categorizing transactions, reviewing unknown merchants, managing category rules, and analyzing spending over time.

FinScope is a single-tenant Flask application with owner-managed user access, shared finance data, SQLite or MySQL persistence, background jobs, feature modules, and a layered pytest suite.

> 
> **DISCLAIMER:** *This application has been vibe coded using OpenAI's Codex: source code, documentation, tests. The content, code quality, and design has been manually reviewed, but code smells and other problems may still be present. Use with care.*


> Project owner: Eugene Syriani  
> Current deployment target: local desktop  
> License: [GNU General Public License v3.0](LICENSE) (`GPL-3.0-only`)  
> Last modification: 2026-05-28

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

On first run, create the owner account in the bootstrap page. The owner manages editor and viewer users from Users.

For another port:

```powershell
$env:FINANCE_PORT = "5001"
.\.venv\Scripts\python.exe -B src\finance_app\app.py
```

## Main features

- Import CSV statements into SQLite or MySQL storage.
- Deduplicate transactions with account-aware fingerprints.
- Categorize transactions with rules, historical matches, manual edits, optional LLM assistance, and persisted decision evidence.
- Control AI categorization separately from imports, including manual reruns for remaining unknown transactions.
- Manage categories and tags from the admin taxonomy page.
- Audit overlapping category rules, preview rule changes, import, export, apply, and undo supported rules and jobs.
- Retry or reprocess stored statement imports without uploading the same file again.
- Use a dark-first interface with a light mode available from Settings.
- Switch the user interface language between English and French from Settings.
- Sign in with owner-managed users and role-based access for owner, editor, and viewer workflows.
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
  <li>tzdata for IANA timezone display on Windows and minimal hosts</li>
  <li><img src="https://cdn.jsdelivr.net/gh/devicons/devicon/icons/flask/flask-original.svg" width="18" /> Flask 3.1.3</li>
  <li>Flask-Login 0.6.3</li>
  <li><img src="https://cdn.jsdelivr.net/gh/devicons/devicon/icons/sqlite/sqlite-original.svg" width="18" /> SQLite 3.31+ <em>(developed and tested with SQLite 3.45.1)</em></li>
  <li><img src="https://cdn.jsdelivr.net/gh/devicons/devicon/icons/mysql/mysql-original.svg" width="18" /> MySQL 8.0.16+ with PyMySQL 1.1.3 <em>(MariaDB-compatible)</em></li>
  <li><img src="https://upload.wikimedia.org/wikipedia/commons/d/d7/SQLAlchemy.svg" width="18" /> SQLAlchemy Core 2.0.49</li>
  <li><img src="https://cdn.simpleicons.org/jinja/black" width="18" /> Jinja templates 3.1.6</li>
  <li><img src="https://cdn.jsdelivr.net/gh/devicons/devicon/icons/bootstrap/bootstrap-original.svg" width="18" /> Bootstrap 5.3.3</li>
  <li><img src="https://cdn.simpleicons.org/apacheecharts/AA344D" width="18" /> ECharts 5.6.0</li>
  <li><img src="https://cdn.simpleicons.org/pytest/0A9EDC" width="18" /> pytest 9.0.3 with pytest-xdist</li>
  <li><img src="https://upload.wikimedia.org/wikipedia/commons/6/66/OpenAI_logo_2025_%28symbol%29.svg" width="18" /> OpenAI SDK 2.33.0 <em>(optional)</em></li>
</ul>

## Repository layout

```text
src/
  finance_app/
    __init__.py          Flask application factory.
    app.py               Application entry point.
    core/                Configuration, constants, SQLAlchemy query helpers, CSRF, filters.
    database/            SQLAlchemy lifecycle, metadata, schema validation, and initialization seeds.
    background/          In-memory background job runner and undo orchestration.
    modules/             Feature modules, including auth, settings, upload, rules, review, and reporting.
    templates/           Jinja templates.
    static/              CSS, JavaScript, image assets, and vendored browser libraries.
    translations/        JSON translation catalogs for user interface text.
tests/                   Unit, integration, route, smoke, and shared support helpers.
docs/                    Deeper project documentation.
runtime/                 Local runtime data, including the default SQLite database.
```

## Setup

Create a virtual environment and install dependencies.

PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

For local development and code-quality checks, install the developer tools:

```powershell
python -m pip install -r requirements-dev.txt
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
| `app.secret_key` | `FINANCE_SECRET_KEY` | Flask session signing key. The bundled `dev-secret-key` is only accepted for debug or loopback local runs. |
| `app.timezone` | `FINANCE_TIMEZONE` | IANA timezone used for displaying UTC timestamps, such as `America/Toronto`. |
| `app.currency_symbol` | `FINANCE_CURRENCY_SYMBOL` | Currency symbol used by Python and template money formatting. |
| `app.secure_cookies` | `FINANCE_SECURE_COOKIES` | Whether session and remember cookies require HTTPS. Leave blank to enable secure cookies for non-local hosts and allow plain HTTP on loopback. |
| `database.url` | `FINANCE_DATABASE_URL` | SQLAlchemy database URL used by the runtime database layer. Leave blank to derive a SQLite URL from `database.path`. |
| `database.path` | `FINANCE_DB_PATH` | SQLite database path. |
| `server.host` | `FINANCE_HOST` | Flask bind host. |
| `server.port` | `FINANCE_PORT` | Flask port. |
| `server.debug` | `FINANCE_DEBUG` | Debug mode. Keep false outside development. |
| `uploads.allowed_extensions` | `FINANCE_ALLOWED_EXTENSIONS` | Supported statement upload extensions. CSV is the only supported statement format for now. |
| `api_keys.openai_api_key` | `OPENAI_API_KEY` | Enables optional LLM categorization. |
| `setting_defaults.comparison_insight_card_limit` | `FINANCE_DEFAULT_COMPARISON_INSIGHT_CARD_LIMIT` | Default number of ranked insight cards shown on the comparison page. |
| `setting_defaults.categorization_model` | `FINANCE_DEFAULT_CATEGORIZATION_MODEL` | Default LLM model name. |
| `setting_defaults.llm_confidence_threshold` | `FINANCE_DEFAULT_LLM_CONFIDENCE_THRESHOLD` | Minimum LLM confidence for automatic rule creation from a no-review result. |
| `setting_defaults.llm_review_threshold` | `FINANCE_DEFAULT_LLM_REVIEW_THRESHOLD` | Minimum LLM confidence for keeping a best-fit category as a review item instead of UNKNOWN. |
| `setting_defaults.verify_threshold` | `FINANCE_DEFAULT_VERIFY_THRESHOLD` | Minimum confidence for an LLM category to clear review automatically. |
| `setting_defaults.transaction_ai_rerun_enabled` | `FINANCE_DEFAULT_TRANSACTION_AI_RERUN_ENABLED` | Default visibility for the one-transaction Suggest category action. |
| `setting_defaults.rule_audit_transaction_limit` | `FINANCE_DEFAULT_RULE_AUDIT_TRANSACTION_LIMIT` | Maximum newest historical transactions analyzed by Rule audit before the limited-audit notice appears. |

FinScope loads `src/finance_app/config.example.ini`, overlays `src/finance_app/config.ini` when present, then applies environment variable overrides.

### Database selection

Choose the active database with the SQLAlchemy URL in `database.url`:

```ini
[database]
url =
path = ../../runtime/finescope.db
```

Selection priority:

1. `FINANCE_DATABASE_URL`, when set.
2. `database.url` in `src/finance_app/config.ini`, when non-empty.
3. A generated SQLite URL from the configured database path.

The database path used for the generated SQLite URL is chosen in this order:

1. `FINANCE_DB_PATH`, when set.
2. `database.path` in `src/finance_app/config.ini`, when present.
3. `database.path` in `src/finance_app/config.example.ini`.

Leave `database.url` blank for the default SQLite database. Set it to a SQLAlchemy URL such as `sqlite:///D:/path/to/finescope.db` or `mysql+pymysql://user:password@127.0.0.1:3306/finscope` to select that database. When a non-SQLite URL is active, `database.path` is not the active database; it is only used as the fallback path if the URL is later removed.

Supported database backends:

| Backend | SQLAlchemy URL | Notes |
| --- | --- | --- |
| SQLite 3.31+ | `sqlite:///D:/path/to/finescope.db` | Default local backend. The current development environment uses SQLite 3.45.1. |
| MySQL 8.0.16+ | `mysql+pymysql://user:password@host:3306/finscope` | Fully supported through SQLAlchemy Core and PyMySQL 1.1.3. Compatible MariaDB servers use the same URL form. |

Interface language is a user-bound runtime setting stored in `user_settings` and managed from Settings. English source strings are the canonical message ids; French translations live in `src/finance_app/translations/fr.json`.

## Running the app

From the repository root:

```powershell
.\.venv\Scripts\python.exe -B src\finance_app\app.py
```

`src/finance_app/app.py` initializes the database before starting Flask: empty databases are created from Core metadata, existing FinScope databases are validated against the current schema, and runtime defaults are seeded. By default, FinScope uses `runtime/finescope.db`.

Imported transactions normally keep their original statement descriptions for auditability. Ledger uploads create transaction rows; enrichment uploads, such as Interac e-Transfer history, update matched rows without adding duplicate ledger activity. Merchant grouping is persisted separately through `merchants` and `merchant_aliases`, which gives recurring activity, merchant filters, categorization rules, and analytics a stable merchant identity. Rules and recurring patterns can still remain keyword-fuzzy when no merchant ID is stored.

The first request to a database without an owner redirects to `/auth/bootstrap`. Create exactly one owner account there, then use Users to create editor and viewer accounts. FinScope generates a temporary password for each owner-created user; provide it manually and the user must change it on first login. All users in one deployment access the same finance dataset; FinScope does not create workspaces, tenant IDs, organizations, or per-user databases. Use a separate deployment and database for a separate finance workspace.

Rule audit is available from Rules. It analyzes the shared finance dataset for overlapping rules, category conflicts, tag differences, shadowed rules, stale or unused rules, and specificity warnings. Rule audit is preview-first: create, edit, delete, approve, apply-all, apply-where-winner, force-apply, and import flows render an impact preview before mutating rules or transactions. The audit uses the same rule matcher as imports and rule application, and the `rule_audit_transaction_limit` setting caps the newest historical transactions analyzed on large datasets.

Statement import type and account reporting role are intentionally separate. The statement import type controls the parser and whether the upload creates ledger rows or enriches existing rows. The account reporting role controls how the account behaves in reports, with roles such as checking, savings, or credit card. Credit card statements are treated as ledger sources because they contain purchase-level detail, while the matching checking-account card payments are marked as payments/transfers so reports do not double-count spending.

## Typical user workflows

### Upload a statement

1. Go to Upload.
2. Choose an account name. This is the account that will own the imported or enriched rows.
3. Choose the statement import type. This controls the file parser and import behavior.
4. Review the account reporting role. FinScope suggests a role from the statement import type; usually keep the suggestion unless the account should behave differently in reports.
5. For Interac e-Transfer history, import the matching checking statements first. Interac history is enrichment-only: it matches generic checking rows such as `Envoi - VFC` or `Recept - VFC` and replaces them with the real counterparty. Rows are ignored until the matching checking transaction exists. Leave Interac direction on Auto-detect unless the file uses generic columns and all amounts are positive. In that case, choose Sent or Received so FinScope can sign the amounts before matching existing checking rows.
6. For credit cards, optionally enter the checking or savings account that pays the card.
7. Choose a CSV and review the preview modal. If slash dates are ambiguous, choose `MM/DD/YYYY` or `DD/MM/YYYY` before confirming.
8. Confirm the import, then use Transactions, Review, and Jobs to inspect the result.
9. If import processing fails, use Upload > Uploaded statements to retry from stored statement text.
10. If parser behavior or statement settings changed, use Reprocess to clear that statement's imported transactions and import them again.
11. If AI categorization is paused or was interrupted, use Jobs > Run AI on unknowns or Upload > Uploaded statements > Run AI to rerun categorization for remaining unknown transactions.

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

Rule sources are persisted as `manual` or `automatic`. The UI labels these as Manual and Auto/Automatic.

### Review unknown transactions

1. Go to Review.
2. Review grouped merchants or individual rows.
3. Open a group and use Show all transactions when only some rows should be categorized differently.
4. Assign categories and tags.
5. Save reusable mappings as rules when appropriate.

### Control AI categorization

AI categorization runs in a separate background queue so OpenAI timeouts do not block statement imports, rule jobs, or review jobs. Automatic AI categorization after imports is off by default; owners can opt in from Settings > Categorization. Unknown transactions remain available for manual reruns from Jobs or from an individual uploaded statement.

External LLM prompts are privacy-minimized. FinScope does not send raw transaction descriptions, exact dates, exact amounts, account names, account types, account IDs, or similar-transaction examples. The prompt uses normalized merchant text, coarse amount direction and magnitude, transaction kind, taxonomy data, and compact category evidence summaries. The static system-prompt policy is stored as structured JSON in `src/finance_app/modules/categories/llm_system_prompt.json` so prompt changes can be reviewed separately from request code.

Use Jobs to run AI on all active unknown transactions, cancel a queued or running AI job, or clear queued AI jobs. Running AI jobs stop cooperatively after the current batch. Manual reruns only target active transactions whose category is still unknown, so they do not overwrite manually reviewed or already categorized rows.

AI uses three thresholds. `llm_review_threshold` keeps a best-fit category as review-required instead of `UNKNOWN`; `verify_threshold` clears review automatically only for high-confidence results; `llm_confidence_threshold` controls when no-review AI results may create reusable automatic rules.

For focused review, Settings > Categorization can show a Suggest category action on transaction rows. This synchronous action previews an LLM suggestion for one transaction, shows confidence, evidence, and metadata, then lets the user explicitly apply it to the row or apply it and create a reusable rule. Its default visibility is controlled by `setting_defaults.transaction_ai_rerun_enabled`.

### Manage taxonomy

1. Go to Admin > Taxonomy.
2. Create or edit categories and tags.
3. Export or import the taxonomy as YAML when moving category and tag metadata between databases.
4. Delete unused taxonomy values when they are no longer referenced.

## Project docs

- [Architecture](docs/architecture.md): module structure, layering expectations, and data model overview.
- [Authentication](docs/authentication.md): owner bootstrap, roles, password handling, settings permissions, and deployment notes.
- [Database](docs/database.md): SQLite/MySQL schema notes and the interactive DBSchema export.
- [Taxonomy and categorization](docs/taxonomy.md): category/tag storage, seed data, synchronization, and categorization flow.
- [Background jobs](docs/background-jobs.md): queued workflows, state lifecycle, undo behavior, and current limitations.
- [Testing](docs/testing.md): pytest markers, suite layout, and recommended execution patterns.
- [Troubleshooting](docs/troubleshooting.md): common local setup and runtime issues.

## Testing

Run the full suite. The default pytest configuration enforces strict markers,
warnings as errors, parallel execution, collection from `tests/`, and no
coverage run:

```powershell
$env:PYTHONDONTWRITEBYTECODE = "1"
.\.venv\Scripts\python.exe -B -m pytest
```

Run a layer during local iteration:

```powershell
.\.venv\Scripts\python.exe -B -m pytest -m unit
.\.venv\Scripts\python.exe -B -m pytest -m integration
.\.venv\Scripts\python.exe -B -m pytest -m route
.\.venv\Scripts\python.exe -B -m pytest -m smoke
```

See [testing](docs/testing.md) and [tests/README.md](tests/README.md) for marker details and suite structure.

Run code-quality checks after installing `requirements-dev.txt`:

```powershell
.\.venv\Scripts\python.exe -B -m black --check .
.\.venv\Scripts\python.exe -B -m ruff check `
  sitecustomize.py `
  src\finance_app\core\query.py `
  src\finance_app\modules\merchants\normalization.py
.\.venv\Scripts\python.exe -B -m mypy
```

Type checking is intentionally gradual. The current mypy target list lives in
`pyproject.toml`; expand it as modules gain annotations and clean boundaries.
Ruff is still applied first to the pilot files while the lint baseline is
cleaned up.

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

- Single-tenant authenticated application: all authenticated users share the same finance database.
- One owner account manages editor and viewer access. The database enforces the single-owner role and case-insensitive username uniqueness. The owner cannot be deactivated; ownership can be handed off to another active user through a confirmation modal, after which the previous owner becomes a viewer.
- Passwords are stored with Werkzeug `scrypt` hashes; plaintext passwords are never stored.
- Login failures are tracked and temporarily locked after repeated failures.
- SQLite and MySQL are fully supported. SQLite stores the database on local disk by default; MySQL is selected through `database.url`.
- No encryption at rest implemented by FinScope.
- CSRF protection is enabled for mutating Flask routes.
- Session cookies are HttpOnly and SameSite=Lax; secure cookies are enabled when debug mode is off.
- OpenAI integration is optional and only active when an API key is configured and an owner explicitly runs or enables AI categorization.

Operational recommendations:

- Keep `FINANCE_SECRET_KEY` private.
- Change the bootstrap owner password after setup if it was created in a shared environment.
- Keep `OPENAI_API_KEY` out of source control.
- Store `runtime/finescope.db`, MySQL credentials, and database backups in protected locations.
- Back up the active database regularly.
- Do not run with debug mode enabled on a shared network.
- Review data-sharing implications before running or enabling LLM categorization.

## License

This project is licensed under the GNU General Public License v3.0 only. See [LICENSE](LICENSE) for the full license text.

Unless a file states otherwise, source files in this repository are distributed under `GPL-3.0-only`.

## Known limitations

- Single-tenant app. FinScope supports multiple authenticated users for one shared finance dataset, not multi-tenant hosting.
- Background job state is in memory and is lost on process restart.
- No bank synchronization.
- No built-in encryption at rest.
- SQLite is appropriate for local use, not high-concurrency workloads. Use MySQL when the deployment needs stronger server-side concurrency and backup tooling.

## Roadmap

- More detailed audit reporting for owner-managed user activity.
- Encrypted database or encrypted backups.
- More statement parsers.
- Support more LLM providers using https://openrouter.ai/

## Contributing

TODO: add contribution policy.

Baseline expectations:

1. Keep changes consistent with the modular architecture.
2. Add tests at the right layer.
3. Run the relevant marker subset before pushing.
4. Run the full suite before releases.
5. Update the README or docs when setup, architecture, or workflows change.
