from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.llm.fake import FakeEmbeddingProvider
from app.retrieval.keyword import keyword_search, or_tsquery
from app.retrieval.retriever import Retriever
from app.retrieval.types import SearchFilters
from tests.factories import conversation
from tests.helpers import ingest_payloads

pytestmark = pytest.mark.integration

DOCS = [
    conversation("invoice", texts=("Hi", "I was charged twice on my invoice", "Refunded")),
    conversation("router", texts=("Hi", "The router shows ERR-4G-503", "Rebooting it")),
    conversation(
        "bank", texts=("Hi", "Charged twice on my card", "Refunded"), started_at="2026-06-01T09:00"
    )
    | {"industry": "banking"},
]


async def test_or_query_drops_stopwords_and_stems(db: async_sessionmaker[AsyncSession]) -> None:
    async with db() as s:
        assert await or_tsquery(s, "Why was I charged on the invoices?") == "'charg' | 'invoic'"
        assert await or_tsquery(s, "what is the") is None


async def test_matches_any_term_and_ranks_more_matches_higher(
    db: async_sessionmaker[AsyncSession],
) -> None:
    await ingest_payloads(db, DOCS)
    async with db() as s:
        results = await keyword_search(
            s, "Who was charged twice on an invoice?", k=5, filters=SearchFilters()
        )
    assert [r.external_id for r in results] == ["invoice", "bank"]
    assert results[0].score > results[1].score


async def test_exact_codes_and_filters(db: async_sessionmaker[AsyncSession]) -> None:
    await ingest_payloads(db, DOCS)
    async with db() as s:
        code = await keyword_search(s, "ERR-4G-503", k=5, filters=SearchFilters())
        telecom = await keyword_search(
            s, "charged twice", k=5, filters=SearchFilters(industry="telecom")
        )
        june = await keyword_search(
            s,
            "charged twice",
            k=5,
            filters=SearchFilters(end=datetime(2026, 7, 1, tzinfo=UTC)),
        )
        nothing = await keyword_search(s, "the and of", k=5, filters=SearchFilters())
    assert [r.external_id for r in code] == ["router"]
    assert [r.external_id for r in telecom] == ["invoice"]
    assert [r.external_id for r in june] == ["bank"]
    assert nothing == []


async def test_hybrid_returns_union_of_both_retrievers(
    db: async_sessionmaker[AsyncSession],
) -> None:
    await ingest_payloads(db, DOCS)
    retriever = Retriever(FakeEmbeddingProvider(), strategy="hybrid")
    async with db() as s:
        results = await retriever.search(s, "charged twice invoice", k=3, filters=SearchFilters())
    assert results[0].external_id == "invoice"
    assert {r.external_id for r in results} == {"invoice", "bank", "router"}
    assert [r.score for r in results] == sorted((r.score for r in results), reverse=True)


async def test_tsquery_syntax_characters_are_inert(
    db: async_sessionmaker[AsyncSession],
) -> None:
    await ingest_payloads(db, DOCS)
    async with db() as s:
        results = await keyword_search(
            s, "router: !ERR & (4G) | 'x' <-> it's", k=5, filters=SearchFilters()
        )
    assert results[0].external_id == "router"


async def test_keyword_finds_nothing_without_shared_terms_but_hybrid_does(
    db: async_sessionmaker[AsyncSession],
) -> None:
    """Why hybrid is the default: paraphrased questions still get candidates."""
    await ingest_payloads(db, DOCS)
    question = "What about prices?"
    async with db() as s:
        keyword = await keyword_search(s, question, k=3, filters=SearchFilters())
        hybrid = await Retriever(FakeEmbeddingProvider(), strategy="hybrid").search(
            s, question, k=3, filters=SearchFilters()
        )
    assert keyword == []
    assert len(hybrid) == 3
