import json
from pathlib import Path

import pytest
from pydantic import BaseModel

from app.agent.tools import TOOLS
from app.llm.base import Message
from app.llm.fake import FakeEmbeddingProvider, FakeLLMProvider, tool_turn
from evals.cache import CachedEmbeddings, CachedLLM, CacheMissError, ResponseStore

MSGS = [Message(role="user", content="hello")]


class Answer(BaseModel):
    label: str


async def test_record_then_replay_all_methods(tmp_path: Path) -> None:
    path = tmp_path / "cache.jsonl"
    inner = FakeLLMProvider(
        ["plain text", '{"label": "x"}'],
        tool_turns=[tool_turn(("search_conversations", {"query": "q"}))],
    )
    recorder = CachedLLM(ResponseStore(path), model="m", mode="record", inner=inner)
    text = await recorder.complete(MSGS)
    value = await recorder.complete_structured(MSGS, Answer)
    turn = await recorder.complete_with_tools(MSGS, TOOLS, tool_choice="required")

    replay = CachedLLM(ResponseStore(path), model="m", mode="replay")
    assert (await replay.complete(MSGS)).text == text.text == "plain text"
    assert (
        (await replay.complete_structured(MSGS, Answer)).value == value.value == Answer(label="x")
    )
    replayed = await replay.complete_with_tools(MSGS, TOOLS, tool_choice="required")
    assert replayed.tool_calls == turn.tool_calls
    assert len(path.read_text().splitlines()) == 3


async def test_any_change_in_the_request_is_a_miss(tmp_path: Path) -> None:
    path = tmp_path / "cache.jsonl"
    recorder = CachedLLM(
        ResponseStore(path), model="m", mode="record", inner=FakeLLMProvider(["a"])
    )
    await recorder.complete(MSGS)

    store = ResponseStore(path)
    for model, messages, temperature in [
        ("other-model", MSGS, 0.0),
        ("m", [Message(role="user", content="hello!")], 0.0),
        ("m", MSGS, 0.7),
    ]:
        with pytest.raises(CacheMissError):
            await CachedLLM(store, model=model, mode="replay").complete(
                messages, temperature=temperature
            )
    assert (store.hits, store.misses) == (0, 3)


async def test_tool_schema_changes_invalidate_tool_turns(tmp_path: Path) -> None:
    path = tmp_path / "cache.jsonl"
    inner = FakeLLMProvider(tool_turns=[tool_turn(text="hi")])
    await CachedLLM(ResponseStore(path), model="m", mode="record", inner=inner).complete_with_tools(
        MSGS, TOOLS
    )
    with pytest.raises(CacheMissError):
        await CachedLLM(ResponseStore(path), model="m", mode="replay").complete_with_tools(
            MSGS, TOOLS[:2]
        )


async def test_embeddings_cache(tmp_path: Path) -> None:
    path = tmp_path / "emb.jsonl"
    rec = CachedEmbeddings(
        ResponseStore(path), model="e", dim=8, mode="record", inner=FakeEmbeddingProvider(dim=8)
    )
    vectors = await rec.embed(["a b", "c"])
    replay = CachedEmbeddings(ResponseStore(path), model="e", dim=8, mode="replay")
    assert await replay.embed(["a b", "c"]) == vectors
    with pytest.raises(CacheMissError):
        await replay.embed(["c"])


def test_record_requires_a_real_provider(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="record mode"):
        CachedLLM(ResponseStore(tmp_path / "x.jsonl"), model="m", mode="record")


def test_entries_are_written_once(tmp_path: Path) -> None:
    store = ResponseStore(tmp_path / "s.jsonl")
    store.put("k", "complete", {"text": 1})
    store.put("k", "complete", {"text": 2})
    lines = (tmp_path / "s.jsonl").read_text().splitlines()
    assert [json.loads(line)["response"] for line in lines] == [{"text": 1}]
