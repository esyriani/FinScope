# User guide

This guide is a concise feature reference for day-to-day FinScope use. Start with [Getting started](getting-started.md) for the first-run walkthrough, then use the [Tutorial](tutorial.md) for workflow advice and best practices.

## Upload

Upload imports CSV statements and manages uploaded statement history.

The upload form collects an account name, statement import type, account reporting role, optional Interac direction, optional paid-from account for credit cards, and the CSV file. The preview modal shows parsed rows, row counts, date range, and date-format controls before import confirmation.

Uploaded statements show import status, added/skipped/ignored counts, unknown counts, AI candidate counts, stored text preview, Retry, Reprocess, and Run AI actions. Retry reruns a failed or incomplete import from stored statement text. Reprocess removes transactions imported from that statement and imports them again from stored statement text.

Interac e-Transfer history is enrichment-only. Import matching checking statements first, then import Interac history for the same account so FinScope can update existing transfer rows with clearer details.

## Transactions

Transactions is the searchable transaction history. It supports period, category, tag, status, ignored-state, and how-categorized filters.

Editors and owners can approve rows, mark them for review again, ignore or restore rows, edit categories and tags, remember future matches from transaction edits, run the optional single-transaction AI suggestion action when enabled, and use batch actions for selected rows.

Transaction statuses are Needs review, Automatic, Approved, and Ignored. Automatic means categorization was accepted without human review; Approved records your explicit confirmation. Status badges, filters, and the Review queue use the stored review state rather than separate confidence cutoffs. Owners configure confidence thresholds in Settings > Categorization; changes apply during future categorization or explicit recategorization, not by silently rewriting existing rows.

Approve clears a review requirement without changing the category, tags, or confidence. It is hidden for automatically accepted, exactly 100% rule-based matches, but remains available for AI results. Use More actions > Mark for review to clear human confirmation and reopen any active transaction without changing its classification. Explicitly reopened rule matches show Approve again so they can be confirmed. Both review actions also support selected rows. Transactions marked for review carry less weight as evidence for future similarity matches.

Editors and owners can export and import a transaction backup CSV from Transactions. The compact, 20-column backup includes all transactions, regardless of the current filters, including ignored rows, plus account details, merchant names, one category, tag names, review and categorization status, categorization and review timestamps, reimbursement completion flags and links, and fingerprints. Categorization explanations, tag assignment history, internal category keys, redundant category values, database IDs, and creation or match audit timestamps are not included; they are not needed to rebuild the usable ledger. Tag names are stored as a list so punctuation in names survives restoration. Each reimbursement link contains only the expense fingerprint and exact allocated amount, including partial and split reimbursements. Keep the exported file private and unchanged: spreadsheet programs may alter dates, amounts, or embedded details when saving CSV files. Spreadsheet formulas are escaped on export, and FinScope reverses that escaping on import.

Export a fresh backup before deleting the database. FinScope validates the expected columns and field values when importing. On a newly initialized database, create the owner account and then restore the transaction backup from Transactions.

Choose Import backup, review the preview, then select Skip duplicates or Import all rows. Duplicate checks use fingerprints or matching date, account, description, and amount, include repeated rows within the file, and run again when processing starts. Import all rows adds copies of duplicates with fresh fingerprints; Skip duplicates retains the existing transaction details. Accounts are matched by name, created when missing, and updated with the backup's account type and funding account. Missing merchants, user categories, and tags are created, and reimbursement links are restored once between the corresponding transactions. Built-in categories are matched by name to the initialized categories so their special behavior is preserved; missing or incorrectly configured built-in categories block restoration. Preview and confirmation return to the same filtered Transactions list.

When Skip duplicates leaves no transactions to import, FinScope reports that nothing was imported without creating a Processing item or changing accounts or other data. Otherwise the import appears on Processing with its status and imported/skipped counts; use View processing after confirmation to follow it. Restoration runs as one transaction: validation failures or cancellation roll back the changes. Fix invalid backup rows before confirming. Uploaded previews expire after 15 minutes and can only be confirmed by the user who uploaded them. Restarting FinScope also clears pending previews.

A transaction backup is not a full database backup. It does not include uploaded statement files, rule definitions, users, settings, saved reports, or accounts with no transactions unless they fund a backed-up account. Restored transactions do not attach to statements or categorization rules. Transactions and reimbursement matches receive new creation or audit timestamps, and restored tag assignments receive new assignment timestamps with an unknown original source. The original categorization and review timestamps are preserved.

Ignored rows stay in the database but are excluded from normal active-transaction views and rule matching unless a feature explicitly includes ignored rows.

## Reimbursements

Reimbursements tracks incoming credits that repay expenses paid upfront.

