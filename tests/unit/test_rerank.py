import json
import uuid
from datetime import UTC, datetime

from app.llm.fake import FakeLLMProvider
from app.retrieval.rerank import LLMReranker
from app.retrieval.types import RetrievedChunk


def _chunks(*texts: str) -> list[RetrievedChunk]:
    return [
        RetrievedChunk(
            chunk_id=uuid.uuid4(),
            conversation_id=uuid.uuid4(),
            external_id=f"c{i}",
            started_at=datetime(2026, 9, 1, tzinfo=UTC),
            text=t,
            score=0.0,
        )
        for i, t in enumerate(texts, start=1)
    ]


def _grades(*pairs: tuple[int, int]) -> str:
    return json.dumps({"grades": [{"id": i, "relevance": r} for i, r in pairs]})


async def test_reorders_by_grade_keeping_retrieval_order_on_ties() -> None:
    candidates = _chunks("greeting", "billing complaint", "router", "price increase")
    llm = FakeLLMProvider([_grades((1, 0), (2, 3), (3, 1), (4, 3))])
    reranker = LLMReranker(llm)

    ranked = await reranker.rerank("Why are bills higher?", candidates, k=3)

    assert [c.external_id for c in ranked] == ["c2", "c4", "c3"]
    assert reranker.usage.input_tokens > 0
    prompt = llm.calls[0][1].content
    assert prompt.startswith("Question: Why are bills higher?")
    assert "[4]\nprice increase" in prompt


async def test_missing_and_invalid_ids_count_as_irrelevant() -> None:
    candidates = _chunks("a", "b", "c")
    llm = FakeLLMProvider([_grades((3, 2), (9, 3))])
    ranked = await LLMReranker(llm).rerank("q", candidates, k=3)
    assert [c.external_id for c in ranked] == ["c3", "c1", "c2"]


async def test_no_candidates_skips_the_llm() -> None:
    llm = FakeLLMProvider()
    assert await LLMReranker(llm).rerank("q", [], k=5) == []
    assert llm.calls == []
