# 0003. Hybrid retrieval with Reciprocal Rank Fusion

- **Status:** Proposed – to be accepted or rejected with eval results in phase 4
- **Date:** 2026-10-26

## Context

Customer service text mixes paraphrased complaints ("they charged me for something I never ordered"), where embeddings shine, with exact tokens (plan codes, error messages, product names), where embeddings are weak and keyword search shines.

## Decision

Run vector search (pgvector) and keyword search (Postgres full-text) in parallel and fuse the rankings with Reciprocal Rank Fusion, `score = Σ 1 / (k + rank)` with `k = 60`, then optionally rerank the top-N.

## Alternatives considered

- **Vector only** – simplest; the baseline we measure against.
- **Weighted score sum** – needs calibrating two incomparable score scales and re-tuning when the embedding model changes. RRF uses ranks only.
- **Learned sparse retrieval (SPLADE)** – strong, but another model to host for a gain we have not shown we need.

## Consequences

- No score calibration; one tunable constant.
- Two queries per search, which is acceptable since both run in the same database.
- **Acceptance criterion:** hybrid must beat vector-only on recall@5 in `evals/` by a margin larger than run-to-run noise. If not, this ADR is rejected and recorded as such.
