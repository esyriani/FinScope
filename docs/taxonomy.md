# Categories, tags, and categorization

FinScope stores categories and tags in the database. The file [src/finance_app/taxonomy.yml](../src/finance_app/taxonomy.yml) provides the initial user-editable seed during application setup, but it is not the runtime source of truth. Once the database has been initialized, categories and tags are managed through the application UI and stored in the database.

Categories and tags are central to FinScope. They drive dashboards and analytics, recurring activity detection, categorization rules, merchant review workflows, reporting and filtering, historical categorization, and optional AI-assisted categorization.

![Categories and tags page](img/taxonomy2.png)

## Categories and tags

Every transaction must belong to exactly one category and may optionally contain zero or more tags.

Categories represent the primary financial nature of a transaction. They answer questions such as:
- What kind of spending is this?
- What kind of income is this?
- What financial activity does this represent?

Typical categories include Groceries, Rent, Salary, Utilities, Transportation.

Categories are intentionally broad and stable because they are heavily used in charts, summaries, trends, and long-term analytics.

Tags provide additional optional context. Unlike categories, tags are non-exclusive and may overlap. Tags are useful for cross-cutting concerns and secondary classification.

For example:

Category `Food` can coexist with tags such as `Work`, `Travel`, and `Reimbursable`.

Tags are commonly used for reimbursements, work-related expenses, vacations, medical claims, tax-related purchases, temporary projects or events.

A good category remains meaningful over years of historical data. A good tag adds contextual information without replacing the category.

## The categories and tags seed file

Before running FinScope for the first time, users may customize [src/finance_app/taxonomy.yml](../src/finance_app/taxonomy.yml).

This file defines the initial user-managed categories and tags inserted into the database during initialization. FinScope-managed built-in categories and tags are defined in code, not in this file.

After the application has been initialized, category and tag changes can only be performed through the FinScope UI rather than by editing the file directly. Directly modifying [taxonomy.yml](../src/finance_app/taxonomy.yml) after the database already contains categories and tags does not automatically synchronize existing runtime data.

## Category and tag structure

Each category or tag contains: a unique name, a human-readable description, and optional AI guidance used by AI categorization.

Typical category or tag fields:

| Field | Example |
| --- | --- |
| `name` | `Groceries` |
| `description` | `Food and household consumables purchased from grocery stores.` |
| `instruction` | `Use for supermarkets, grocery chains, food markets, and recurring food shopping.` |

### Naming constraints

Category names and tag names must remain unique within their own groups.
Avoid names that differ only by capitalization, spacing, punctuation, or singular/plural form.
Prefer concise and descriptive names over abbreviations or personal shorthand.

Good examples: Transportation, Healthcare, Home Maintenance
Poor examples: Misc, Other2, Stuff, Random

### Writing effective categories

Categories should represent stable financial concepts rather than merchants or temporary situations.
Merchant-specific behavior should usually be handled through categorization rules rather than through dedicated categories.
Categories should answer *What kind of financial activity is this?* rather than *where was the purchase made?*

### Writing effective tags

Tags should provide secondary context that may apply across multiple categories.
Good tags are reusable, orthogonal to categories, optional, and descriptive.
Tags should answer questions such as *Was this reimbursable? Was this work-related? Was this part of a trip? Was this exceptional or temporary?*

Avoid creating tags that duplicate categories or that are too specific to a single transaction.

### Descriptions

The `description` field is intended primarily for human users.
It explains the meaning of a category or tag to reduce reduce ambiguity and guide users toward consistent categorization.
Descriptions should not contain prompt-engineering instructions, technical metadata, or excessive examples.

### AI guidance

The `instruction` field is shown as AI guidance and is intended primarily for optional AI-assisted categorization.
It helps the model distinguish similar categories, understand categorization boundaries, identify likely merchant patterns, and avoid incorrect classifications.

Instructions may contain merchant examples, inclusion guidance,exclusion guidance, semantic hints.

