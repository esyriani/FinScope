# User Guide

This guide covers local setup and day-to-day FinScope workflows for end users. Developer setup, code quality, architecture, and testing details live in the [Developer Guide](developer-guide.md), [Architecture](architecture.md), [Database](database.md), [Testing](testing.md), and [Background Jobs](background-jobs.md) docs.

## First Run

Install the runtime dependencies, copy the local configuration file, and start FinScope from the repository root.

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

Open `http://127.0.0.1:5000`. On first run, FinScope redirects to `/auth/bootstrap`; create exactly one owner account there. The owner can create editor and viewer users from Users. See [Authentication](authentication.md) for role and password details.

## Run on Another Port

<details open>
<summary>Windows PowerShell</summary>

```powershell
$env:FINANCE_PORT = "5001"
.\.venv\Scripts\python.exe -B src\finance_app\app.py
```

</details>

<details>
<summary>Windows cmd</summary>

```bat
set "FINANCE_PORT=5001"
.venv\Scripts\python.exe -B src\finance_app\app.py
```

</details>

<details>
<summary>macOS</summary>

```bash
export FINANCE_PORT=5001
.venv/bin/python -B src/finance_app/app.py
```

</details>

<details>
<summary>Linux</summary>

```bash
export FINANCE_PORT=5001
.venv/bin/python -B src/finance_app/app.py
```

</details>

## Configuration

FinScope loads [src/finance_app/config.example.ini](../src/finance_app/config.example.ini), overlays [src/finance_app/config.ini](../src/finance_app/config.ini) when present, then applies environment variable overrides.

Common settings:

| Setting | Environment variable | Purpose |
| --- | --- | --- |
| `app.secret_key` | `FINANCE_SECRET_KEY` | Flask session signing key. The bundled `dev-secret-key` is only accepted for debug or loopback local runs. |
| `app.timezone` | `FINANCE_TIMEZONE` | IANA timezone used for displaying UTC timestamps, such as `America/Toronto`. |
| `app.currency_symbol` | `FINANCE_CURRENCY_SYMBOL` | Currency symbol used by Python and template money formatting. |
| `app.secure_cookies` | `FINANCE_SECURE_COOKIES` | Whether session and remember cookies require HTTPS. |
| `database.url` | `FINANCE_DATABASE_URL` | SQLAlchemy database URL used by the runtime database layer. |
| `database.path` | `FINANCE_DB_PATH` | SQLite database path. |
| `server.host` | `FINANCE_HOST` | Flask bind host. |
| `server.port` | `FINANCE_PORT` | Flask port. |
| `server.debug` | `FINANCE_DEBUG` | Debug mode. Keep false outside development. |
| `api_keys.openai_api_key` | `OPENAI_API_KEY` | Enables optional LLM categorization. |

Interface language is a user-bound runtime setting stored in `user_settings` and managed from Settings. English source strings are the canonical message ids; French translations live in [src/finance_app/translations/fr.json](../src/finance_app/translations/fr.json).

## Database Selection

Leave `database.url` blank for the default SQLite database at [runtime/finescope.db](../runtime/finescope.db). Set `database.url` to a SQLAlchemy URL when you want an explicit SQLite file or MySQL database.

Selection priority:

1. `FINANCE_DATABASE_URL`, when set.
2. `database.url` in [src/finance_app/config.ini](../src/finance_app/config.ini), when non-empty.
3. A generated SQLite URL from the configured database path.

SQLite path priority, used only when no database URL is provided:

1. `FINANCE_DB_PATH`, when set.
2. `database.path` in [src/finance_app/config.ini](../src/finance_app/config.ini), when present.
3. `database.path` in [src/finance_app/config.example.ini](../src/finance_app/config.example.ini).

Supported backends are SQLite 3.31+ and MySQL 8.0.16+ through PyMySQL. See [Database](database.md) for schema and backend details.

## Upload a Statement

1. Go to Upload.
2. Choose an account name. This is the account that will own the imported or enriched rows.
3. Choose the statement import type. This controls the file parser and import behavior.
4. Review the account reporting role. FinScope suggests a role from the statement import type; usually keep the suggestion unless the account should behave differently in reports.
5. For Interac e-Transfer history, import the matching checking statements first. Interac history is enrichment-only: it matches generic checking rows such as `Envoi - VFC` or `Recept - VFC` and replaces them with the real counterparty.
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

