# Processing activity

Longer workflows run through a process-local processing runner with persisted
database history.

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

Each processing item records its label, queue, status, result, error details,
timestamps, progress counters, sanitized log entries, cancellation request flag,
and optional undo outcome in the database. Live execution details such as
futures and undo callables remain in memory.

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

Some processing items register undo handlers. Undo is available only when the
item completed successfully and the current process still has the undo callable
and metadata needed to reverse the operation.

Examples include statement uploads and supported review/rule operations.

## Retention and cleanup

FinScope retains terminal processing history for 90 days. Startup cleanup
deletes completed, failed, and cancelled jobs older than that window, along with
their progress log rows. Queued or running rows are never deleted by cleanup;
they are repaired first if they belonged to a previous process.

Progress logs are also capped per processing item so repeated batch updates do
not create unbounded database growth.

## Current limitations

Processing execution is process-local and in memory. When the Flask process
restarts, queued or running processing items in the database are marked failed
because their worker futures no longer exist. Statement imports that were queued
or running are also marked failed during startup so they can be retried or
reprocessed from the upload page.

Persisted history is for visibility and diagnostics. It is not a durable queue,
and processing does not resume automatically after restart.
