# 0001. pgvector over a dedicated vector database

- **Status:** Accepted
- **Date:** 2026-10-26

## Context

The agent needs semantic search over conversation chunks, but almost every useful query also filters on structured data: date range, industry, channel, contact reason, churn status. The text-to-SQL tool reads metrics from the same entities. Target scale is in the order of 10^5–10^6 chunks.

## Decision

Store embeddings in PostgreSQL 16 with the pgvector extension, next to conversations, insights, and metric views.

## Alternatives considered

- **Qdrant / Weaviate / Pinecone** – excellent ANN performance and built-in hybrid search, but they add a second store to keep consistent with Postgres. Metadata filters would be duplicated, and joins with insights would happen in application code.
- **FAISS in-process** – fast and simple, but no persistence, no concurrent writers, and no filtering.

## Consequences

- One transactional store: a chunk and its insight are written atomically, and a filter like `started_at >= '2026-09-01' AND contact_reason = 'billing'` lives in the same query as the vector search.
- Full-text search (`tsvector`) is in the same database, which makes hybrid retrieval (ADR 0003) a single SQL round-trip.
- One fewer service to run, back up, and secure.
- ANN tuning is limited to HNSW/IVFFlat parameters. **Revisit** if p95 vector search latency exceeds ~200 ms at target scale, or if the corpus grows past ~10^7 chunks.