Credit card uploads create purchase-level ledger rows. Payment rows from the card statement and matched payment rows from the funding account remain visible as payments/transfers but are excluded from spending and income totals.

Tagged reimbursement credits remain categorized as Transfers. When a dashboard or comparison cash-flow view is filtered by included tags, matching negative transfer rows are included as credits so reimbursable travel, work, insurance, or shared-expense tags can show a net amount after repayment.

## Create a Rule

1. Go to Rules.
2. Enter a merchant keyword, category, optional amount bounds, and optional tags.
3. Preview matches.
4. Save and apply the rule.

Rules created from the Rules page are keyword-fuzzy by default. Rules saved while editing a transaction are merchant-bound when that transaction has a durable merchant identity. Rules CSV import/export includes an optional `merchant_name` column for merchant-bound rules.

Rule audit is available from Rules. It reports overlapping rules, category conflicts, tag differences, shadowed rules, stale or unused rules, and specificity warnings. See [Background Jobs](background-jobs.md) for queued rule-job behavior.

## Review Unknown Transactions

1. Go to Review.
2. Review grouped merchants or individual rows.
3. Open a group and use Show all transactions when only some rows should be categorized differently.
4. Assign categories and tags.
5. Save reusable mappings as rules when appropriate.

## Control AI Categorization

AI categorization runs in a separate background queue so OpenAI timeouts do not block statement imports, rule jobs, or review jobs. Automatic AI categorization after imports is off by default; owners can opt in from Settings > Categorization.

External LLM prompts are privacy-minimized. FinScope does not send raw transaction descriptions, exact dates, exact amounts, account names, account types, account IDs, or similar-transaction examples. The static system-prompt policy is stored in [src/finance_app/modules/categories/llm_system_prompt.json](../src/finance_app/modules/categories/llm_system_prompt.json) so prompt changes can be reviewed separately from request code.

Use Jobs to run AI on all active unknown transactions, cancel a queued or running AI job, or clear queued AI jobs. Manual reruns only target active transactions whose category is still unknown, so they do not overwrite manually reviewed or already categorized rows.

For focused review, Settings > Categorization can show a Suggest category action on transaction rows. This synchronous action previews an LLM suggestion for one transaction, then lets the user explicitly apply it to the row or apply it and create a reusable rule.

## Manage Taxonomy

1. Go to Admin > Taxonomy.
2. Create or edit categories and tags.
3. Export or import the taxonomy as YAML when moving category and tag metadata between databases.
4. Delete unused taxonomy values when they are no longer referenced.

See [Taxonomy and Categorization](taxonomy.md) for category/tag seed data, synchronization, and categorization flow details.

## Privacy and Security

FinScope handles financial data. Treat the local database and uploaded content as sensitive.

- Single-tenant authenticated application: all authenticated users share the same finance database.
- One owner account manages editor and viewer access.
- Passwords are stored with Werkzeug `scrypt` hashes; plaintext passwords are never stored.
- Login failures are tracked and temporarily locked after repeated failures.
- SQLite and MySQL are fully supported. SQLite stores the database on local disk by default; MySQL is selected through `database.url`.
- No encryption at rest is implemented by FinScope.
- CSRF protection is enabled for mutating Flask routes.
- Session cookies are HttpOnly and SameSite=Lax; secure cookies are enabled when debug mode is off.
- OpenAI integration is optional and only active when an API key is configured and an owner explicitly runs or enables AI categorization.

Operational recommendations:

- Keep `FINANCE_SECRET_KEY` private.
- Change the bootstrap owner password after setup if it was created in a shared environment.
- Keep `OPENAI_API_KEY` out of source control.
- Store [runtime/finescope.db](../runtime/finescope.db), MySQL credentials, and database backups in protected locations.
- Back up the active database regularly.
- Do not run with debug mode enabled on a shared network.
- Review data-sharing implications before running or enabling LLM categorization.

## Known Limitations

- FinScope supports multiple authenticated users for one shared finance dataset, not multi-tenant hosting.
- Background job state is in memory and is lost on process restart.
- No bank synchronization.
- No built-in encryption at rest.
- SQLite is appropriate for local use, not high-concurrency workloads. Use MySQL when the deployment needs stronger server-side concurrency and backup tooling.
