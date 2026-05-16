# Test suite layout

Tests are organized by the layer they primarily exercise:

- `unit/`: isolated helpers, parsers, presenters, and pure domain logic.
- `integration/`: database-backed workflows, repositories, services, and cross-module behavior.
- `routes/`: Flask route/controller tests using the test client.
- `smoke/`: high-value end-to-end workflows across routes, background jobs, and persistence.

Pytest markers are assigned automatically from the directory layout. Useful commands:

```powershell
.\.venv\Scripts\python.exe -B -m pytest -m unit
.\.venv\Scripts\python.exe -B -m pytest -m integration
.\.venv\Scripts\python.exe -B -m pytest -m route
.\.venv\Scripts\python.exe -B -m pytest -m "not slow"
.\.venv\Scripts\python.exe -B -m pytest tests\smoke
.\.venv\Scripts\python.exe -B -m pytest --cov=finance_app --cov-report=term-missing
```

Capability markers are also added automatically:

- `db`: tests using the database fixture.
- `flask`: tests using the Flask app, request context, or test client.
- `slow`: currently applied to smoke tests.
