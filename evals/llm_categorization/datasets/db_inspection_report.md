# LLM Categorization Database Inspection Report

This report is generated from SQLite schema introspection and aggregate counts.
Inferred table roles are inferred, not guaranteed.

## Source

- Database: `runtime\finescope.db`
- Connection: SQLite read-only URI with `PRAGMA query_only=ON`
- Raw merchant names and transaction descriptions are not printed.

## Inferred Relevant Schema

### AI confidence or evidence
- Inferred table `category_rules`; relevant columns: `ai_approved`
- Inferred table `statements`; relevant columns: `llm_candidate_count`
- Inferred table `transactions`; relevant columns: `category_source`, `category_confidence`, `category_metadata`, `category_rule_id`

### Categories
- Inferred table `categories`; relevant columns: `id`, `name`, `builtin_key`, `description`, `instruction`, `created_at`
- Inferred table `category_rules`; relevant columns: `category`, `category_id`, `source`, `ai_approved`
- Inferred table `transactions`; relevant columns: `category`, `category_id`, `category_source`, `category_confidence`, `category_metadata`

### Categorization rules
- Inferred table `category_rule_tags`; relevant columns: `rule_id`, `tag_id`
- Inferred table `category_rules`; relevant columns: `id`, `account_id`, `merchant_id`, `keyword`, `category`, `category_id`, `amount_min`, `amount_max`, `direction`, `source`, `ai_approved`, `created_at`
- Inferred table `transactions`; relevant columns: `category_rule_id`

### Manual edits or audit history
- Inferred table `audit_log`; relevant columns: `id`, `user_id`, `username`, `action`, `details`, `ip_address`, `created_at`
- Inferred table `category_rule_tags`; relevant columns: `rule_id`, `tag_id`
- Inferred table `category_rules`; relevant columns: `source`, `created_at`
- Inferred table `transaction_tags`; relevant columns: `source`, `rule_id`, `assigned_at`
- Inferred table `transactions`; relevant columns: `category_source`, `reviewed_at`

### Review status
- Inferred table `transactions`; relevant columns: `needs_review`, `reviewed_at`

### Tags
- Inferred table `category_rule_tags`; relevant columns: `rule_id`, `tag_id`
- Inferred table `tags`; relevant columns: `id`, `name`, `builtin_key`, `description`, `instruction`, `color`, `created_at`
- Inferred table `transaction_tags`; relevant columns: `transaction_id`, `tag_id`, `source`, `rule_id`, `assigned_at`

### Transaction-category assignment
- Inferred table `category_rules`; relevant columns: `id`, `category`, `category_id`, `source`, `ai_approved`
- Inferred table `transactions`; relevant columns: `category`, `category_id`, `category_source`, `category_confidence`, `category_rule_id`, `category_metadata`, `needs_review`, `reviewed_at`

### Transaction-tag assignment
- Inferred table `category_rule_tags`; relevant columns: `rule_id`, `tag_id`
- Inferred table `tags`; relevant columns: `id`, `name`, `builtin_key`, `description`, `instruction`
- Inferred table `transaction_tags`; relevant columns: `transaction_id`, `tag_id`, `source`, `rule_id`, `assigned_at`

### Transactions
- Inferred table `accounts`; relevant columns: `id`, `account_type`, `paid_from_account_id`
- Inferred table `statement_types`; relevant columns: `id`, `name`, `parser_type`, `import_mode`
- Inferred table `statements`; relevant columns: `id`, `account_id`, `statement_type_id`, `import_status`, `llm_candidate_count`, `uploaded_at`
- Inferred table `transactions`; relevant columns: `id`, `statement_id`, `account_id`, `merchant_id`, `tx_date`, `description`, `amount`, `category`, `category_id`, `needs_review`, `category_source`, `category_confidence`, `category_rule_id`, `category_metadata`, `categorized_at`, `reviewed_at`, `ignored`, `transaction_kind`, `fingerprint`, `created_at`

## Missing or not found

- No methodology concept was completely absent from inferred schema matches.

## Aggregate Counts

- Total transactions: 1296
- Likely UNKNOWN transactions: 126
- Likely needs_review transactions: 131
- Transactions with confidence value: 1171

### Transactions By Category

- `Food`: 333
- `Housing`: 155
- `Rental`: 151
- `UNKNOWN`: 126
- `Transfers`: 106
- `Administrative`: 93
- `Health`: 70
- `Personal`: 67
- `Utilities`: 55
- `Transportation`: 50
- `Income`: 42
- `Work`: 34
- `Entertainment`: 8
- `Travel`: 5
- `Reimbursement`: 1

### Transactions By Tag

- `Grocery`: 191
- `Restaurant`: 129
- `Insurance`: 63
- `Tax`: 28
- `Reimbursable`: 22
- `Service`: 21
- `Investment`: 20
- `Children`: 12
- `Vehicle`: 11
- `Judo`: 10
- `Government`: 7
- `Conference`: 1

### Transactions By Debit/Credit Sign

- `credit_negative`: 323
- `debit_positive`: 973

### Transactions By Statement Type

- `Credit card`: 655
- `Checking account`: 641

### Transactions By Account

- `account_id 1`: 641
- `account_id 2`: 537
- `account_id 3`: 118

### Transactions By Evidence Source

- `rule`: 1096
- `unknown`: 125
- `history`: 35
- `ai`: 25
- `manual`: 15

## Benchmark coverage readiness

- Category candidate examples: 15 category(s) found; 1 below recommended minimum
- Tag candidate examples: 12 tag(s) found; 1 below recommended minimum
- Positive and negative amounts: yes
- Likely `UNKNOWN` examples exist: yes
- Likely `needs_review` examples exist: yes
- Transfer-like cases appear to exist: yes
- Income-like cases appear to exist: yes
- Reimbursement-like cases appear to exist: yes
- Reimbursable-like cases appear to exist: yes
- Rental-like cases appear to exist: yes
- Tax-like cases appear to exist: yes
- Similar-history evidence can be extracted: yes
- Manual or reviewed labels can be treated as high-trust ground truth: yes

## Potential evaluation risks

- Category `Reimbursement` has too few examples: 1
- Tag `Conference` has too few examples: 1
