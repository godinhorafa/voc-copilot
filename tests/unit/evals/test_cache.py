import json
import re
from pathlib import Path
from typing import Any

import httpx2
import openai
import pytest
from pydantic import BaseModel

from app.agent.tools import TOOLS
from app.llm.base import Message
from app.llm.fake import FakeEmbeddingProvider, FakeLLMProvider, tool_turn
from evals.cache import (
    CachedEmbeddings,
    CachedLLM,
    CacheMissError,
    ProviderUnavailableError,
    ResponseStore,
)

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


def _request() -> httpx2.Request:
    return httpx2.Request("POST", "https://api.openai.com/v1/chat/completions")


def _status(cls: type[openai.APIStatusError], code: int, message: str) -> openai.APIStatusError:
    return cls(message, response=httpx2.Response(code, request=_request()), body=None)


class Refusing(FakeLLMProvider):
    """A provider whose every call fails with `error`."""

    def __init__(self, error: Exception) -> None:
        super().__init__()
        self.error = error
        self.calls_made = 0

    async def complete_structured(self, *args: object, **kwargs: object) -> Any:
        self.calls_made += 1
        raise self.error


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (
            _status(openai.RateLimitError, 429, "Error code: 429 - You have no credits remaining."),
            "RateLimitError: Error code: 429 - You have no credits remaining.",
        ),
        (
            _status(openai.AuthenticationError, 401, "Incorrect API key provided: sk-pro***abcd"),
            "AuthenticationError: Incorrect API key provided",
        ),
        (_status(openai.NotFoundError, 404, "The model `gpt-9` does not exist"), "NotFoundError"),
        (openai.APIConnectionError(request=_request()), "APIConnectionError: Connection error."),
    ],
)
async def test_refusals_that_retrying_wont_fix_stop_the_recording(
    tmp_path: Path, error: Exception, reason: str
) -> None:
    path = tmp_path / "c.jsonl"
    recorder = CachedLLM(ResponseStore(path), model="m", mode="record", inner=Refusing(error))
    with pytest.raises(ProviderUnavailableError, match=re.escape(reason)):
        await recorder.complete_structured(MSGS, Answer)
    assert not path.exists()


async def test_bad_requests_concern_one_prompt_and_pass_through(tmp_path: Path) -> None:
    error = _status(openai.BadRequestError, 400, "context length exceeded")
    recorder = CachedLLM(
        ResponseStore(tmp_path / "c.jsonl"), model="m", mode="record", inner=Refusing(error)
    )
    with pytest.raises(openai.BadRequestError):
        await recorder.complete_structured(MSGS, Answer)


async def test_recording_resumes_without_calling_the_provider_again(tmp_path: Path) -> None:
    path = tmp_path / "c.jsonl"
    first = CachedLLM(
        ResponseStore(path),
        model="m",
        mode="record",
        inner=FakeLLMProvider(structured=lambda schema, messages: schema(label="x")),
    )
    await first.complete_structured(MSGS, Answer)

    offline = Refusing(openai.APIConnectionError(request=_request()))
    resumed = CachedLLM(ResponseStore(path), model="m", mode="record", inner=offline)
    assert (await resumed.complete_structured(MSGS, Answer)).value == Answer(label="x")
    assert offline.calls_made == 0
