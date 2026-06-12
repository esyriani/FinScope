# Tutorial

This tutorial is an opinionated workflow guide for using FinScope well after the first launch. It focuses on practical sequencing, habits, and interpretation. Use the [User guide](user-guide.md) when you need a concise feature reference, and use [Taxonomy and categorization](taxonomy.md) for the detailed category, tag, rule, historical, and LLM model.

## Start with the taxonomy

FinScope seeds its initial user-managed categories and tags from [src/finance_app/taxonomy.yml](../src/finance_app/taxonomy.yml) when a new database is initialized.

Modify `taxonomy.yml` before the first run if you already know your preferred category and tag vocabulary. This is the best time to remove categories you will not use, add durable personal categories, add reusable tags, and write short descriptions or LLM instructions.

After the database has been initialized, edit taxonomy from Admin > Taxonomy instead. Changing `taxonomy.yml` later does not automatically synchronize existing database categories and tags.

Good taxonomy habits:

- Keep categories broad and stable enough to survive years of history.
- Use tags for cross-cutting context such as work, travel, reimbursable, shared expense, medical, tax, or a temporary project.
- Avoid merchant names as categories unless the merchant is truly the financial purpose.
- Avoid tags that merely duplicate categories.
- Write descriptions for humans and instructions for optional LLM categorization.

![Taxonomy](img/taxonomy.png)

## First month workflow

The first month is about building reliable history, not perfect dashboards.

1. Decide whether to edit `taxonomy.yml` before creating the first database.
2. Create the owner account.
3. Import checking and savings statements first.
4. Import credit card statements after deciding their account reporting role and paid-from account.
5. Import Interac e-Transfer history only after the matching checking rows exist.
6. Check Upload > Uploaded statements for added, skipped, ignored, unknown, and failed counts.
7. Open Review and categorize the biggest unknown groups.
8. Save rules for stable merchant patterns.
9. Use Rules > Preview apply all after creating or importing several rules.
10. Open Dashboard, Calendar, Recurring, and Comparison to find obvious cleanup needs.

Repeat that loop for the next statement. The app becomes more useful as rules and reviewed history accumulate.

![Home](img/home.png)

## Upload statements

Use the statement import type that matches the file:

| Statement type | Typical role | How to think about it |
| --- | --- | --- |
| Checking account | Checking or savings | Creates ledger transactions for bank activity. |
| Credit card | Credit card | Creates purchase-level ledger transactions. Card payments are marked as payments/transfers so spending is not double-counted. |
| Interac e-Transfer | Checking account | Enriches existing generic checking rows with real counterparty detail. It does not add duplicate ledger transactions. |

The upload preview is important. It shows parsed dates, descriptions, amounts, imported row counts, ignored row counts, and date-format handling. For ambiguous slash dates, choose the correct `MM/DD/YYYY` or `DD/MM/YYYY` option before confirming.

![Confirm import](img/confirm-import.png)

### Why rows may be skipped or ignored

Skipped usually means FinScope chose not to insert a row that should not become a new ledger transaction, such as a duplicate transaction fingerprint or an ambiguous Interac match.

Ignored usually means the parser recognized a row but decided it is not importable as a transaction, such as a non-transaction row. For Interac history, ignored rows can also be cancelled, non-deposited, or missing a matching checking transaction.

Exact duplicate files are blocked by statement checksum. If the previous upload created a statement record but the import failed, use Retry from Uploaded statements. Use Reprocess when you want to remove transactions imported from that statement and import them again from the stored statement text.

Common import errors and fixes:

- Wrong statement type: reprocess with a corrected statement type if the parser or sign behavior was wrong.
- Ambiguous dates: use preview date-format controls before importing.
- Unrecognized CSV shape: check that the file has recognizable date, description, and amount/debit/credit columns, or a compact `date,description,amount` shape.
- Interac imported too early: import the matching checking statement first, then reprocess the Interac history.
- Credit card payments counted as spending: confirm the account reporting role and paid-from account, then reprocess the card statement if needed.

