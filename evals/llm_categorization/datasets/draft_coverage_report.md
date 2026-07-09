# Draft Dataset Coverage Report

- Selected examples: 150
- Adjudication examples: 8
- Source: read-only SQLite extraction with redacted free text.

## Selected examples by category

- `Administrative`: 22
- `Entertainment`: 5
- `Food`: 7
- `Health`: 5
- `Housing`: 15
- `Income`: 11
- `Personal`: 13
- `Reimbursement`: 1
- `Rental`: 28
- `Transfers`: 6
- `Transportation`: 5
- `Travel`: 5
- `UNKNOWN`: 5
- `Utilities`: 12
- `Work`: 10

## Selected examples by tag

- `Children`: 3
- `Conference`: 1
- `Government`: 3
- `Grocery`: 3
- `Insurance`: 3
- `Investment`: 3
- `Judo`: 3
- `Reimbursable`: 3
- `Restaurant`: 3
- `Service`: 6
- `Tax`: 10
- `Vehicle`: 3

## Debit versus credit counts

- `credit`: 32
- `debit`: 118

## Statement type counts

- `Checking account`: 107
- `Credit card`: 43

## Account counts

- `checking`: 107
- `credit_card`: 43

## needs_review counts

- `False`: 145
- `True`: 5

## UNKNOWN expected counts

- UNKNOWN: 5

## Label source counts

- `high_confidence_rule`: 127
- `manual_edit`: 4
- `reviewed`: 15
- `unknown`: 4

## Ambiguity type counts

- `ambiguous_merchant`: 28
- `income_like`: 11
- `noisy_description`: 40
- `reimbursable_like`: 3
- `reimbursement_like`: 1
- `rental_like`: 28
- `straightforward`: 19
- `tax_like`: 10
- `transfer_like`: 5
- `unknown_correct`: 5

## Missing categories and tags

- Categories not covered: Education
- Tags not covered: Donation, Family, Shared, Trip

## Missing benchmark strata

- weak_history
- misleading_history
