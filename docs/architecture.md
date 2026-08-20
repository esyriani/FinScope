# Architecture

FinScope is organized as a Flask application with feature modules, fully supported SQLite or MySQL persistence, Jinja templates, and browser-side JavaScript for page behavior.

## Application architecture diagram

The diagram shows the stable runtime boundaries reflected by `finance_app.__init__.create_app`, the registered blueprints in `finance_app.modules`, and the repository-level [runtime/](../runtime/) data directory.

![Application architecture diagram](diagrams/architecture-diagram.png)

The editable Mermaid source is stored in [docs/diagrams/architecture.mmd](diagrams/architecture.mmd). Keep the rendered image generated from that source when the architecture changes.

## Module structure

Feature code lives under [src/finance_app/modules/](../src/finance_app/modules/). A module should keep presentation, logic, and persistence concerns separate where practical.

Common files, with representative examples:

| File | Responsibility |
| --- | --- |
| [controller.py](../src/finance_app/modules/transactions/controller.py) | Flask routes and HTTP request/response handling. |
| [service.py](../src/finance_app/modules/transactions/service.py) | Use-case orchestration and page context builders. |
| [forms.py](../src/finance_app/modules/rules/forms.py) | Form parsing and validation. |
| [presenter.py](../src/finance_app/modules/transactions/presenter.py) | View-model shaping for templates. |
| [queries.py](../src/finance_app/modules/transactions/queries.py) | Read-side SQL. |
| [repository.py](../src/finance_app/modules/transactions/repository.py) | Mutation persistence helpers. |
| [workflow.py](../src/finance_app/modules/upload/workflow.py) | Multi-step application workflows. |
| [engine.py](../src/finance_app/modules/rules/engine.py) | Domain logic that is independent from Flask. |
| [client_i18n.py](../src/finance_app/modules/rules/client_i18n.py) | Browser-facing message ids owned by feature scripts. |

Not every module needs every file. Small modules can stay compact, but new behavior should not push SQL, form parsing, domain decisions, and template shaping into one controller.

## Layering expectations

- Presentation layer: Flask controllers, Jinja templates, static assets, and presenters.
- Logic layer: services, workflows, engines, form parsers, and orchestration code.
- Data layer: schema code, SQL query helpers, repositories, and persistence utilities.

Controllers should parse HTTP inputs, call the logic layer, and return redirects or rendered templates. Services and workflows should own business decisions. Query and repository helpers should own SQL details.

## Boundary catalog

Shared domain catalogs that must be used by both low-level SQL helpers and feature modules live under [src/finance_app/core/](../src/finance_app/core/). Startup seed orchestration lives under [src/finance_app/database/](../src/finance_app/database/) and must not import feature modules from [src/finance_app/modules/](../src/finance_app/modules/).

Database seeding is an initialization/bootstrap responsibility, not a request-time read fallback. Category option readers, settings page context builders, and other presentation-facing helpers should return explicit defaults or fallbacks when data is unavailable, but they should not silently create runtime settings or built-in taxonomy rows during ordinary template or JSON request handling.

The Flask app factory in [src/finance_app/__init__.py](../src/finance_app/__init__.py) owns application construction: configuration, database registration, auth, filters, assets, CSRF, client-i18n registration, and blueprint registration. It should not perform feature-owned database reads or writes inside template context processors.

Request-wide template context belongs in [src/finance_app/runtime_context.py](../src/finance_app/runtime_context.py). It loads database-backed UI settings and built-in category exclusions once per request, stores them on `g`, and exposes template-safe values without seeding taxonomy or opening feature-owned transactions during template rendering.

Database initialization in [src/finance_app/database/connection.py](../src/finance_app/database/connection.py) creates empty databases from Core metadata, validates existing schema, seeds runtime settings, statement types, and built-in taxonomy, then calls [src/finance_app/database/runtime_repair.py](../src/finance_app/database/runtime_repair.py) for persisted runtime state that cannot survive process-local workers.

Runtime setting key ownership lives in [src/finance_app/core/runtime_settings.py](../src/finance_app/core/runtime_settings.py). Personal UI preferences such as theme, language, and page sizes resolve for the active user. Owner-managed application behavior such as AI model and thresholds, token confirmation, AI rerun behavior, and recurrence detection settings must use owner/global runtime-setting readers.

Runtime settings readers should use caller-owned SQLAlchemy Core connections or an explicit request-scoped context provider. Avoid convenience readers that open their own database connection inside low-level settings runtime code; they make request/render data flow harder to trace and bypass the established transaction boundary.

Built-in taxonomy product semantics live in [src/finance_app/core/builtin_taxonomy.py](../src/finance_app/core/builtin_taxonomy.py). Database taxonomy seeding and upserts live in [src/finance_app/database/taxonomy.py](../src/finance_app/database/taxonomy.py). Feature code should ask taxonomy metadata or behavior helpers instead of hard-coding category or tag labels.

Shared analytics contracts such as quick-view values, report measures, cash-flow summaries, data-quality summaries, and generic cleaned-query URL helpers live under [src/finance_app/core/](../src/finance_app/core/). Dashboard and Reports can reuse those contracts, but neither feature should import the other's presenter, constants, or service modules for shared analytics behavior.

Recurring activity detection, source queries, and recurring activity view models are owned by [src/finance_app/modules/recurring/](../src/finance_app/modules/recurring/). Calendar and Home pages can consume that read model, but recurring workflows should not depend on calendar service internals.

