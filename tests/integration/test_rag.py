import json
from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.answering.rag import NO_EVIDENCE
from app.llm.fake import FakeEmbeddingProvider, FakeLLMProvider
from app.retrieval.types import SearchFilters
from app.retrieval.vector import vector_search
from tests.factories import conversation
from tests.helpers import DATASET, ingest_payloads

pytestmark = pytest.mark.integration

BILLING = conversation(
    "billing",
    texts=("Hi", "I was charged twice on my invoice this month", "I've refunded the charge"),
    started_at="2026-09-03T10:00:00",
)
NETWORK = conversation(
    "network",
    texts=("Hi", "No signal at all, the router shows ERR-4G-503", "There's an outage"),
    started_at="2026-10-15T10:00:00",
)
BANKING = conversation(
    "bank", texts=("Hi", "I was charged twice on my card statement", "Refunded")
) | {"industry": "banking"}


async def test_vector_search_ranks_and_filters(db: async_sessionmaker[AsyncSession]) -> None:
    await ingest_payloads(db, [BILLING, NETWORK, BANKING])
    [query] = await FakeEmbeddingProvider().embed(["charged twice invoice"])

    async with db() as s:
        everything = await vector_search(s, query, k=5, filters=SearchFilters())
        telecom_sep = await vector_search(
            s,
            query,
            k=5,
            filters=SearchFilters(
                industry="telecom",
                start=datetime(2026, 9, 1, tzinfo=UTC),
                end=datetime(2026, 10, 1, tzinfo=UTC),
            ),
        )
    assert everything[0].external_id == "billing"
    assert [c.score for c in everything] == sorted((c.score for c in everything), reverse=True)
    assert {c.external_id for c in everything} == {"billing", "network", "bank"}
    assert [c.external_id for c in telecom_sep] == ["billing"]


async def test_selective_filter_still_returns_k(db: async_sessionmaker[AsyncSession]) -> None:
    lines = (DATASET / "conversations.jsonl").read_text().splitlines()[:150]
    await ingest_payloads(db, [json.loads(line) for line in lines])
    [query] = await FakeEmbeddingProvider().embed(["my bill went up"])
    june = SearchFilters(
        industry="telecom",
        start=datetime(2026, 6, 1, tzinfo=UTC),
        end=datetime(2026, 6, 8, tzinfo=UTC),
    )
    async with db() as s:
        results = await vector_search(s, query, k=10, filters=june)
    assert len(results) == 10
    assert all(r.started_at < datetime(2026, 6, 8, tzinfo=UTC) for r in results)


def _answer(text: str, *citations: tuple[int, str]) -> str:
    return json.dumps(
        {"answer": text, "citations": [{"source": s, "quote": q} for s, q in citations]}
    )


async def test_ask_returns_verified_citations(
    app: FastAPI, client: AsyncClient, db: async_sessionmaker[AsyncSession]
) -> None:
    await ingest_payloads(db, [BILLING, NETWORK])
    llm = FakeLLMProvider(
        [_answer("A customer was double charged.", (1, "charged twice on my invoice"))]
    )
    app.state.llm = llm

    response = await client.post("/v1/ask", json={"question": "Who was charged twice?", "k": 2})

    body = response.json()
    assert response.status_code == 200
    assert body["citations"] == [
        {"conversation_id": "billing", "excerpt": "charged twice on my invoice"}
    ]
    assert (body["sources_retrieved"], body["dropped_citations"]) == (2, 0)
    assert body["tools_used"] == ["search_conversations"]
    assert body["usage"]["input_tokens"] > 0
    prompt = llm.calls[0][1].content
    assert "[S1] conversation billing, 2026-09-03" in prompt
    assert prompt.endswith("Question: Who was charged twice?")


async def test_ask_drops_fabricated_citations_after_retry(
    app: FastAPI, client: AsyncClient, db: async_sessionmaker[AsyncSession]
) -> None:
    await ingest_payloads(db, [BILLING])
    fabricated = _answer("Customers hate the new prices.", (1, "the prices are outrageous"))
    fixed = _answer("One customer was double charged.", (1, "charged twice on my invoice"))
    llm = FakeLLMProvider([fabricated, fixed])
    app.state.llm = llm

    body = (await client.post("/v1/ask", json={"question": "What about prices?"})).json()

    assert body["answer"] == "One customer was double charged."
    assert body["dropped_citations"] == 0
    assert len(llm.calls) == 2
    assert "could not be verified" in llm.calls[1][-1].content

    llm.queue(fabricated, fabricated)
    body = (await client.post("/v1/ask", json={"question": "What about prices?"})).json()
    assert body["citations"] == []
    assert body["dropped_citations"] == 1


async def test_ask_without_matching_conversations_skips_the_llm(
    app: FastAPI, client: AsyncClient, db: async_sessionmaker[AsyncSession]
) -> None:
    await ingest_payloads(db, [BILLING])
    llm = FakeLLMProvider()
    app.state.llm = llm
    body = (
        await client.post("/v1/ask", json={"question": "Anything?", "industry": "ecommerce"})
    ).json()
    assert body["answer"] == NO_EVIDENCE
    assert body["sources_retrieved"] == 0
    assert llm.calls == []