## Built-in taxonomy semantics

Some categories and tags are built into FinScope because they carry application
behavior beyond ordinary labeling. Built-ins are seeded from the application
registry with stable keys, then shown in the Categories and tags page as
read-only system rows. Their names, descriptions, guidance, and internal
semantics are managed by FinScope rather than by [taxonomy.yml](../src/finance_app/taxonomy.yml).

The starter [taxonomy.yml](../src/finance_app/taxonomy.yml) remains the editable
seed for ordinary user-managed categories and tags. It can reference built-in
items in guidance, but it is not the source of truth for protected semantics.

The application UI marks these rows as built-in so users know they cannot be
edited or deleted. The operational effects are documented here instead of being
shown as short badges in the table.

| Built-in item | What the stable key affects |
| --- | --- |
| `Income` category | Provides the protected ordinary-income category used by categorization rules and AI guidance. Rule matching treats this key as ordinary income for income-specific direction checks. Income analytics still use the transaction cash-flow role, not this category alone. |
| `Rental` category | Reserves a stable category for rental-property cash flow. It does not currently change report totals by itself; normal inclusion still follows transaction kind. The key is protected so future rental reporting, tax review, and exports can depend on it. |
| `UNKNOWN` category | Represents unresolved classification. Imports, deterministic categorization, historical matching, and AI fallback use this value when no confident category exists. Review and AI rerun flows treat null or `UNKNOWN` categories as unresolved work. |
| `Transfers` category | Provides the protected label used when transaction-kind detection identifies balance movement, credit card payments, cash withdrawals, deposits, or adjustments. Spending and income reports exclude transfer/payment transaction kinds; the category keeps the user-facing taxonomy aligned with that non-reportable cash-flow role. |
| `Reimbursement` category | Identifies incoming reimbursement credits. Reporting excludes these credits from ordinary income, and allocation-aware spending calculations use reimbursement allocations to offset the covered expense rows. The Reimbursements page uses the key to find eligible credits. |
| `Reimbursable` tag | Identifies expenses expected to be repaid. The Reimbursements page uses this key, allocation rows, and completion markers to show pending expenses and settled items. |
| `Tax` tag | Reserves a stable tag for tax preparation, accounting, and year-end review. It does not currently change totals by itself; the key is protected for future tax review and export workflows. |

## Special built-in categories

### Income

The built-in `Income` category is the default category for ordinary incoming
money such as payroll, benefits, pensions, or investment income credits.
Income totals are still calculated from the transaction cash-flow role rather
than from the category name alone, so rental income, transfers, reimbursements,
and refunds can keep their more precise semantics.

`Income` is protected because it anchors rule guidance and ordinary-income
categorization. It cannot be renamed, edited, or deleted from the UI.

### Rental

The built-in `Rental` category is reserved for income-generating rental-property
cash flow. It is currently a workflow-ready system category: FinScope protects
the identity now so future rental-property reporting, tax review, and exports
can depend on a stable semantic key.

`Rental` cannot be renamed, edited, or deleted from the UI.

### Unknown

The built-in `UNKNOWN` category is reserved for transactions that could not be confidently categorized automatically.
Transactions assigned to `UNKNOWN` should normally be reviewed manually. Users may assign a category directly to the transaction, or create a categorization rule. This category is managed by FinScope and cannot be renamed, edited, or deleted from the UI.

> A high volume of unknown transactions reduces the usefulness and accuracy of analytics.

### Transfers

The built-in `Transfers` category is reserved for internal money movements that may appear multiple times across statements.
Typical examples include paying a credit card balance, moving money between checking and savings accounts, or internal account rebalancing.
This category is managed by FinScope and cannot be renamed, edited, or deleted from the UI.

> Transfer transactions are excluded from income and spending analytics to avoid double-counting money movement as real financial activity.

### Reimbursement

