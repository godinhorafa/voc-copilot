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

Split in two PRs: **2a** (pipeline) ✅ and **2b** (extraction and real providers) ✅.

### 2a – Upload, queue, chunks, embeddings ✅

- Schema: `customers`, `conversations`, `turns`, `chunks` (pgvector HNSW + `tsvector` GIN), `jobs`
- `POST /v1/conversations` → `202` + `job_id`; idempotent by `external_id` + content hash
- arq worker; whole-job retry with backoff, idempotent per conversation; `partial` status on the last attempt
- Turn-aware chunking with speaker labels and overlap
- `PUT /v1/customers`, `GET /v1/jobs/{id}`, `GET /v1/conversations/{external_id}`
- API key auth on `/v1/*`
- `make seed` (generate + ingest); worker in Compose
- Tests: arq end to end through Redis, model/migration drift check

### 2b – Extraction and providers ✅

- `insights` table; structured extraction (contact reason, sentiment, resolution, competitors, churn risk, summary) with a per-industry schema where the contact reason is an enum of the taxonomy (`app/ingestion/taxonomy.yaml`)
- Extractor version = major + hash of prompt and taxonomy, stored with the model and token usage on every insight
- One OpenAI-compatible provider for OpenAI, Azure OpenAI and Ollama (ADR 0006); chat and embeddings can use different providers
- Pipeline steps tracked separately (`chunked_hash`, insight `content_hash`): a failed extraction keeps the chunks; a retry only extracts
- `make eval-extraction`: per-field accuracy and macro-F1 vs. ground truth, competitor precision/recall, majority-class baseline, Markdown + JSON report

**Exit:** extraction eval runs end to end (verified with an oracle and with placeholder answers in tests). **Pending:** first report with a real model, which needs an API key.

**Follow-ups:**
- Re-extraction when the extractor version changes (today a version bump only applies to new or changed conversations)
- LLM-rendered dataset for retrieval evals (from phase 1)

## Phase 3 – Minimal RAG and eval baseline ✅

- Vector search with industry/date filters; pgvector iterative HNSW scan so selective filters still return k rows
- `POST /v1/ask`: retrieve → numbered sources → structured answer with citations; no LLM call when nothing is retrieved
- Citation validation: a citation must name a retrieved source and quote text that appears in it (normalized for case, whitespace, typographic quotes); failures are dropped after one retry with feedback
- Golden set (45 questions for `telecom-seed42-n500`) derived from ground truth; a test fails if it drifts from the dataset
- `make eval-retrieval`: MRR, precision@k, recall@k (normalized by min(k, |relevant|)), by question type

**Exit:** first row of the README results table filled with the offline baseline (hashed bag-of-words embeddings): MRR@10 0.387, recall@10 0.268. **Pending:** the same eval with real embeddings (needs an API key).

## Phase 4 – Hybrid retrieval ✅ (pending real-embedding numbers)

- Keyword search: OR of the question's stemmed terms (quoted, so query syntax can't be injected), ranked with `ts_rank_cd`
- Reciprocal Rank Fusion (k = 60) of vector and keyword rankings; `Retriever` selects vector | keyword | hybrid, optionally followed by an LLM reranker (graded relevance in one call)
- `RETRIEVAL_STRATEGY` / `RETRIEVAL_RERANK` settings; `/v1/ask` reports which strategy answered
- `make eval-retrieval ARGS="--strategy …"`; results table filled for the offline embeddings: keyword 0.826 MRR, hybrid 0.722, vector 0.387
- ADR 0003 stays **Proposed**: the offline embeddings can't test it. Hybrid is the interim default because keyword-only returns nothing for questions without shared terms

**Pending (needs an API key):** vector/hybrid with real embeddings, rerank, and paraphrased golden questions; then accept or reject ADR 0003.

## Phase 5 – Text-to-SQL ✅ (pending real-model accuracy)

- `analytics` schema with three views (conversations + insights, customers, competitor mentions) and a `voc_analytics` role that can only read them
- `sql_guard` on sqlglot: one SELECT, no DML/DDL anywhere (including CTEs), allowed views and functions only, no `INTO` or row locks, forced `LIMIT`, SQL regenerated without comments
- Executor: READ ONLY transaction, `statement_timeout`, always rolled back, runs as the analytics role; `ANALYTICS_DATABASE_URL` for a dedicated least-privilege login in production
- `POST /v1/metrics`: question → SQL → rows, with one repair round on guard or database errors
- Adversarial tests per layer, with the other layers bypassed (ADR 0007)
- `make eval-sql`: execution accuracy on 18 golden questions; verified with an oracle (1.0) and with swapped answers (< 0.3)

**Pending (needs an API key):** first accuracy report with a real model.

## Phase 6 – Agent ✅ (pending real-model evals)

- Tool calling added to the provider protocol (strict JSON schemas from Pydantic) for OpenAI-compatible providers and the fake
- `app/agent/`: explicit loop with `search_conversations`, `query_metrics`, `final_answer`; evidence numbered across the run ([S#], [M#]); final citations and metric references verified, with one retry then drop; step budget that ends in `final_answer`; per-step trace
- `/v1/ask` runs the agent by default (`mode: "rag"` for the plain baseline); request filters are passed as scope; `?stream=true` streams `tool_start` / `tool_end` / `answer` events (progress, not tokens: the answer arrives through a tool)
- `make eval-agent`: routing accuracy, over-calling, citation validity, and faithfulness by LLM-as-judge with `JUDGE_MODEL` (18 questions: metrics-only, search-only, both)

**Pending (needs an API key):** first agent report with a real model and judge.

## Phase 7 – Observability and cost

- Langfuse / OpenTelemetry spans per tool call; per-model price table; real `usage` in responses

## Phase 8 – Evals in CI

- Cached LLM responses on a fixed subset; report posted on each PR; thresholds that fail regressions

## Phase 9 – Hardening and extras

- PII redaction before embedding, multi-tenancy with row-level security, rate limiting
- React UI built with **shadcn/ui** (Tailwind, Radix): modern layout, accessible components, dark mode. Chat with streamed answers and clickable citations that open the source conversation; insights dashboard with filters
- Deploy guide for Azure Container Apps