Rules engine code in [src/finance_app/modules/rules/engine.py](../src/finance_app/modules/rules/engine.py) is pure domain logic. Rule SQL, persistence, preview presentation, background apply/undo workflows, and route orchestration belong in the rules query, repository, presenter, workflow, service, and controller modules.

Controllers should not open `db_core_transaction()` or own multi-step business workflows. Route modules collect HTTP inputs and handle Flask concerns; services and workflows own validation decisions, transaction scopes, persistence orchestration, and reusable result objects.

Background jobs are intentionally process-local and in memory. Durable domain status that points at background work needs a startup/runtime reconciliation path, such as statement import recovery in `database/runtime_repair.py` and the upload/background workflow boundary.

External provider boundaries must be injectable. LLM categorization and Settings model validation depend on passed request/client/provider collaborators or small adapters; route-facing services should not construct provider clients directly.

LLM categorization uses an explicit database/provider/database sequence. Rule and historical evidence plus prompt context are prepared in a short transaction, the OpenAI-compatible request runs after that transaction is released, and validated results are applied in a short write transaction owned by the caller. Use [src/finance_app/modules/categories/llm_workflow.py](../src/finance_app/modules/categories/llm_workflow.py) for request-handled or background workflows that call the default provider.

Statement import type configuration is owned by
[src/finance_app/modules/statements/types.py](../src/finance_app/modules/statements/types.py).
The Settings page may edit those rows and Upload may read them for parser and
import-mode validation, but runtime settings resolution should stay focused on
user and owner-managed key/value settings.

## Static architecture tests

Architecture boundaries are intentionally protected by small unit tests:

- [tests/unit/test_import_boundaries.py](../tests/unit/test_import_boundaries.py) guards low-level package imports, dashboard/report and recurring/calendar independence, controller transaction ownership, rules engine purity, settings runtime ownership, and lazy seed writes.
- [tests/unit/test_module_size.py](../tests/unit/test_module_size.py) keeps refactored modules below reviewable sizes after large-file splits.
- [tests/unit/test_frontend_initializers.py](../tests/unit/test_frontend_initializers.py) checks browser initialization patterns and verifies that client translation messages come from the registry instead of the app factory.
- [tests/unit/test_runtime_context.py](../tests/unit/test_runtime_context.py) verifies request-scoped runtime template context caching and defaults.
- [tests/unit/test_settings_openai_model_validation.py](../tests/unit/test_settings_openai_model_validation.py) and [tests/unit/test_network_guard.py](../tests/unit/test_network_guard.py) protect injectable external-provider behavior and prevent accidental network calls in tests.

## User interface text

User-facing static text should go through `finance_app.core.i18n` using English source text as the message id. Templates use the global `_()` helper, Python routes can use `gettext()`, and browser-side scripts use `window.financeTranslate()`.

Translation catalogs are JSON files under [src/finance_app/translations/](../src/finance_app/translations/). Keep user data, merchant names, category names, account names, and uploaded statement content untranslated.

Shared browser message ids live in [src/finance_app/core/client_i18n.py](../src/finance_app/core/client_i18n.py). Feature-owned browser message ids live in `src/finance_app/modules/<feature>/client_i18n.py` and are aggregated by [src/finance_app/modules/client_i18n.py](../src/finance_app/modules/client_i18n.py). The app factory registers the aggregate catalog but should not own feature-specific browser strings.

## Current module examples

- [src/finance_app/modules/transactions/](../src/finance_app/modules/transactions/): [controller.py](../src/finance_app/modules/transactions/controller.py), [service.py](../src/finance_app/modules/transactions/service.py), [filters.py](../src/finance_app/modules/transactions/filters.py), [queries.py](../src/finance_app/modules/transactions/queries.py), [repository.py](../src/finance_app/modules/transactions/repository.py), [presenter.py](../src/finance_app/modules/transactions/presenter.py), and [importer.py](../src/finance_app/modules/transactions/importer.py).
- [src/finance_app/modules/rules/](../src/finance_app/modules/rules/): [controller.py](../src/finance_app/modules/rules/controller.py), [service.py](../src/finance_app/modules/rules/service.py), [forms.py](../src/finance_app/modules/rules/forms.py), [engine.py](../src/finance_app/modules/rules/engine.py), [queries.py](../src/finance_app/modules/rules/queries.py), [repository.py](../src/finance_app/modules/rules/repository.py), [presenter.py](../src/finance_app/modules/rules/presenter.py), [workflow.py](../src/finance_app/modules/rules/workflow.py), [import_export.py](../src/finance_app/modules/rules/import_export.py), and [listing.py](../src/finance_app/modules/rules/listing.py).
- [src/finance_app/modules/categories/](../src/finance_app/modules/categories/): deterministic categorization lives in [categorization.py](../src/finance_app/modules/categories/categorization.py), provider request shaping in [llm.py](../src/finance_app/modules/categories/llm.py), database/provider/database orchestration in [llm_workflow.py](../src/finance_app/modules/categories/llm_workflow.py), and persistence helpers in [repository.py](../src/finance_app/modules/categories/repository.py).

## Runtime boundaries

Runtime state belongs outside source-controlled code:

- SQLite databases
- MySQL databases
- uploaded statements
- generated logs
- backups
- local secrets

Keep those in [runtime/](../runtime/) or another protected local path.