![Uploaded statements](img/statements-uploaded.png)

## Manage transactions

Transactions is the detailed ledger view. Use it when you need to search, filter, approve, ignore, or directly edit rows.

Useful transaction actions:

- Approve a row when its current category and tags are correct.
- Ignore a row when it should stay in the ledger but not affect normal review work.
- Edit category and tags when a row is wrong or incomplete.
- Save a rule while editing when future matching rows should be categorized the same way.
- Use batch actions to approve selected rows, ignore selected rows, or recategorize selected rows.

![transactions](img/transactions.png)

## Build rules deliberately

Rules are the strongest day-to-day automation tool. Create fewer, clearer rules before creating many broad ones.

Merchant-specific rules are best when the transaction has a durable merchant identity and the merchant always means the same thing. They are commonly created from a transaction edit or review flow.

Keyword-fuzzy rules are best when a normalized keyword reliably appears in transaction descriptions or merchant names. Rules created directly from the Rules page are keyword-fuzzy by default.

Use optional scopes to make rules safer:

- Account scope when the same keyword means different things on different accounts.
- Direction scope when debit and credit rows should be categorized differently.
- Amount bounds when a merchant has multiple predictable payment types.
- Tags when the same rule should attach secondary context.

Rules are preview-first. Creating, editing, deleting, approving automatic rules, importing rules, applying one rule, and applying all rules should show impact before mutation.

![rules](img/rules.png)

### Importing and applying rules

Rules can be exported and imported as CSV. Imported rows use the export format shown in the import modal, including keyword, account name, merchant name, category, tags, amount bounds, direction, source, and created timestamp.

Use Add new rules only when merging rules from another database or backup. Use Override all rules only when you intentionally want to replace the current rule set; the resulting background job can be undone from Jobs when undo metadata is still available.

To apply one rule, use its preview apply action and confirm the preview. To apply many rules, use Preview apply all. FinScope applies rule precedence rather than blindly rewriting every matching transaction. Where the audit exposes a force-apply action, treat it as an explicit override and review the preview carefully.

![Rule audit detail](img/rule-audit-detail.png)

### Rule matching order

When more than one rule matches, FinScope uses deterministic precedence. Higher-priority rules are evaluated first based on:

1. Manual versus automatic/default source.
2. Amount-bounded versus unbounded rules.
3. Merchant-bound versus keyword-fuzzy scope.
4. Account-scoped and direction-scoped rules.
5. Longer keywords versus shorter keywords.

Manual edits take precedence over automatic categorization. Rule-based categorization runs before historical retrieval and optional LLM categorization.

## Use optional AI categorization carefully

AI categorization is optional and requires `OPENAI_API_KEY` or the equivalent config setting. By default, imports keep remaining unknown rows available for manual AI runs from Uploaded statements or Jobs after FinScope shows a token estimate. Owners can turn that confirmation step off in Settings > Categorization; when it is off, imports automatically queue AI categorization for remaining unknown rows.

AI fits after deterministic categorization:

1. Rules run first.
2. Historical evidence is considered.
3. Remaining unknown rows can be sent to the AI queue automatically when token confirmation is off, or manually requested when confirmation is on.
4. Low-confidence or review-required results stay reviewable.

FinScope privacy-minimizes external LLM prompts. It does not send raw transaction descriptions, exact dates, exact amounts, account names, account types, account IDs, or similar-transaction examples.

Use Jobs > Run AI on unknowns for a broad pass. Use Upload > Uploaded statements > Run AI for one statement. If enabled in Settings, use Suggest category from a transaction row for a one-row preview before applying the suggestion or applying it and creating a rule.

![Suggest category](img/suggest-category.png)

## Review unknowns

The Review module is optimized for resolving unknown or review-required transactions in groups.

Recommended review flow:

