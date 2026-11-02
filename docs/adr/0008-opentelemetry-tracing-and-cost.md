# 0008. OpenTelemetry for tracing, and a per-request usage meter for cost

- **Status:** Accepted
- **Date:** 2026-11-01

## Context

An answer touches several model calls (agent turns, text-to-SQL, reranking, embeddings) and several database queries. To debug a bad answer or a slow request we need to see each step with its latency and tokens, and to know what each request and ingestion job costs.

## Decision

- **Tracing with OpenTelemetry.** FastAPI requests (health checks excluded), the agent run, each tool call, every model call and every ingestion job become spans. Model spans follow the OTel GenAI semantic conventions (`gen_ai.operation.name`, `gen_ai.request.model`, `gen_ai.usage.input_tokens`, …). Export is OTLP, configured by the standard `OTEL_EXPORTER_OTLP_*` variables: Jaeger locally (compose profile `observability`), or any OTLP backend in production (Langfuse, Grafana Tempo, Honeycomb, Datadog). A tracer provider is always installed, so logs and responses carry a `trace_id` even when nothing is exported.
- **Cost from a usage meter.** Providers record `(provider, model, kind, tokens)` after every call into a meter bound to a context variable; each API request and ingestion job opens one. Cost comes from a versioned price table (`app/observability/prices.yaml`, overridable with `PRICES_FILE`), matched by the longest model-name prefix. Local and fake providers are free; an unknown model makes the total `null` rather than silently 0.

## Alternatives considered

- **Langfuse SDK directly** – richer LLM-specific UI out of the box, but ties instrumentation to one vendor. Langfuse ingests OTLP, so OpenTelemetry keeps that option open.
- **Logging tokens only** – cheap, but loses the request tree that explains latency and failures.
- **Asking the provider for cost** – not every provider returns it, and the price used for planning should be ours to set.

## Consequences

- Every model call costs one span; at this traffic level the overhead is negligible, and the batch exporter keeps it off the request path.
- Prices drift and were not verified when written: the file says so, and `PRICES_FILE` exists so a deployment can pin its own.
- Embedding usage for the fake provider is approximate (word count); real providers report tokens.