The built-in `Reimbursement` category is reserved for incoming credits that repay expenses the user paid upfront. Keep the original expenses in their natural category, such as `Travel`, `Food`, or `Work`, and tag those expenses with context such as `Conference` and `Reimbursable`. Categorize the incoming credit as `Reimbursement`, then link it to the covered expense transactions so FinScope can track paid and pending amounts. The credit can keep context tags such as `Conference` or `Insurance`, but it does not need the `Reimbursable` tag because the allocation link records what was repaid.

For example, $1,000 of conference travel expenses tagged `Conference` and `Reimbursable`, followed by a $900 reimbursement credit categorized as `Reimbursement` and allocated to those expenses, leaves $100 pending reimbursement while preserving the natural `Travel` spending category.

If the missing $100 is never expected because of an eligibility policy,
mark the expense complete on the Reimbursements page. Marking it complete removes the item from
the active reimbursement queue but does not create an artificial reimbursement
or change the underlying category treatment.

## Special built-in tags

### Reimbursable

The built-in `Reimbursable` tag marks expenses that are expected to be repaid
by work, insurance, a tenant, another person, or another organization. The
Reimbursements page uses this tag, reimbursement allocations, and completion
markers to track which expenses are still pending.

`Reimbursable` is protected and cannot be renamed, edited, or deleted from the
UI. Reimbursement credits themselves normally use the `Reimbursement` category
instead of this tag; the allocation link records what was repaid.

### Tax

The built-in `Tax` tag marks transactions that may be useful for tax
preparation, accounting, or year-end review. It is protected as a
workflow-ready tag so future tax review and export features can depend on a
stable semantic key.

## Categories and tags administration

The Categories and tags page allows authorized users to:
* create categories and tags,
* rename existing entries,
* update descriptions and instructions,
* delete unused entries.

Deletion is blocked when a category or tag is still referenced by transactions or rules.
Built-in categories and tags cannot be modified or deleted.

## Categorization flow

FinScope categorizes transactions incrementally using deterministic rules, historical evidence, and optional AI assistance.

The process follows this general order:

1. Normalize the merchant description.
2. Evaluate matching categorization rules.
3. Apply high-confidence deterministic matches.
4. Retrieve similar historical transactions.
5. Apply historical evidence when confidence is sufficient.
6. Keep unresolved transactions in `UNKNOWN`.
7. Optionally invoke AI categorization for unresolved transactions.
8. Preserve review requirements for uncertain results.

Rule-based categorization always runs before historical retrieval and AI categorization.
Manual edits take precedence over automatic categorization.

Within one import batch, FinScope only reuses an automatic decision for transactions with the same durable merchant identity and the same signed amount. This prevents amount-specific rule or history evidence from leaking across different transaction amounts.

### Rule matching

FinScope supports two rule scopes: merchant-bound and approximate-keyword rules.
**Merchant-bound rules** match transactions associated with a durable merchant identity.
**Approximate-keyword rules** use normalized substring matching against simplified transaction descriptions and main merchant names.
For example, `VIREMENT` matches: `VIREMENT INTERAC 2`.

Rules may also be constrained by account, signed direction (`any`, `debit`, or `credit`), and optional amount bounds. Account and direction constraints make rules more specific and increase rule confidence when they match. Imported rule CSV files can include `account_name` and `direction`; an explicit account name must match an existing account so a misspelled scoped import does not become a broad rule.

Rule priority is deterministic. Higher-priority rules are evaluated first based on:

1. manual vs automatic/default,
2. amount-bounded vs unbounded,
3. merchant-bound vs keyword-fuzzy,
4. account-scoped and direction-scoped rules,
5. longer keywords vs shorter keywords.

### Rule health check

Rule health check is the diagnostic surface for category-rule behavior. It uses the same matcher and deterministic priority model as imports, apply-all, and selected-rule application, but exposes all matching rules rather than only the applied rule.

