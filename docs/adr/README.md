# Architecture Decision Records

Each ADR captures one decision that had a real alternative: the context, what we chose, and what we gave up. ADRs are immutable once accepted; a reversal is a new ADR that supersedes the old one.

| #    | Decision                                                        | Status   |
|------|-----------------------------------------------------------------|----------|
| 0001 | [pgvector over a dedicated vector DB](0001-pgvector-over-dedicated-vector-db.md) | Accepted |
| 0002 | [No agent framework](0002-no-agent-framework.md)                | Accepted |
| 0003 | [Hybrid retrieval with RRF](0003-hybrid-retrieval.md)           | Proposed (hybrid is the interim default) |
| 0004 | [Labels-first synthetic data](0004-labels-first-synthetic-data.md) | Accepted |
| 0005 | [Idempotent ingestion with whole-job retries](0005-idempotent-ingestion-and-retries.md) | Accepted |
| 0006 | [One OpenAI-compatible provider](0006-single-openai-compatible-provider.md) | Accepted |
| 0007 | [Guarded text-to-SQL over read-only views](0007-guarded-text-to-sql.md) | Accepted |

Template: [`template.md`](template.md).
