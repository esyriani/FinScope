# Dataset Split Report

- Source: `evals\llm_categorization\datasets\draft_from_db.jsonl`
- Output directory: `evals\llm_categorization\datasets`
- Seed: `42`
- Requested ratios: dev=0.50, validation=0.30, test=0.20
- Leakage groups: 104
- Multi-example leakage groups: 17

## Intended use

- Development set: prompt design and iteration.
- Validation set: prompt candidate selection.
- Held-out test set: final estimate after prompt selection only.

## Overfitting controls

- Do not tune prompts directly against the held-out test set.
- Do not encode merchant-specific fixes in the prompt when a rule or taxonomy fix is more appropriate.
- Use failure categories to revise prompts, not isolated examples.
- Near-duplicates use normalized merchant, description, and signed amount.
- Repeated merchant-and-amount patterns stay in one split when possible.
- Notes containing `source_request_id=...` keep synthetic variants with their source example.

## Split counts

| Split | Target | Actual | Leakage groups |
| --- | ---: | ---: | ---: |
| dev | 75 | 75 | 44 |
| validation | 45 | 45 | 36 |
| test | 30 | 30 | 24 |

## Leakage check

- No leakage grouping keys cross splits.

## Dataset summary: evals\llm_categorization\datasets\dev.jsonl

- Examples: 75
- Examples needing review: 2
- High-trust labels: 73
- Low-trust labels: 2
- Expected UNKNOWN: 2

### Category coverage
- Administrative (16): 14
- Entertainment (14): 4
- Food (8): 4
- Health (11): 2
- Housing (6): 8
- Income (1): 6
- Personal (12): 6
- Rental (2): 14
- Transfers (5): 2
- Transportation (9): 2
- Travel (13): 2
- UNKNOWN (3): 2
- Utilities (7): 5
- Work (15): 4

### Tag coverage
- Children (3): 1
- Government (13): 2
- Grocery (15): 2
- Insurance (11): 2
- Investment (9): 3
- Judo (16): 1
- Reimbursable (1): 1
- Restaurant (14): 1
- Service (8): 5
- Tax (2): 5
- Vehicle (10): 2

### Directions
- credit: 14
- debit: 61

### needs_review
- false: 73
- true: 2

### Label sources
- high_confidence_rule: 64
- manual_edit: 2
- reviewed: 7
- unknown: 2

### Privacy levels
- redacted_real: 75

### Ambiguity types
- ambiguous_merchant: 14
- income_like: 6
- noisy_description: 21
- reimbursable_like: 1
- rental_like: 14
- straightforward: 10
- tax_like: 5
- transfer_like: 2
- unknown_correct: 2

### Statement types
- Checking account: 53
- Credit card: 22

### Missing categories
- Education (10)
- Reimbursement (4)

### Missing tags
- Conference (6)
- Donation (12)
- Family (4)
- Shared (7)
- Trip (5)

### Warnings
- fewer than 80 examples: 75
- missing built-in concept coverage: Reimbursement
- categories with no examples: 2 candidate value(s) never expected: Education (10), Reimbursement (4)
- tags with no examples: 5 candidate value(s) never expected: Conference (6), Donation (12), Family (4), Shared (7), Trip (5)
- missing ambiguity_type value represented in source: reimbursement_like
- no reimbursement_like cases represented from source

## Dataset summary: evals\llm_categorization\datasets\validation.jsonl

- Examples: 45
- Examples needing review: 2
- High-trust labels: 43
- Low-trust labels: 2
- Expected UNKNOWN: 2

### Category coverage
- Administrative (16): 5
- Food (8): 2
- Health (11): 2
- Housing (6): 5
- Income (1): 3
- Personal (12): 5
- Rental (2): 8
- Transfers (5): 2
- Transportation (9): 2
- Travel (13): 1
- UNKNOWN (3): 2
- Utilities (7): 5
- Work (15): 3

### Tag coverage
- Children (3): 1
- Conference (6): 1
- Government (13): 1
- Grocery (15): 1
- Judo (16): 1
- Reimbursable (1): 1
- Restaurant (14): 1
- Tax (2): 3

### Directions
- credit: 9
- debit: 36

### needs_review
- false: 43
- true: 2

### Label sources
- high_confidence_rule: 37
- manual_edit: 1
- reviewed: 5
- unknown: 2

### Privacy levels
- redacted_real: 45

### Ambiguity types
- ambiguous_merchant: 9
- income_like: 3
- noisy_description: 11
- reimbursable_like: 1
- rental_like: 8
- straightforward: 6
- tax_like: 3
- transfer_like: 2
- unknown_correct: 2

### Statement types
- Checking account: 33
- Credit card: 12

### Missing categories
- Education (10)
- Entertainment (14)
- Reimbursement (4)

### Missing tags
- Donation (12)
- Family (4)
- Insurance (11)
- Investment (9)
- Service (8)
- Shared (7)
- Trip (5)
- Vehicle (10)

### Warnings
- fewer than 80 examples: 45
- missing built-in concept coverage: Reimbursement
- categories with no examples: 3 candidate value(s) never expected: Education (10), Entertainment (14), Reimbursement (4)
- tags with no examples: 8 candidate value(s) never expected: Donation (12), Family (4), Insurance (11), Investment (9), Service (8), Shared (7), Trip (5), Vehicle (10)
- missing ambiguity_type value represented in source: reimbursement_like
- no reimbursement_like cases represented from source

## Dataset summary: evals\llm_categorization\datasets\test.jsonl

- Examples: 30
- Examples needing review: 1
- High-trust labels: 30
- Low-trust labels: 0
- Expected UNKNOWN: 1

### Category coverage
- Administrative (16): 3
- Entertainment (14): 1
- Food (8): 1
- Health (11): 1
- Housing (6): 2
- Income (1): 2
- Personal (12): 2
- Reimbursement (4): 1
- Rental (2): 6
- Transfers (5): 2
- Transportation (9): 1
- Travel (13): 2
- UNKNOWN (3): 1
- Utilities (7): 2
- Work (15): 3

### Tag coverage
- Children (3): 1
- Insurance (11): 1
- Judo (16): 1
- Reimbursable (1): 1
- Restaurant (14): 1
- Service (8): 1
- Tax (2): 2
- Vehicle (10): 1

### Directions
- credit: 9
- debit: 21

### needs_review
- false: 29
- true: 1

### Label sources
- high_confidence_rule: 26
- manual_edit: 1
- reviewed: 3

### Privacy levels
- redacted_real: 30

### Ambiguity types
- ambiguous_merchant: 5
- income_like: 2
- noisy_description: 8
- reimbursable_like: 1
- reimbursement_like: 1
- rental_like: 6
- straightforward: 3
- tax_like: 2
- transfer_like: 1
- unknown_correct: 1

### Statement types
- Checking account: 21
- Credit card: 9

### Missing categories
- Education (10)

### Missing tags
- Conference (6)
- Donation (12)
- Family (4)
- Government (13)
- Grocery (15)
- Investment (9)
- Shared (7)
- Trip (5)

### Warnings
- fewer than 80 examples: 30
- categories with no examples: 1 candidate value(s) never expected: Education (10)
- tags with no examples: 8 candidate value(s) never expected: Conference (6), Donation (12), Family (4), Government (13), Grocery (15), Investment (9), Shared (7), Trip (5)
- missing label_source value represented in source: unknown
