# Authentication and authorization

FinScope is a single-tenant application. One deployment maps to one finance database, and every authenticated user in that deployment sees the same underlying financial data. Create a separate deployment and database when you need a separate finance workspace.

FinScope does not create workspaces, tenant IDs, organizations, or per-user finance databases.

## First owner bootstrap

When the configured database has no active owner, FinScope redirects application requests to `/auth/bootstrap`.

1. Start the app normally.
2. Open the local URL, for example `http://127.0.0.1:5000`.
3. Create the owner username and password in the bootstrap form.
4. Use Users to create editor and viewer accounts.

Only one owner account is supported in a FinScope database. The user-management routes prevent deactivating or directly demoting the owner, and the database enforces the single-owner role invariant. Ownership can only be handed off from the Users page to another active user through a confirmation modal; the previous owner becomes a viewer.

## Roles

Owner:

- Full access to the application.
- Manage users.
- Modify owner-only advanced settings on their own account.
- Import statements, edit transactions, manage rules, manage taxonomy, and undo jobs.

Editor:

- Import statements.
- Edit transactions, review groups, recurring patterns, rules, categories, and tags.
- Cannot manage users.
- Cannot modify owner-only advanced settings.

Viewer:

- Read dashboards, charts, reports, transactions, calendar, recurring views, and personal settings.
- Can change their own display name and password.
- Can edit only their own General settings.
- Cannot mutate finance data or owner-only advanced settings.

## Passwords and sessions

Passwords are hashed with Werkzeug `scrypt` password hashes. Plaintext passwords are never stored.

Owner-created users receive a temporary password and `must_change_password=true`. The owner must provide that temporary password manually. After logging in with it, the user must change the password in the forced login modal before reaching regular application pages.

The owner changes their password from Account. Owner password recovery outside the UI is an administrator maintenance task for the deployment host and database backup process.

Login errors are generic. Failed logins are counted, and accounts are temporarily locked after repeated failures. Session cookies are HttpOnly and SameSite=Lax. Secure cookies are enabled when debug mode is off.

## Settings permissions

All saved runtime settings are stored per user in `user_settings`.

General settings are editable by every authenticated user:

- Theme mode.
- Interface language.
- Personal table and display limits.

Advanced settings are owner-only but still saved on the owner's user account:

- LLM model, categorization thresholds, and single-transaction AI suggestions.
- Recurrence detection defaults.
- Statement import type configuration.

Backend services enforce these permissions. Hidden UI controls are not the security boundary. Non-request jobs and scripts resolve settings through the active owner account when no request user is available.

## Database tables

Authentication adds these SQLAlchemy Core tables:

- `users`: account identity, generated username and owner uniqueness keys, role, password hash, status, lockout, and login tracking.
- `user_settings`: per-user runtime settings, including owner-only advanced settings.
- `audit_log`: security-relevant account events without plaintext passwords.

The `password_hash` column uses `TEXT` on SQLite. MySQL uses `VARCHAR(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL`.

## Deployment notes

Set a strong `FINANCE_SECRET_KEY` before using FinScope beyond local development. Keep debug mode off on shared networks so secure cookie settings are active. Protect the runtime database file and backups because authentication does not encrypt the database at rest.
