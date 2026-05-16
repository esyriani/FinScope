**CRITICAL**

- **Category foreign keys are no longer populated on normal writes.**  
  Normal transaction/rule write paths still set cached `category` text but not `category_id`: [upload/workflow.py](D:/Udm/sms/dev/applications/finances/src/finance_app/modules/upload/workflow.py:151), [transactions/repository.py](D:/Udm/sms/dev/applications/finances/src/finance_app/modules/transactions/repository.py:60), [rules/engine.py](D:/Udm/sms/dev/applications/finances/src/finance_app/modules/rules/engine.py:318), [categories/repository.py](D:/Udm/sms/dev/applications/finances/src/finance_app/modules/categories/repository.py:66). Rename/unknown-category sync then updates by `category_id`, so most app-created rows can be missed: [categories/repository.py](D:/Udm/sms/dev/applications/finances/src/finance_app/modules/categories/repository.py:183).  
  **Why it matters:** after removing DB triggers/legacy schema behavior, the stable category identity model is only partially active, which can cause stale category text, broken rename behavior, and weak referential integrity.  
  **Remediation:** add a Core-safe category resolver and populate `category_id` in import, manual assignment, rule application, rule import/save, and LLM/category update paths. Add tests proving rename affects rows created through normal workflows.

- **Statement imports can partially commit data on failure.**  
  `import_statement_transactions_job()` commits `RUNNING`, then commits imported transactions before later status/count/queue work completes: [upload/workflow.py](D:/Udm/sms/dev/applications/finances/src/finance_app/modules/upload/workflow.py:666), [upload/workflow.py](D:/Udm/sms/dev/applications/finances/src/finance_app/modules/upload/workflow.py:689), [upload/workflow.py](D:/Udm/sms/dev/applications/finances/src/finance_app/modules/upload/workflow.py:707).  
  **Why it matters:** a failure after row insertion can leave transactions committed while the statement is marked failed, making retries and audit state unsafe.  
  **Remediation:** keep progress-status commits separate if needed, but wrap import rows plus final counters/state in one transaction. On error, roll back that transaction, then mark the statement failed in a short independent transaction.

- **Duplicate-row handling during import is not PostgreSQL-safe.**  
  `insert_imported_transaction()` catches `SqlAlchemyIntegrityError` inside the active import flow: [upload/workflow.py](D:/Udm/sms/dev/applications/finances/src/finance_app/modules/upload/workflow.py:133).  
  **Why it matters:** SQLite can often continue after a constraint failure, but PostgreSQL marks the whole transaction aborted until rollback. Future PostgreSQL imports could fail after the first duplicate even though SQLite tests pass.  
  **Remediation:** avoid raising integrity errors for expected duplicates, use savepoints around per-row inserts, or introduce a small dialect-aware “insert or ignore” abstraction when non-SQLite support is added.

- **Important uniqueness constraints disappear outside SQLite.**  
  Several integrity indexes are explicitly SQLite-only: recurring pattern uniqueness and category rule duplicate prevention: [tables.py](D:/Udm/sms/dev/applications/finances/src/finance_app/database/tables.py:81), [tables.py](D:/Udm/sms/dev/applications/finances/src/finance_app/database/tables.py:368), [tables.py](D:/Udm/sms/dev/applications/finances/src/finance_app/database/tables.py:377). Tests currently assert they are absent for MySQL instead of requiring an equivalent: [test_sqlalchemy_tables.py](D:/Udm/sms/dev/applications/finances/tests/integration/test_sqlalchemy_tables.py:195).  
  **Why it matters:** MySQL/PostgreSQL would silently permit duplicate rules/patterns that SQLite rejects.  
  **Remediation:** define portable constraints where possible, or isolate dialect-specific equivalents behind a schema adapter and test that every supported dialect enforces the same logical uniqueness.

**IMPORTANT**

- **Money is stored as `Float`.**  
  Amount fields use floating point in transactions, rules, and recurring patterns: [tables.py](D:/Udm/sms/dev/applications/finances/src/finance_app/database/tables.py:167), [tables.py](D:/Udm/sms/dev/applications/finances/src/finance_app/database/tables.py:223), [tables.py](D:/Udm/sms/dev/applications/finances/src/finance_app/database/tables.py:286).  
  **Why it matters:** binary floats are unsafe for financial values and portability semantics vary.  
  **Remediation:** migrate to `Numeric` with fixed scale or integer minor units before serious multi-dialect production use.