Keep reimbursable expenses in their natural category and tag them `Reimbursable` plus any context tags. Categorize the incoming credit as `Reimbursement` and keep any useful context tags such as `Judo`. Then create allocations that link the credit to the covered expenses. Allocated amounts reduce the original expense category in Dashboard and Comparison, while the reimbursement credit itself is not counted as ordinary income.

When an expense is only partially eligible for reimbursement, match the amount actually paid back, then mark the expense complete. This removes the remaining balance from reimbursement follow-up without inventing a credit or changing the original expense category. Use Resume tracking if more money later needs to be matched.

## Rules

Rules assign categories and optional tags to matching transactions.

Rules can be created, edited, deleted, imported, exported, approved, preview-applied individually, or preview-applied as a full rule set. Creating or editing a rule saves the future matching behavior directly; applying that rule to existing transactions, deleting applied rules, importing rules, and applying a full rule set remain preview-first when existing transactions may be affected.

Rules created directly from Rules are approximate-keyword rules by default. Rules saved while editing a transaction can be merchant-bound when the transaction has a durable merchant identity. Rules may also be scoped by account, transaction direction, and amount bounds.

Rule health check reports overlapping rules, category conflicts, tag differences, rules skipped by priority, stale or unused rules, and precision warnings. See [Categories, tags, and categorization](taxonomy.md) for matching priority and [Processing activity](background-jobs.md) for queued rule processing behavior.

## Review

Review groups active unknown or review-required transactions by merchant-like text so related rows can be categorized together.

The page shows group counts, transaction counts, largest group, review amount, examples, and Review group actions. In the review modal, users can assign a category, assign tags, choose Remember for future matches, or use Show all transactions to apply the change only to selected rows in the group.

Review operations may run in the background. Check Processing when a review action is queued.

## Home

Home is the operational landing page after login.

It shows a financial pulse, needs-attention items, recent activity, quick insights, and shortcuts. Use it after imports or long-running processing activity to see what needs cleanup next.

## Dashboard

Dashboard summarizes the selected period.

It includes categorization completeness, spending, income and credits, net cash flow, savings rate, average transaction, untagged rate, verified rate, top categorization source, spending breakdowns by category or tag, monthly cash flow, spending versus income over time, and merchant analytics.

Use the account filter to scope dashboard totals, charts, merchant rows, and transaction drill-downs to one imported account. Use the merchant filter to select a known merchant from suggestions or type partial merchant text; selected suggestions use the durable merchant identity, while typed text can match merchant names and imported descriptions.

The Scope filter applies to all financial totals, trend charts, and top drivers. Categorized includes transactions with a known category that do not need review; All includes every active reportable transaction in the selected period, account, and merchant filters. These scopes match the corresponding Reports quick views. Analysis readiness always covers the full period, account, and merchant selection so transactions needing review remain visible, even when Categorized has no matching rows.

Each Dashboard trend chart has PNG export and expand controls, matching the shared chart controls used elsewhere in FinScope. Expand opens a larger chart view; closing it restores the chart to its original position. The controls remain available after changing Dashboard filters.

Transfers and payments are visible as transactions but excluded from spending and income totals to avoid double-counting internal money movement. Reimbursement credits are also excluded from ordinary income; their allocations reduce the covered expense categories instead.

Unknown categories reduce report usefulness. Use the categorization completeness panel to find transactions that need review.

## Reports

Reports contains deeper financial analysis than Dashboard: overview totals, category and tag reports, account reports, merchant reports, income and credits, comparison links, exports, charts, and detailed tables.

Use Pin report on Reports pages to save the current report view and filters. Pinned report cards appear on the Reports overview with current live values, can be opened directly, and can be reordered, renamed, or removed from Edit pins. The number of cards shown is controlled by Settings > General > Pinned report limit.

## Comparison

Comparison analyzes spending, income and credits, or net cash flow changes.

Period changes compare a selected current period with a matching prior period. The Analysis filter controls whether category and merchant change details focus on spending, income and credits, or net cash flow. The view includes summary metrics, key insights, category changes, merchant changes, and filters for account, merchant, categories, and tags.

Year trends compare monthly values across selected years using the same Analysis filter. Account, merchant, category, and tag filters apply to both period and year comparisons, so a card, bank account, or merchant can be compared across months or years. The merchant filter supports known merchant suggestions and typed partial text. The view includes monthly charts, distribution, and category totals by year. Category warnings appear when Unknown spending may make comparison unreliable.

## Calendar

Calendar shows posted daily transactions for a selected month.

It summarizes monthly spending, income and credits, net cash flow, expected recurring items, and daily transaction counts. The heatmap can show spending, income, or net cash flow. Day cells can open transaction detail for that date.

