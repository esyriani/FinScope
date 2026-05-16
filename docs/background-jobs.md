# Background jobs

Longer workflows run through the in-memory background job runner.

## Job types

Common jobs include:

- statement import
- LLM categorization follow-up
- applying all rules
- review-group operations
- rule import

## State lifecycle

Jobs move through a small lifecycle:

```text
queued -> running -> completed
queued -> running -> failed
```

Each job records its label, payload, status, result, error details, timestamps, and optional undo metadata.

## Undo behavior

Some jobs register undo handlers. Undo is available only when the job completed successfully and the handler still has enough metadata to reverse the operation.

Examples include statement uploads and supported review/rule operations.

## Current limitations

The job runner is process-local and in memory. Job history, progress, undo metadata, and errors are lost when the Flask process restarts.

This is acceptable for the current local FinScope shape, but a shared or hosted deployment should move job state to durable storage.
