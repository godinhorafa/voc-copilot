"""Retrieval strategies behind one interface.

* vector   - pgvector cosine search
* keyword  - PostgreSQL full-text search
* hybrid   - both, fused with Reciprocal Rank Fusion

Optionally followed by LLM reranking of the fused candidates. Each strategy is
measured in `evals/retrieval_eval.py`; see the results table in the README.
"""

from dataclasses import replace
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from app.llm.base import EmbeddingProvider
from app.retrieval.fusion import rrf
from app.retrieval.keyword import keyword_search
from app.retrieval.rerank import LLMReranker
from app.retrieval.types import RetrievedChunk, SearchFilters
from app.retrieval.vector import vector_search

Strategy = Literal["vector", "keyword", "hybrid"]
STRATEGIES: tuple[Strategy, ...] = ("vector", "keyword", "hybrid")


class Retriever:
    def __init__(
        self,
        embeddings: EmbeddingProvider,
        *,
        strategy: Strategy = "hybrid",
        reranker: LLMReranker | None = None,
        candidates: int = 30,
    ) -> None:
        self._embeddings = embeddings
        self.strategy = strategy
        self._reranker = reranker
        self._candidates = candidates

    @property
    def name(self) -> str:
        return f"{self.strategy}+rerank" if self._reranker else self.strategy

    async def search(
        self,
        session: AsyncSession,
        query: str,
        *,
        k: int,
        filters: SearchFilters,
        query_embedding: list[float] | None = None,
    ) -> list[RetrievedChunk]:
        # With a reranker (or fusion) we need a deeper candidate pool than k.
        pool = max(k, self._candidates) if (self._reranker or self.strategy == "hybrid") else k

        async def vector() -> list[RetrievedChunk]:
            embedding = query_embedding
            if embedding is None:
                [embedding] = await self._embeddings.embed([query])
            return await vector_search(session, embedding, k=pool, filters=filters)

        if self.strategy == "vector":
            candidates = await vector()
        elif self.strategy == "keyword":
            candidates = await keyword_search(session, query, k=pool, filters=filters)
        else:
            # One AsyncSession can't run two queries at once, so these run in sequence.
            dense = await vector()
            sparse = await keyword_search(session, query, k=pool, filters=filters)
            by_id = {c.chunk_id: c for c in [*dense, *sparse]}
            fused = rrf([[c.chunk_id for c in dense], [c.chunk_id for c in sparse]])
            candidates = [replace(by_id[chunk_id], score=score) for chunk_id, score in fused[:pool]]

        if self._reranker:
            return await self._reranker.rerank(query, candidates, k)
        return candidates[:k]
