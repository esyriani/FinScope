# User guide

This guide is a concise feature reference for day-to-day FinScope use. Start with [Getting started](getting-started.md) for the first-run walkthrough, then use the [Tutorial](tutorial.md) for workflow advice and best practices.

## Upload

Upload imports CSV statements and manages uploaded statement history.

The upload form collects an account name, statement import type, account reporting role, optional Interac direction, optional paid-from account for credit cards, and the CSV file. The preview modal shows parsed rows, row counts, date range, and date-format controls before import confirmation.

Uploaded statements show import status, added/skipped/ignored counts, unknown counts, AI candidate counts, stored text preview, Retry, Reprocess, and Run AI actions. Retry reruns a failed or incomplete import from stored statement text. Reprocess removes transactions imported from that statement and imports them again from stored statement text.

Interac e-Transfer history is enrichment-only. Import matching checking statements first, then import Interac history for the same account so FinScope can update existing transfer rows with clearer details.

## Transactions

Transactions is the searchable transaction history. It supports period, category, tag, status, ignored-state, and how-categorized filters.

Editors and owners can approve rows, ignore or restore rows, edit categories and tags, remember future matches from transaction edits, run the optional single-transaction AI suggestion action when enabled, and use batch actions for selected rows.

Ignored rows stay in the database but are excluded from normal active-transaction views and rule matching unless a feature explicitly includes ignored rows.

## Reimbursements

Reimbursements tracks incoming credits that repay expenses paid upfront.

Keep reimbursable expenses in their natural category and tag them `Reimbursable` plus any context tags. Categorize the incoming credit as `Reimbursement`, then create allocations that link the credit to the covered expenses. Allocated amounts reduce the original expense category in Dashboard and Comparison, while the reimbursement credit itself is not counted as ordinary income.

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

Common processing activity includes statement import, AI categorization, applying rules, review operations, and rule import. The page shows status, timestamps, results, errors, AI progress logs, cancellation where supported, and undo where the completed item still has undo metadata.

AI categorization uses a separate queue from the main import/rule/review queue. Processing also provides Run AI on unknowns and Clear queued AI controls.

## Settings

Settings stores database-backed runtime preferences.

All authenticated users can edit General settings for their own account, including theme mode, interface language, and personal table/display limits. Owners can also edit shared advanced settings for categorization, recurrence detection, and statement import type mappings.

See [Settings reference](settings.md) for every configurable setting and [Authentication and authorization](authentication.md) for role-specific settings permissions.

## Categories and tags

Admin > Categories and tags manages categories and tags in the active database.

Categories are exclusive primary classifications. Tags are optional secondary labels that can overlap. The page supports creating, editing, deleting unused user-managed values, and importing or exporting categories and tags YAML.

Some categories and tags are system-managed because they affect workflows or reports. `Income`, `Rental`, `UNKNOWN`, `Transfers`, `Reimbursement`, `Reimbursable`, and `Tax` are visible in the taxonomy page with built-in badges and cannot be renamed or deleted.

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
- Session cookies are HttpOnly and SameSite=Lax; secure cookies are enabled when debug mode is off.
- No encryption at rest is implemented by FinScope.
- Optional OpenAI integration is inactive unless configured and explicitly run or enabled.

Operational recommendations:

- Keep `FINANCE_SECRET_KEY` private.
- Keep `OPENAI_API_KEY` out of source control.
- Protect `runtime/finscope.db`, MySQL credentials, and backups.
- Back up the active database regularly.
- Do not run with debug mode enabled on a shared network.

## Known limitations

- FinScope supports multiple authenticated users for one shared finance dataset, not multi-tenant hosting.
- Processing activity state is process-local and in memory.
- No bank synchronization is built in.
- No built-in encryption at rest is implemented.
- SQLite is intended for local use, not high-concurrency workloads.
