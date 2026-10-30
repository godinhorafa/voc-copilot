# 0007. Guarded text-to-SQL over read-only views

- **Status:** Accepted
- **Date:** 2026-10-29

## Context

Quantitative questions ("how many customers churned each month?") are answered by having an LLM write SQL. The SQL is untrusted: the model can be wrong, and a question can be crafted to make it write something harmful (prompt injection through the question or through conversation text). The database also holds data the analytics layer shouldn't expose, like raw turns or jobs.

## Decision

Defense in depth, so that no single layer has to be perfect:

1. **Surface.** Generated SQL may only read three views in an `analytics` schema (`conversations` with insights, `customers`, `competitor_mentions`). The views are described to the model from one catalog (`app/analytics/catalog.py`), which a test checks against the real view columns.
2. **Static guard** (`app/analytics/sql_guard.py`, sqlglot):
   - exactly one statement, and it is a `SELECT`;
   - no DML or DDL anywhere in the tree, which catches `WITH x AS (DELETE … RETURNING *) SELECT`;
   - only allowed views, with CTE names excepted;
   - only allowlisted functions, which stops `set_config`, `pg_sleep`, `pg_read_file` and `dblink`;
   - no `SELECT INTO` and no row locks;
   - a forced `LIMIT`.

   The SQL that runs is regenerated from the checked tree, without comments.
3. **Database.** Each query runs in a `READ ONLY` transaction with `statement_timeout`, always rolled back, as the `voc_analytics` role, which has `SELECT` on the analytics views and nothing else.
   - In production, text-to-SQL connects with `ANALYTICS_DATABASE_URL` as a login user that is only a member of `voc_analytics`, so there is no other role to switch to.
   - In development the main connection does `SET LOCAL ROLE`. A query could call `set_config('role', …)` to switch back, but privileges are checked when a statement starts, the driver runs one statement per call, and the transaction is rolled back right after. Tests demonstrate both modes.
4. **Repair loop.** A guard rejection or database error is sent back to the model once, as feedback.

Creating the production login user is an operational step, not a migration, because the password belongs in a secret store:

```sql
CREATE ROLE voc_analytics_login LOGIN PASSWORD '<from your secret store>' IN ROLE voc_analytics;
```

## Alternatives considered

- **Guard only** – a parser allowlist will eventually miss something (dialect quirks, new functions); the database must refuse independently.
- **Database permissions only** – correct, but the model gets opaque permission errors instead of actionable feedback, and expensive queries still reach the database.
- **Predefined metric endpoints only** – safe and fast, but every new question needs code. A semantic layer (dbt metrics, Cube) is the scale-up path if the question space stabilizes.
- **Materialized views** – faster, but stale between refreshes; plain views are fast enough at this size.

## Consequences

- Adversarial tests cover each layer on its own: `tests/unit/test_sql_guard.py` for the guard, and `tests/integration/test_analytics.py` for the database layer, with the guard bypassed.
- The function allowlist will need additions as real questions arrive; each one is a one-line change with a test.
- Answers about insight columns are only as good as extraction (phase 2b). The SQL eval compares against reference queries on the same data, so it measures SQL generation separately.
