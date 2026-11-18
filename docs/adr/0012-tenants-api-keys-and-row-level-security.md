# 0012. Tenants, per-tenant API keys and row-level security

- **Status:** Accepted
- **Date:** 2026-11-17

## Context

Until now there was one API key and one pool of data. To serve several organizations from one deployment, each one's conversations, customers, insights and metrics must be invisible to the others. That has to hold in every query: hand-written ones, the ORM, retrieval, the worker, and SQL written by a model. A `WHERE tenant_id = ...` in application code is one forgotten filter away from a leak, and text-to-SQL is code we don't write at all.

Callers also need different powers: a CRM sync uploads data, a dashboard reads it, and someone manages the keys.

## Decision

**The database enforces isolation with row-level security (RLS).**
- **Schema.** Every table with customer data (`customers`, `conversations`, `turns`, `chunks`, `insights`, `jobs`) has a `tenant_id` column.
  - **Default value:** the column defaults to the transaction's `app.tenant_id` setting. With no tenant set, the default raises an error, so an insert never lands in a guessed tenant.
  - **Policy:** one policy per table, `tenant_id = app.tenant_id`, applies to both reads (`USING`) and writes (`WITH CHECK`). It is `FORCE`d, so it applies to the table owner as well. With no tenant set, a session sees no rows and can write none.
- **Superusers bypass RLS** (the development database user is one). So tenant-scoped sessions run as `voc_app`, a role with only DML grants on those tables, via `SET LOCAL ROLE voc_app`.
- **Tenant-scoped sessions.** `tenant_sessionmaker(engine, tenant_id)` sets `app.tenant_id` and the role at the start of every transaction, in an `after_begin` hook. Both settings are transaction-local, so a pooled connection never carries them into another request.
  - **API:** every request gets a session scoped to its caller's tenant.
  - **Worker:** jobs carry their tenant in the queue message.
  - **Evals and demo seed:** they use the default tenant.
- **Uniqueness per tenant.** Customer ids and external conversation ids are unique within a tenant, not globally. Two tenants can both have a `conv-1`.
- **Analytics views filter on `app.tenant_id` themselves.** Views run with their owner's privileges, and in development the owner is a superuser that RLS would not limit. The executor sets the tenant for each query. With no tenant, the views return nothing. The SQL guard already rejects `set_config`, so a generated query cannot switch tenants.

**API keys belong to a tenant and have a role.**
- **Format:** `voc_<prefix>_<secret>`, where the secret is 32 random bytes. Only the SHA-256 hash is stored. The key is shown once, when it is created. The prefix identifies a key in listings.
- **Roles:**
  - `ingest`: upload conversations and customers, read jobs.
  - `analyst`: ask, metrics, insights, read conversations.
  - `admin`: everything, plus managing the tenant's keys through `/v1/api-keys`.
  - Each route declares the roles it allows. A key with another role gets `403`; an unknown or revoked key gets `401`.
- **Bootstrap key.** `API_KEY` is an admin of the `default` tenant, which migration `0006` creates and assigns all existing data to. Single-tenant deployments and the web UI's proxy keep working unchanged.
- **Operator CLI.** Tenants are created with `python -m scripts.tenants`, which also issues the first key of each tenant. There is no API to create tenants.

## Alternatives considered

- **Filtering in application code only.** It is simpler, but every query becomes a place to forget the filter, and it can't constrain model-written SQL. We still filter explicitly where it is cheap (the views), but RLS is what guarantees isolation.
- **A schema or database per tenant.** This gives the strongest isolation and per-tenant backups. The cost: migrations and connection pools multiply with the number of tenants, and cross-tenant operations get harder. It is worth it for a few large, regulated tenants, not for many small ones.
- **Setting the tenant per connection (`SET`, not `SET LOCAL`).** A pooled connection would keep the previous request's tenant if a reset ever failed. Transaction-local settings can't outlive the transaction.
- **bcrypt/argon2 for key hashes.** Slow hashes protect low-entropy passwords. A key with 256 random bits can't be brute-forced from its SHA-256. A slow hash would only add latency to every request.
- **JWTs instead of stored keys.** JWTs avoid a database lookup per request, but revoking them needs a deny list or short expiry. Per-user login with OIDC at the proxy is planned next (Phase 10b-2). Stored keys stay for machine clients.

## Consequences

- One indexed lookup per request resolves the key. A short in-process cache can be added if it shows up in latency.
- **Production connection setup.**
  - **Migration user:** owns the tables and is granted `voc_app`, which it can switch to.
  - **Application login:** ideally a separate role that is only a member of `voc_app`, like `voc_analytics` for text-to-SQL (ADR 0007).
  - **Hosted Postgres:** managed services such as Azure Flexible Server have no superuser, which helps.
- **Downgrading 0006** keeps only the default tenant's data, because ids are only unique per tenant.
- **Residual risk.** A session can change `app.tenant_id` with `set_config`. Application code is trusted not to. Model-written SQL is blocked by the guard, and in the shared-engine mode it runs in its own read-only transaction as `voc_analytics`.
- **Tests.**
  - Two tenants with the same external ids and customer ids.
  - Every tenant table is isolated, and cross-tenant writes are rejected.
  - A session without a tenant sees and writes nothing.
  - The analytics views are filtered.
  - Requests and worker jobs are scoped to the tenant.
  - A role matrix covers every endpoint group, plus key lifecycle and storage, and the CLI.
  - Mutation check: removing the role switch makes the isolation tests fail, because the test database user is a superuser.
