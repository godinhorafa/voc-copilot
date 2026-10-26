import math

import pytest
from pydantic import BaseModel

from app.llm.base import EmbeddingProvider, LLMProvider, Message
from app.llm.fake import FakeEmbeddingProvider, FakeLLMProvider


class Sentiment(BaseModel):
    label: str
    score: float


def test_fakes_satisfy_protocols() -> None:
    assert isinstance(FakeLLMProvider(), LLMProvider)
    assert isinstance(FakeEmbeddingProvider(), EmbeddingProvider)


async def test_returns_scripted_responses_in_order_then_default() -> None:
    llm = FakeLLMProvider(["first", "second"], default="fallback")
    msgs = [Message(role="user", content="hello there")]

    texts = [(await llm.complete(msgs)).text for _ in range(3)]

    assert texts == ["first", "second", "fallback"]
    assert len(llm.calls) == 3
    assert llm.calls[0][0].content == "hello there"


async def test_reports_token_usage() -> None:
    llm = FakeLLMProvider(["one two three"])
    result = await llm.complete([Message(role="user", content="a b")])
    assert (result.usage.input_tokens, result.usage.output_tokens) == (2, 3)


async def test_structured_parses_queued_json() -> None:
    llm = FakeLLMProvider(['{"label": "negative", "score": -0.8}'])
    result = await llm.complete_structured([Message(role="user", content="x")], Sentiment)
    assert result.value == Sentiment(label="negative", score=-0.8)


async def test_structured_uses_factory() -> None:
    llm = FakeLLMProvider(structured=lambda schema, _: Sentiment(label="neutral", score=0.0))
    result = await llm.complete_structured([Message(role="user", content="x")], Sentiment)
    assert result.value.label == "neutral"


async def test_structured_without_script_fails_loudly() -> None:
    with pytest.raises(LookupError, match="Sentiment"):
        await FakeLLMProvider().complete_structured([], Sentiment)


def _cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


async def test_embeddings_are_deterministic_and_normalized() -> None:
    emb = FakeEmbeddingProvider(dim=64)
    [a1], [a2] = await emb.embed(["billing issue"]), await emb.embed(["billing issue"])
    assert a1 == a2
    assert len(a1) == 64
    assert math.isclose(math.sqrt(sum(v * v for v in a1)), 1.0)


async def test_embeddings_reflect_word_overlap() -> None:
    emb = FakeEmbeddingProvider(dim=256)
    query, related, unrelated = await emb.embed(
        ["charged twice on my bill", "I was charged twice this month", "router keeps rebooting"]
    )
    assert _cosine(query, related) > _cosine(query, unrelated)
