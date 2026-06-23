# Settings reference

Settings stores runtime preferences in the active database. General settings are personal to each signed-in user. Owner-only settings control shared behavior for the deployment and are saved on the owner account so background jobs and non-request workflows can resolve the same values.

Initial values come from the `[setting_defaults]` entries in root `config.ini`, environment variables, or built-in defaults when a setting has not been saved yet. Root [config.example.ini](../config.example.ini) lists a seed value for every Settings-page parameter. Changing configuration defaults later does not overwrite settings already stored in the database; use the Settings page to change saved runtime behavior.

See [Authentication and authorization](authentication.md#settings-permissions) for role-specific access rules.

## General

Every authenticated user can edit General settings for their own account.

| Setting | Allowed values | Effect |
| --- | --- | --- |
| Theme mode | `Dark` or `Light` | Chooses the user's interface theme. |
| Interface language | `English` or `French` | Chooses UI language for the user. User-entered financial data such as merchants, accounts, categories, tags, statements, and descriptions is not translated. |
| Table page size | Whole number, minimum `1` | Sets the default row count for paginated tables. |
| Comparison default years | Whole number, minimum `2` | Sets how many years are selected by default on the Comparison year-trends view. |
| Comparison insight card limit | Whole number, minimum `1` | Limits the ranked insight cards shown on Comparison. |
| Home top category limit | Whole number, minimum `1` | Limits the top spending categories shown on Home. |
| Dashboard top driver limit | Whole number, minimum `1` | Limits the compact top-driver previews shown on Dashboard. |
| Merchant table limit | Whole number, minimum `1` | Limits merchant rows shown in comparison summaries. |
| Merchant suggestion limit | Whole number, minimum `1`; default `5` | Limits merchant suggestions shown in autocomplete fields. |
| Rule preview limit | Whole number, minimum `1` | Limits matching transactions shown while previewing rule changes. |
| Rule health check transaction limit | Whole number, minimum `1` | Sets how many newest historical transactions the rule health check analyzes before showing its limited-check notice. |

## Categorization

Owners can edit Categorization settings. Optional AI behavior still requires an OpenAI API key in configuration before any request can be sent.

| Setting | Allowed values | Effect |
| --- | --- | --- |
| Single-transaction AI | On or off | Shows or hides the Suggest category action on transaction rows. The action previews one AI category suggestion and lets the user decide whether to apply it or save a rule. |
| Review AI usage | On or off | When on, AI actions show an estimated AI usage summary and ask before sending a request. When off, statement imports can automatically queue AI categorization for remaining unknown rows. |
| AI acceptance threshold | Number from `0` to `1` | Minimum confidence required before AI can create an automatic rule for a no-review result. |
| AI review threshold | Number from `0` to `1` | Minimum confidence required to keep the best-fit AI category as a review item instead of leaving the transaction as `UNKNOWN`. |
| Verify threshold | Number from `0` to `1` | Accepted AI categories below this confidence stay marked for review. |
| OpenAI model | Model name containing letters, numbers, `.`, `_`, `:`, `/`, `+`, or `-` | Sets the categorization model. The Validate button checks whether the configured API key can see the model through the OpenAI models API. |

## Recurrence detection

Owners can edit Recurrence detection defaults. Users with recurring-edit permission can still override an individual recurring pattern from its detail modal.

| Setting | Allowed values | Effect |
| --- | --- | --- |
| Minimum occurrences | Whole number, minimum `1`; default `3` | Sets the number of distinct months needed before a detected pattern is shown. |
| Date tolerance days | Whole number, minimum `1`; default `5` | Sets the match window around an expected recurring date. |
| Absolute amount tolerance | Number, minimum `0`; default `10` | Sets the minimum amount difference tolerated when matching a recurring item, in the app's configured currency units. |
| Amount tolerance percent | Number from `0` to `1`; default `0.15` | Sets the percentage amount tolerance relative to the typical amount. FinScope uses the larger of the absolute and percentage tolerances. |
| Missed cycles before inactive | Whole number, minimum `1`; default `2` | Reserved for future inactive-pattern detection. |

## Statements

Owners can edit the statement types available on Upload and Uploaded statements. At least one active statement type is required, and active statement type names must be unique.

Each statement type has these fields:

| Field | Allowed values | Effect |
| --- | --- | --- |
| Name | Non-empty unique name | Displayed in upload forms and statement history. |
| Statement type | `Checking account`, `Credit card`, or `Interac e-Transfer history` | Chooses how imported rows are parsed and interpreted. |
| Import behavior | `Ledger source` or `Enrichment source` | Ledger sources create transaction rows. Enrichment sources update matching existing rows instead of creating a new ledger. Interac e-Transfer history is always treated as an enrichment source. |
| Default account role | `Checking account`, `Savings account`, or `Credit card` | Sets the default reporting role for accounts created from that statement type. |

Removing a statement type from Settings deactivates it for future uploads. Existing uploaded statement history remains part of the database.

## Saving behavior

Save settings validates all visible fields before writing changes. Non-owners can save only their own General settings; owner-only fields are not accepted from editor or viewer accounts even if submitted manually.

Reset form reloads the page and discards unsaved form edits. It does not restore saved settings to defaults.
