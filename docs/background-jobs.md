# Background jobs

Longer workflows run through the in-memory background job runner.

## Job types

Common jobs include:

- statement import
- LLM categorization follow-up
- applying all rules
- review-group operations
- rule import

Statement imports, rules, and review operations use the main background queue.
LLM categorization uses a separate AI queue so model timeouts do not block
future imports or other local work.

## State lifecycle

Jobs move through a small lifecycle:

```text
queued -> running -> completed
queued -> running -> failed
queued -> cancelled
queued -> running -> cancelled
```

Each job records its label, payload, status, result, error details, timestamps, and optional undo metadata.

Running jobs can only stop cooperatively. AI categorization checks for cancel
requests between batches and records a cancelled result after the active batch
finishes.

## AI categorization controls

Automatic AI categorization after imports is off by default. Owners can turn it
on or off from Settings. Turning it off does not disable manual categorization;
it only stops statement imports from queueing AI follow-up work automatically.

Use Jobs to queue AI categorization for all active unknown transactions or to
clear queued AI jobs. Use Upload > Uploaded statements to queue AI
categorization for one statement. AI reruns only select active transactions
whose category is still null or `UNKNOWN`, so manual and already categorized
rows are left unchanged.

AI categorization commits after each batch. If the process is interrupted,
rerun AI categorization and the job resumes from the remaining unknown rows.

The transaction-table Suggest category action is an exception to the queue model.
It runs synchronously for one selected transaction, shows the model result and
metadata in a modal dialog, and waits for the user to explicitly apply the
suggestion. The modal can apply only the selected row, or apply the row and save
a reusable rule. Owners can show or hide that action from Settings; its default
visibility is controlled by `setting_defaults.transaction_ai_rerun_enabled`.

## Undo behavior

Some jobs register undo handlers. Undo is available only when the job completed successfully and the handler still has enough metadata to reverse the operation.

Examples include statement uploads and supported review/rule operations.

## Current limitations

The job runner is process-local and in memory. Job history, progress, undo metadata, cancellation requests, and errors are lost when the Flask process restarts.

This is acceptable for the current local FinScope shape, but a shared or hosted deployment should move job state to durable storage.