The main health check page reports overlap pairs, harmless overlaps, category conflicts, tag differences, rules skipped by priority, stale or unused rules, and precision or priority warnings. Detail pages show the shared transactions behind an overlap, the applied rule, rules not applied, confidence level, match score, precision, current stored category and tags, and whether the applied rule agrees with the stored category.

Creating or editing a rule saves the future matching behavior directly from the Rules page. Existing transactions are not rewritten by that save. When a rule might change historical transaction state, the cautious path remains preview-first: deleting applied rules, applying all rules, applying a selected rule where it would normally have priority, force-applying a selected rule, and importing rules render a read-only impact preview before the historical mutation is allowed. The preview groups transaction-level effects by category changes, tag changes, applied-rule-only changes, and no material changes.

FinScope is single-tenant: all authenticated users with rule-management permission check the same shared finance dataset for the deployment. The health check intentionally does not filter by per-user transaction ownership because transactions and rules are not workspace-scoped in the current schema. Use a separate deployment and database for separate finance workspaces.

For large histories, the `rule_audit_transaction_limit` setting caps the newest historical transactions analyzed. Pages show a limited-check notice when more eligible rows exist than the configured cap.


### Historical categorization

Before using AI, FinScope retrieves similar previously categorized transactions from the local database.
Historical scoring considers merchant similarity, normalized descriptions, amount similarity, account matching, transaction direction, recency, and manually reviewed status.

High-confidence matches require strong agreement across similar transactions before they are applied automatically. Medium-confidence matches remain reviewable, and ambiguous or contradictory historical evidence is passed to AI as context instead of being finalized alone.

## AI categorization

Automatic and manual category writes persist compact JSON evidence in `transactions.category_metadata`. The metadata uses a controlled technical `decision_source`: `rule`, `similar_transactions`, `llm`, `llm_with_similar_transactions`, `combined`, `manual`, or `unknown`.

AI categorization is optional and requires `OPENAI_API_KEY`.
External prompts are privacy-minimized. The AI receives normalized merchant text, coarse amount direction and magnitude, transaction kind, compact category evidence summaries, the full category/tag list, and transaction-local candidate category/tag hints. FinScope does not send raw transaction descriptions, exact dates, exact amounts, account names, account types, account IDs, or similar-transaction examples. Candidate categories and tags are hints, not a gate: the model may choose any active category or tag ID from the full category/tag list when the supplied evidence supports it.

The static system-prompt policy is stored in [src/finance_app/modules/categories/llm_system_prompt.json](../src/finance_app/modules/categories/llm_system_prompt.json). Runtime code renders that structured resource with the current confidence thresholds, while transaction, category/tag, and rule payloads are still built by the Python prompt builders.

Returned results are validated conservatively. Invalid JSON, invalid category IDs, invalid tag IDs, invalid confidence values, or inconsistent evidence remain categorized as `Unknown` or are marked for review according to the shared confidence policy.

AI uses three configurable thresholds with separate responsibilities:

1. `llm_review_threshold` keeps a best-fit AI category as a review-required suggestion instead of falling back to `UNKNOWN`.
2. `verify_threshold` controls when an AI category can clear review automatically.
3. `llm_confidence_threshold` controls when a no-review AI result can create a reusable automatic rule.

AI categorization is operationally separate from statement import. Imports apply
rules and historical evidence first, then report remaining unknown rows that can
be categorized with AI. By default, owners run AI manually from Processing or from an
uploaded statement after reviewing the AI usage estimate. If the owner turns off
AI usage review in Settings > Categorization, statement imports
automatically queue AI categorization for those remaining unknown rows. Reruns
only target active transactions that are still null or `UNKNOWN`, so existing
manual, rule-based, historical, transfer, and accepted AI categories are not
overwritten.

For focused review, the transaction table can show a synchronous Suggest category action for one row. This previews AI categorization for the selected transaction only, displays confidence level, evidence, metadata, and failure reasons, and then lets the user explicitly apply the suggestion once or apply it and remember future matches.