The account and merchant filters scope daily posted activity, monthly totals, transaction drill-downs, and recurring evidence. Merchant suggestions use durable merchant identity when selected; typed partial text can match merchant names and imported descriptions.

## Recurring

Recurring detects repeated spending and income patterns.

It provides list and calendar views, category and tag filters, confidence filtering, status filtering, month navigation, and summary metrics for needs-attention items, monthly recurring spending, recurring income, expected soon, occurred, overdue, and possibly inactive patterns.

The account and merchant filters scope recurring detection evidence and list/calendar results. Merchant filtering can match the stored recurring pattern merchant or the underlying example transactions used to detect the pattern.

Detection is inferred from historical transactions before the selected month. Transactions in the selected month are used as evidence only when they are near the expected date and within the configured amount tolerance.

Users with recurring-edit permission can confirm a pattern, ignore a pattern, or edit frequency, expected date, typical amount, tolerances, and active state from the detail modal.

## Processing

Processing tracks longer-running workflows while the app remains usable.

Common processing activity includes statement import, transaction backup import, AI categorization, applying rules, review operations, and rule import. The page shows status, timestamps, results, errors, processing logs, cancellation where supported, and undo where the completed item still has undo metadata.

AI categorization uses a separate queue from the main import/rule/review queue. Processing also provides Run AI on unknowns and Clear queued AI controls.

Processing history is retained in the database for recent completed, failed, and cancelled work. If the app restarts while work is queued or running, that item is marked failed because the in-process worker no longer exists.

## Settings

Settings stores database-backed runtime preferences.

All authenticated users can edit General settings for their own account, including theme mode, interface language, and personal table/display limits. Owners can also edit shared advanced settings for categorization, recurrence detection, and statement import type mappings.

See [Settings reference](settings.md) for every configurable setting and [Authentication and authorization](authentication.md) for role-specific settings permissions.

## Categories and tags

Admin > Categories and tags manages categories and tags in the active database.

Categories are exclusive primary classifications. Tags are optional secondary labels that can overlap. The page supports creating, editing, deleting unused user-managed values, and importing or exporting categories and tags YAML.

Some categories and tags are system-managed because they affect workflows or reports. Built-in categories include `Income`, `Rental`, `UNKNOWN`, `Transfers`, and `Reimbursement`; built-in tags include `Reimbursable` and `Tax`. They are visible in the taxonomy page with built-in badges and cannot be renamed or deleted.

The seed file [src/finance_app/taxonomy.yml](../src/finance_app/taxonomy.yml) is used only when initializing ordinary user-managed taxonomy rows in a new database. After initialization, use the Categories and tags page for runtime changes. See [Categories, tags, and categorization](taxonomy.md) for the full model.

## AI categorization

AI categorization is optional and requires an OpenAI API key.

By default, FinScope shows an AI usage estimate before sending an AI request. Owners can turn that confirmation step off from Settings > Categorization; when it is off, statement imports automatically queue AI categorization for remaining unknown rows. Manual AI runs remain available from Processing and Uploaded statements. The single-transaction Suggest category action can also be shown or hidden from Settings.

FinScope minimizes what it sends to the AI provider. It does not send raw transaction descriptions, exact dates, exact amounts, account names, account types, account IDs, or similar-transaction examples.

## Privacy and security

FinScope handles financial data. Treat runtime databases, uploaded statement text, backups, logs, credentials, and API keys as sensitive.

- One FinScope installation maps to one shared finance database.
- One owner manages editor and viewer users.
- Passwords are stored with Werkzeug `scrypt` hashes.
- CSRF protection is enabled for mutating routes.
- Session cookies are HttpOnly and SameSite=Lax. HTTPS-only cookie transport is controlled by `FINANCE_SECURE_COOKIES` or `[app] secure_cookies`; when that setting is blank, non-local server binds use secure cookies and loopback local runs use plain cookies.
- No encryption at rest is implemented by FinScope.
- Optional OpenAI integration is inactive unless configured and explicitly run or enabled.

Operational recommendations:

- Keep `FINANCE_SECRET_KEY` private.
- Keep OpenAI API keys out of source control.
- Protect `runtime/finscope.db`, MySQL credentials, and backups.
- Back up the active database regularly.
- Do not run with debug mode enabled on a shared network.

## Known limitations

- FinScope supports multiple authenticated users for one shared finance dataset, not multi-tenant hosting.
- Processing execution is process-local and does not resume automatically after restart.
- No bank synchronization is built in.
- No built-in encryption at rest is implemented.
- SQLite is intended for local use, not high-concurrency workloads.
