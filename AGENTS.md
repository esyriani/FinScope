# AGENTS.md

## Project context

This is FinScope, a Flask-based personal finance application using Python, SQLite, Bootstrap, ECharts, and pytest.

When modifying this project, preserve the existing architecture, coding style, user interface conventions, and test structure unless the task explicitly requires a change.

## General rules

- Do not change application behavior unless explicitly requested.
- Keep changes focused and minimal.
- Maintain the existing architecture and separation of concerns throughout the codebase.
- Follow the current naming conventions, file organization, route structure, database access patterns, and template organization.
- Prefer clear, maintainable code over clever or overly abstract solutions.
- The readme file gives you a quick overview of code structure, style, and common workflows.

## Documentation rules

All new modules, functions, classes, and public methods must be documented.
Always update the readme file when changes impact its content such as dependencies, file structure, etc.
Favor sentence case over title case in all documentation, readme, and headers in all GUI pages and templates.

### Python modules

Every new Python module must begin with a short module docstring that explains:

1. the module's responsibility,
2. its main collaborators or dependencies,
3. important side effects or assumptions, only if relevant.

Example:

```python
"""Transaction repository helpers.

Provides SQLite query and persistence functions for transaction records.
Callers are responsible for managing database connections and transactions.
"""
````

Do not include author names, dates, version history, or changelog-style information in module preambles.

### Functions and classes

* Document all new functions, classes, Flask routes, database helpers, and service-layer logic.
* Use concise docstrings that explain purpose, parameters, return values, side effects, and important assumptions.
* For Flask routes, document the HTTP behavior, expected request data, session/auth assumptions, redirects, and response format when relevant.
* For database functions, document schema assumptions, transaction behavior, and error cases when relevant.

## Comments

* Add or update comments for non-trivial logic.
* Prefer comments that explain **why** something is done, not merely **what** the code does.
* Do not add obvious comments that restate the code.
* Update stale or misleading comments when modifying nearby code.

## Testing rules

After completing any change:

* Run the full test suite unless it is an editorial change (e.g., documentation, label renaming, formatting).
* Report the exact test command used.
* Fix any failing tests caused by the change.
* Update or revise tests corresponding to application changes.
* Add new tests when introducing new behavior, fixing bugs, or changing edge-case handling.
* Update the documentation if pertinent.

Preferred command:

```bash
pytest
```

Use the project's existing test command if one is already documented.

## GUI and frontend rules

Maintain a consistent theme, style, and behavior across the user interface.

Pay particular attention to consistency in:

* filters,
* tables,
* charts,
* modal dialogs,
* form validation,
* buttons,
* spacing,
* colors,
* loading states,
* empty states,
* error messages.

For Bootstrap templates:

* Reuse existing components and layout patterns.
* Avoid introducing one-off styles unless necessary.
* Keep templates readable and consistent with the rest of the app.

For ECharts:

* Keep chart styling, labels, legends, tooltips, colors, and interaction behavior consistent across dashboards.
* Preserve existing chart initialization and update patterns unless there is a clear reason to change them.

## Architecture rules

Maintain the existing architectural style throughout the codebase.

* Keep route handling, business logic, persistence logic, and presentation concerns separated according to the current project structure.
* Do not move responsibilities between layers unless explicitly requested.
* Avoid duplicating logic across routes, templates, or database helpers.
* Reuse existing helper functions, services, repositories, and template partials when appropriate.
* Keep financial calculations centralized and well documented.

## Change review checklist

Before finishing, verify that:

* new or changed modules have appropriate preambles,
* new or changed functions/classes have docstrings,
* non-trivial code has useful comments,
* related tests were added or updated,
* the full test suite was run,
* UI behavior remains consistent,
* the architecture style was preserved,
* no unrelated behavior was changed.
