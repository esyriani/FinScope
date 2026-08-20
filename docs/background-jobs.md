# Processing activity

Longer workflows run through the in-memory processing runner.

## Processing types

Common processing activity includes:

- statement import
- AI categorization follow-up
- applying all rules
- review-group operations
- rule import

Statement imports, rules, and review operations use the main background queue.
AI categorization uses a separate AI queue so model timeouts do not block
future imports or other local work.

## State lifecycle

Processing items move through a small lifecycle:

- `queued -> running -> completed`
- `queued -> running -> failed`
- `queued -> cancelled`
- `queued -> running -> cancelled`

Each processing item records its label, payload, status, result, error details, timestamps, and optional undo metadata.

Running processing can only stop cooperatively. AI categorization checks for cancel
requests between batches and records a cancelled result after the active batch
finishes.

## AI categorization controls

Automatic AI categorization after imports is controlled by Settings >
Categorization > Review AI usage. The confirmation step is on by default,
so imports report remaining unknown rows and wait for a manual AI run. When an
owner turns confirmation off, statement imports automatically queue AI
follow-up work for those remaining unknown rows.

Use Processing to queue AI categorization for all active unknown transactions or to
clear queued AI jobs. Use Upload > Uploaded statements to queue AI
categorization for one statement. AI reruns only select active transactions
whose category is still null or `UNKNOWN`, so manual and already categorized
rows are left unchanged.

AI categorization commits after each batch. If the process is interrupted,
rerun AI categorization and processing resumes from the remaining unknown rows.

The transaction-table Suggest category action is an exception to the queue model.
It runs synchronously for one selected transaction, shows the model result and
metadata in a modal dialog, and waits for the user to explicitly apply the
suggestion. The modal can apply only the selected row, or apply the row and save
a reusable rule. Owners can show or hide that action from Settings; its default
visibility is controlled by `setting_defaults.transaction_ai_rerun_enabled`.

## Undo behavior

Some processing items register undo handlers. Undo is available only when the item completed successfully and the handler still has enough metadata to reverse the operation.

Examples include statement uploads and supported review/rule operations.

## Current limitations

The processing runner is process-local and in memory. Processing history, progress, undo metadata, cancellation requests, and errors are lost when the Flask process restarts. Statement imports that were queued or running are marked failed during startup so they can be retried or reprocessed from the upload page.

This is acceptable for the current local FinScope shape, but a shared or hosted deployment should move job state to durable storage.
