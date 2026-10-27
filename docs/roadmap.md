# Roadmap

Each phase is a vertical slice that ends with something runnable and, from phase 2 on, measured. A phase is done when its exit criteria hold in CI.

## Phase 0 – Foundation ✅

- uv, ruff, mypy (strict), pytest; pre-commit; Makefile
- Docker Compose: Postgres 16 + pgvector, Redis, one-shot `migrate`, API with healthcheck
- Settings with fail-fast validation, structured JSON logs with request ids
- `/health` (liveness) and `/ready` (Postgres + Redis, 503 when degraded)
- Alembic (async) with a round-trip test
- `LLMProvider` / `EmbeddingProvider` protocols with deterministic fakes
- CI: lint, type-check, tests with Testcontainers, Docker build

**Exit:** `make up` gives a healthy stack and `make check` passes from a clean clone.

## Phase 1 – Synthetic data generator ✅

- `scenarios.yaml`: telecom, banking, and e-commerce, with contact reasons, outcomes, sentiment, competitor mention rates, and fictional brands
- Customers with a timeline (signup, plan, churn) and **injected events**: telecom price increase (Sep) and network outage (Oct 14–16); banking app outage (Aug) and fee increase (Oct); e-commerce carrier backlog (late Nov)
- Labels are sampled first, then rendered to text: phrase-bank templates (deterministic) or an LLM with label/text consistency checks and retry-with-feedback
- Labels live in `ground_truth.jsonl`, separate from the ingestion payload
- `telecom-seed42-n500` committed, with a test that fails when it drifts from the config

**Exit:** `make generate` is reproducible and each conversation has ground-truth labels. Tests assert the event effects hold across seeds (e.g. September billing contacts > 1.6x the Jun–Aug average).

**Follow-up (after phase 2 wires a real provider):** generate an LLM-rendered dataset for retrieval evals; template text is too formulaic to measure retrieval honestly.

## Phase 2 – Ingestion

- Schema: `customers`, `conversations`, `turns`, `chunks`, `insights`, `jobs`
- `POST /v1/conversations` → `202` + `job_id`; arq worker with retries and backoff
- Idempotency via `external_id` + `content_hash`
- Turn-aware chunking; embeddings; structured extraction validated by Pydantic, with `extractor_version` stored
- Real providers: OpenAI, Azure OpenAI, Ollama
- API key auth on `/v1/*`

- `make seed`: generate + ingest

**Exit:** 500 conversations ingested; extraction F1 per field reported against ground truth.

## Phase 3 – Minimal RAG and eval baseline

- Vector search only; `/v1/ask` without an agent (retrieve → answer with citations)
- Citation validation: every cited id must be in the retrieved set
- Golden set (30–50 questions) derived from ground truth; recall@k and MRR

**Exit:** first row of the README results table filled.

## Phase 4 – Hybrid retrieval

- Postgres full-text search, Reciprocal Rank Fusion, reranking
- One results-table row per step; accept or reject ADR 0003 on the numbers

## Phase 5 – Text-to-SQL

- Read-only views and a `voc_readonly` role; `statement_timeout`
- `sql_guard` built on sqlglot: single `SELECT`, view allowlist, forced `LIMIT`, no dangerous functions
- Adversarial tests (DDL, stacked statements, CTEs hiding base tables, `pg_sleep`, catalog access)
- SQL accuracy eval comparing result sets

## Phase 6 – Agent

- Explicit tool-calling loop with iteration and token budgets (ADR 0002)
- Routing between `search_conversations` and `query_metrics`
- SSE streaming; faithfulness and relevance evals (LLM-as-judge, different model from the generator)

## Phase 7 – Observability and cost

- Langfuse / OpenTelemetry spans per tool call; per-model price table; real `usage` in responses

## Phase 8 – Evals in CI

- Cached LLM responses on a fixed subset; report posted on each PR; thresholds that fail regressions

## Phase 9 – Hardening and extras

- PII redaction before embedding, multi-tenancy with row-level security, rate limiting
- Minimal React UI; deploy guide for Azure Container Apps
