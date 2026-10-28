# 0003. Hybrid retrieval with Reciprocal Rank Fusion

- **Status:** Proposed. Hybrid is the default (see "Interim decision"), but acceptance waits for results with real embeddings.
- **Date:** 2026-10-26, updated 2026-10-29

## Context

Customer service text mixes paraphrased complaints ("they charged me for something I never ordered"), where embeddings shine, with exact tokens (plan codes, error messages, product names), where embeddings are weak and keyword search shines.

## Decision (proposed)

Run vector search (pgvector) and keyword search (Postgres full-text) and fuse the rankings with Reciprocal Rank Fusion, `score = Σ 1 / (k + rank)` with `k = 60`, then optionally rerank the top candidates with an LLM.

## Alternatives considered

- **Vector only** – simplest; the baseline we measure against.
- **Weighted score sum** – needs calibrating two incomparable score scales and re-tuning when the embedding model changes. RRF uses ranks only.
- **Learned sparse retrieval (SPLADE)** – strong, but another model to host for a gain we have not shown we need.
- **Local cross-encoder for reranking** – no per-query LLM cost, but adds PyTorch and a model to the image. The LLM reranker needs no new infrastructure; revisit if reranking proves worth it and its cost matters.

## Evidence so far

`make eval-retrieval`, 45 golden questions on `telecom-seed42-n500`, k = 10, with the **offline hashed bag-of-words embeddings** (no API key available yet):

| Strategy | MRR@10 | Precision@10 | Recall@10 |
|----------|-------:|-------------:|----------:|
| vector | 0.387 | 0.240 | 0.268 |
| keyword | **0.826** | **0.656** | **0.768** |
| hybrid (RRF) | 0.722 | 0.520 | 0.594 |

Keyword beats hybrid here, and that is expected. The "dense" side is a bag of hashed tokens with no stemming or stopword removal, dominated by greetings that appear in every conversation; RRF gives this weak ranking equal weight and dilutes the strong one. Template-rendered transcripts also share vocabulary with the golden questions, which flatters keyword search. **This does not test the hypothesis**: the hypothesis is about real semantic embeddings.

## Interim decision

Default `RETRIEVAL_STRATEGY=hybrid`, not `keyword`, because keyword-only search returns **no candidates** when a question shares no terms with the conversations ("What about prices?" against transcripts that say "my bill went up"). The golden set doesn't capture this, since its questions reuse the transcripts' vocabulary; `tests/integration/test_keyword_search.py` pins the behavior.

## To accept or reject

1. Run the same eval with `text-embedding-3-small` (vector, keyword, hybrid) and with `--rerank`.
2. Add paraphrased golden questions (or an LLM-rendered dataset) so the eval rewards semantic matching.
3. Accept if hybrid beats both single retrievers on MRR and recall by more than run-to-run noise; otherwise set the default to the winner and record it here.

## Consequences

- No score calibration; one tunable constant.
- Two queries per search, run in sequence on one session (an `AsyncSession` can't run them concurrently); both are index scans in the same database.
- Reranking adds one LLM call per question (latency and cost), so it is off by default (`RETRIEVAL_RERANK=false`) until it shows a gain.