- **`db_core_transaction(conn=...)` can accidentally commit or roll back an outer transaction.**  
  The helper commits/rolls back if `conn.in_transaction()` without tracking whether it opened the transaction: [engine.py](D:/Udm/sms/dev/applications/finances/src/finance_app/database/engine.py:128).  
  **Why it matters:** a future nested caller can break atomicity owned by a higher-level workflow.  
  **Remediation:** detect transaction ownership, use `conn.begin()` context managers, or reject externally managed transactional connections unless explicitly supported.

- **Get-or-create/upsert helpers are race-prone.**  
  Several helpers do select-then-insert without handling concurrent unique conflicts: [accounts/repository.py](D:/Udm/sms/dev/applications/finances/src/finance_app/modules/accounts/repository.py:57), [merchants/repository.py](D:/Udm/sms/dev/applications/finances/src/finance_app/modules/merchants/repository.py:96), [categories/taxonomy.py](D:/Udm/sms/dev/applications/finances/src/finance_app/modules/categories/taxonomy.py:181), [settings/runtime.py](D:/Udm/sms/dev/applications/finances/src/finance_app/modules/settings/runtime.py:365).  
  **Why it matters:** multi-worker Flask/background usage can raise avoidable integrity errors.  
  **Remediation:** catch `IntegrityError` and reselect as a portable fallback, or introduce dialect-specific upsert only behind a narrow abstraction.

- **Non-SQLite tests give limited confidence.**  
  The “non-SQLite” init test still uses a SQLite engine: [test_sqlalchemy_engine.py](D:/Udm/sms/dev/applications/finances/tests/integration/test_sqlalchemy_engine.py:153). Test fixtures also retain raw driver SQL helpers: [conftest.py](D:/Udm/sms/dev/applications/finances/tests/conftest.py:120).  
  **Why it matters:** the suite proves the Core migration works on SQLite, but not that MySQL/PostgreSQL behavior is equivalent.  
  **Remediation:** add dialect compilation tests for constraints/types and focused transactional failure tests. Later, add real database CI when those backends become supported.

- **Legacy SQL helper surfaces remain.**  
  `core/sql.py` and `core/periods.py` still expose SQLite-style SQL strings, `?` placeholders, `COLLATE NOCASE`, and `date('now')` expressions: [sql.py](D:/Udm/sms/dev/applications/finances/src/finance_app/core/sql.py:6), [periods.py](D:/Udm/sms/dev/applications/finances/src/finance_app/core/periods.py:110).  
  **Why it matters:** even if not currently used by runtime Core repositories, these helpers invite regressions back to raw SQLite SQL.  
  **Remediation:** delete them when their tests are retired, or mark them deprecated/test-only and replace remaining date/filter needs with Core expressions.

**NICE TO HAVE**

- Date and timestamp columns are mostly `String`, and reporting groups months with string functions: [tables.py](D:/Udm/sms/dev/applications/finances/src/finance_app/database/tables.py:221), [dashboard/queries.py](D:/Udm/sms/dev/applications/finances/src/finance_app/modules/dashboard/queries.py:60). Moving to `Date`/`DateTime` later would improve portability and query correctness.

- Some filters and analytics aggregate in Python after broad scans, especially merchant filtering and rule previews: [filters.py](D:/Udm/sms/dev/applications/finances/src/finance_app/modules/transactions/filters.py:229), [rules/engine.py](D:/Udm/sms/dev/applications/finances/src/finance_app/modules/rules/engine.py:99). This is acceptable for local datasets, but should be tightened before larger multi-user deployments.

- The Core migration itself is mostly coherent: engine lifecycle is centralized, Flask registers the Core lifecycle only, and I did not find runtime `sqlite3` or ORM usage. The isolated SQLite foreign-key PRAGMA in the SQLAlchemy connect event is reasonable for SQLite support.

**Final Assessment**

Overall migration quality is solid mechanically: the app is now largely SQLAlchemy Core end to end, with clean centralized engine handling and repository/query modules mostly following one style.

Confidence for future MySQL/PostgreSQL migration is **medium-low** right now. The main blockers are logical constraints that only exist on SQLite, float money fields, string date modeling, PostgreSQL-unsafe duplicate handling, and tests that do not yet prove non-SQLite equivalence.

Before production deployment, I would prioritize: category ID synchronization, atomic import rollback behavior, duplicate handling without aborting transactions, portable uniqueness guarantees, and focused tests for those paths.