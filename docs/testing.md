# Testing

The FinScope test suite is organized by layer and uses strict pytest markers.

## Run the suite

Full suite:

```powershell
$env:PYTHONDONTWRITEBYTECODE = "1"
.\.venv\Scripts\python.exe -B -m pytest
```

By layer:

```powershell
.\.venv\Scripts\python.exe -B -m pytest -m unit
.\.venv\Scripts\python.exe -B -m pytest -m integration
.\.venv\Scripts\python.exe -B -m pytest -m route
.\.venv\Scripts\python.exe -B -m pytest -m smoke
```

Other useful selections:

```powershell
.\.venv\Scripts\python.exe -B -m pytest -m "not slow"
.\.venv\Scripts\python.exe -B -m pytest -m "db and not smoke"
.\.venv\Scripts\python.exe -B -m pytest tests\unit\test_merchant_normalization.py
```

Coverage:

```powershell
.\.venv\Scripts\python.exe -B -m pytest --cov=finance_app --cov-report=term-missing
```

## Markers

- `unit`: isolated helper, parser, presenter, and domain tests.
- `integration`: database-backed service, repository, and workflow tests.
- `route`: Flask route and controller tests.
- `smoke`: high-value end-to-end workflow checks.
- `slow`: currently applied to smoke tests.
- `db`: tests using the database fixture.
- `flask`: tests using Flask app, request context, or test client.

## Layout

```text
tests/
  unit/
  integration/
  routes/
  smoke/
```

Prefer the smallest useful test layer. Use smoke tests for critical cross-layer workflows, not for behavior already covered cleanly by unit, integration, or route tests.

See `tests/README.md` for more detail.