1. Sort or search to find high-impact merchant groups.
2. Open Review group.
3. Check the examples and total impact.
4. Use Show all transactions when the group may contain exceptions.
5. Select only the rows that should receive the same category when needed.
6. Choose a category and optional tags.
7. Save a rule only when future rows should match the same way.
8. Check Jobs for background review operations.

![Review rule](img/review-rule.png)

## Read the main pages

### Home

Home is the operating view. It summarizes what needs attention, recent activity, quick insights, and shortcuts to the next likely action. Treat it as a triage page after imports or long-running jobs.

### Dashboard

Dashboard is the current analysis view. It includes categorization completeness, spending, income and credits, net cash flow, savings rate, average transaction, untagged rate, verified rate, spending breakdowns, monthly cash flow, spending versus income over time, and merchant analytics.

Unknown categories reduce report usefulness. Use the categorization completeness panel and Review link when Dashboard warns about data quality.

Transfers are visible in the ledger but are excluded from spending and income totals to avoid double-counting internal money movement. Credit card payment rows and matching funding-account payment rows are treated as payments/transfers. When a concrete tag filter is applied, matching transfer credits can be included so reimbursement-style tags can show a net view.

Use account and merchant filters when you want an analysis slice you can return to later, such as one credit card and one merchant across several months. Analytics pages keep those filters in the URL so refresh, back/forward navigation, and copied links preserve the view.

![Dashboard](img/dashboard.png)

### Comparison

Comparison has two major views:

- Period changes compare the selected current period with the matching prior period and highlight category and merchant changes.
- Year trends compare monthly spending, income and credits, or net cash flow patterns across selected years and summarize category totals by year.

Large Unknown category shares can make category comparisons unreliable, so review unknowns before drawing conclusions. Period comparisons are most useful when both periods have similar import completeness.

![Comparison period](img/comparison-period.png)
![Comparison year](img/comparison-year.png)

### Calendar

Calendar shows posted daily transactions for a selected month. It summarizes spending, income and credits, net cash flow, and expected recurring activity. The account and merchant filters narrow visible days, monthly totals, transaction drill-downs, and recurring evidence. The heatmap can show spending, income, or net cash flow. Double-click a day or use the day link to inspect that day's transactions.

![Calendar](img/calendar.png)

### Recurring

Recurring detects repeated spending and income patterns. Use it to confirm useful patterns, ignore noise, inspect overdue items, and track amount changes. It has list and calendar views, account and merchant filters, status filters, category/tag filters, confidence filtering, month navigation, and detail modals with confirm, ignore, and edit actions for users with recurring-edit permission.

![Recurring](img/recurring.png)

## Settings controls

Every authenticated user can update General settings such as theme mode, interface language, and personal table/display limits.

Owners can also manage advanced settings:

- Categorization settings: AI token confirmation, single-transaction AI action visibility, confidence thresholds, and OpenAI model validation.
- Recurrence detection settings: minimum occurrences, date tolerance, amount tolerance, and missed-cycle defaults.
- Statement settings: statement import type names, parser mappings, import behavior, and default account role.

![Settings general](img/settings-general.png)

## Common mistakes and recommended practices

Common mistakes:

- Importing Interac history before checking-account statements.
- Using a credit card statement type with a non-credit account role.
- Treating transfers as spending.
- Creating categories for every merchant.
- Creating broad fuzzy rules without previewing matches.
- Editing `taxonomy.yml` after the database is already initialized and expecting live data to change.
- Drawing conclusions from Dashboard or Comparison while many transactions are still Unknown.
- Turning off AI token confirmation before a large cleanup you have not reviewed.

Recommended practices:

- Start with ordinary checking and credit card statements before enrichment sources.
- Review Upload counts after every import.
- Resolve large unknown groups before fine-tuning small rows.
- Prefer merchant-bound or scoped rules for ambiguous merchants.
- Use tags for reimbursable, work, travel, tax, and shared-expense overlays.
- Export rules and taxonomy before large restructuring work.
- Keep token confirmation on when you want to run AI manually after deterministic rules and obvious review work have done their part.
- Back up the active database regularly.
