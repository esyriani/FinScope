# Troubleshooting

## FinScope starts with no data

Check the configured database URL and SQLite path:

<details open>
<summary>Windows PowerShell</summary>

```powershell
$env:FINANCE_DATABASE_URL
$env:FINANCE_DB_PATH
```

</details>

<details>
<summary>Windows cmd</summary>

```bat
set FINANCE_DATABASE_URL
set FINANCE_DB_PATH
```

</details>

<details>
<summary>macOS</summary>

```bash
echo "$FINANCE_DATABASE_URL"
echo "$FINANCE_DB_PATH"
```

</details>

<details>
<summary>Linux</summary>

```bash
echo "$FINANCE_DATABASE_URL"
echo "$FINANCE_DB_PATH"
```

</details>

If `FINANCE_DATABASE_URL` or `database.url` is set, FinScope uses that SQLite or MySQL database. If no database URL is set, FinScope uses the SQLite path from `FINANCE_DB_PATH` or `database.path`; the default is [runtime/finescope.db](../runtime/finescope.db).

## AI categorization does not run

Verify that `OPENAI_API_KEY` or `api_keys.openai_api_key` is configured.
By default, statement imports leave unknown rows ready for a manual AI run from Uploaded statements or Processing after you review the AI usage estimate.
If Settings > Categorization > Review AI usage is off, statement imports automatically queue AI categorization for remaining unknown rows.

Without a key, unknown transactions remain unknown and can be reviewed manually.

## Uploaded CSV rows are ignored

The parser expects recognizable date, description, and amount/debit/credit columns, or a compact `date,description,amount` shape.

Check statement type direction as well. Credit card and bank account imports normalize signs differently.

Interac e-Transfer history is enrichment-only. Rows are ignored when there is no matching checking-account transaction with the same account, direction, amount, and nearby posting date, or when multiple checking rows match equally well. Upload the checking statement first, then upload the Interac history for the same account name.

Credit card statements should use a credit card account role. If the card is paid from a checking or savings account, set the paid-from account during upload. FinScope then keeps card purchases as expenses and marks matched account-payment rows as payments/transfers so they do not inflate spending or income totals.

If a credit card payment still appears as spending, check that the credit card account has the correct paid-from account and reprocess the credit card statement. Matching uses amount, nearby date, and a description that points to the linked credit card account.

## Uploaded CSV dates look wrong

Slash dates such as `05/12/2026` are ambiguous. During upload, use the preview modal to confirm whether ambiguous slash dates are month-first (`MM/DD/YYYY`) or day-first (`DD/MM/YYYY`) before importing. FinScope stores transaction dates as `YYYY-MM-DD` after parsing.

Quoted ISO dates such as `"2026-04-29"` do not need a date-order selection.

## Duplicate upload blocks retry

FinScope rejects exact duplicate files by checksum. If a previous upload created the statement row but the import job failed, go to Upload > Uploaded statements and use Retry.

Use Reprocess when you want to remove transactions imported from that statement and import them again from the stored statement text.

## Port already in use

Run on another port:

<details open>
<summary>Windows PowerShell</summary>

```powershell
$env:FINANCE_PORT = "5001"
finscope
```

</details>

<details>
<summary>Windows cmd</summary>

```bat
set "FINANCE_PORT=5001"
finscope
```

</details>

<details>
<summary>macOS</summary>

```bash
export FINANCE_PORT=5001
finscope
```

</details>

<details>
<summary>Linux</summary>

```bash
export FINANCE_PORT=5001
finscope
```

</details>
