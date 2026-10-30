"""Providers against a mocked HTTP transport: request shape and response handling."""

import json
from collections.abc import Callable
from typing import Any

import httpx2 as httpx  # the transport the openai SDK uses
import pytest
from openai import AsyncAzureOpenAI, AsyncOpenAI, DefaultAsyncHttpx2Client
from pydantic import BaseModel

from app.llm.base import Message, ToolCall, ToolSpec
from app.llm.openai_compatible import (
    LLMRefusalError,
    OpenAICompatibleEmbeddings,
    OpenAICompatibleLLM,
)

Handler = Callable[[httpx.Request], httpx.Response]


class Verdict(BaseModel):
    label: str
    score: float


def _client(handler: Handler, **kwargs: Any) -> AsyncOpenAI:
    return AsyncOpenAI(
        api_key="sk-test",
        http_client=DefaultAsyncHttpx2Client(transport=httpx.MockTransport(handler)),
        **kwargs,
    )


def _chat(content: str | None, *, refusal: str | None = None) -> dict[str, Any]:
    return {
        "id": "chatcmpl-1",
        "object": "chat.completion",
        "created": 0,
        "model": "gpt-test-2026",
        "choices": [
            {
                "index": 0,
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": content, "refusal": refusal},
            }
        ],
        "usage": {"prompt_tokens": 12, "completion_tokens": 5, "total_tokens": 17},
    }


class Recorder:
    def __init__(self, *responses: httpx.Response) -> None:
        self.responses = list(responses)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.responses.pop(0)

    def body(self, i: int = -1) -> dict[str, Any]:
        result: dict[str, Any] = json.loads(self.requests[i].content)
        return result


MSGS = [Message(role="system", content="be brief"), Message(role="user", content="hi")]


async def test_complete_returns_text_model_and_usage() -> None:
    rec = Recorder(httpx.Response(200, json=_chat("hello")))
    llm = OpenAICompatibleLLM(_client(rec), "gpt-test")

    result = await llm.complete(MSGS, temperature=0.2)

    assert (result.text, result.model) == ("hello", "gpt-test-2026")
    assert (result.usage.input_tokens, result.usage.output_tokens) == (12, 5)
    body = rec.body()
    assert body["model"] == "gpt-test"
    assert body["messages"] == [
        {"role": "system", "content": "be brief"},
        {"role": "user", "content": "hi"},
    ]
    assert rec.requests[0].url.path == "/v1/chat/completions"


async def test_structured_sends_strict_schema_and_parses() -> None:
    rec = Recorder(httpx.Response(200, json=_chat('{"label": "neg", "score": -0.5}')))
    llm = OpenAICompatibleLLM(_client(rec), "gpt-test")

    result = await llm.complete_structured(MSGS, Verdict)

    assert result.value == Verdict(label="neg", score=-0.5)
    fmt = rec.body()["response_format"]
    assert fmt["type"] == "json_schema"
    assert fmt["json_schema"]["strict"] is True
    assert set(fmt["json_schema"]["schema"]["required"]) == {"label", "score"}


async def test_structured_refusal_raises() -> None:
    rec = Recorder(httpx.Response(200, json=_chat(None, refusal="I can't help with that")))
    llm = OpenAICompatibleLLM(_client(rec), "gpt-test")
    with pytest.raises(LLMRefusalError, match="can't help"):
        await llm.complete_structured(MSGS, Verdict)


async def test_rate_limit_is_retried_by_the_sdk() -> None:
    rec = Recorder(
        httpx.Response(429, json={"error": {"message": "slow down"}}, headers={"retry-after": "0"}),
        httpx.Response(200, json=_chat("ok")),
    )
    llm = OpenAICompatibleLLM(_client(rec, max_retries=1), "gpt-test")
    assert (await llm.complete(MSGS)).text == "ok"
    assert len(rec.requests) == 2


def _embeddings(vectors: list[list[float]]) -> dict[str, Any]:
    # Returned out of order on purpose: the provider must sort by index.
    data = [{"object": "embedding", "index": i, "embedding": v} for i, v in enumerate(vectors)]
    return {
        "object": "list",
        "data": list(reversed(data)),
        "model": "text-embedding-3-small",
        "usage": {"prompt_tokens": 4, "total_tokens": 4},
    }


async def test_embeddings_sorted_by_index_and_send_dimensions() -> None:
    rec = Recorder(httpx.Response(200, json=_embeddings([[1.0, 0.0], [0.0, 1.0]])))
    emb = OpenAICompatibleEmbeddings(
        _client(rec), "text-embedding-3-small", 2, send_dimensions=True
    )

    vectors = await emb.embed(["a", "b"])

    assert vectors == [[1.0, 0.0], [0.0, 1.0]]
    body = rec.body()
    assert (body["model"], body["input"], body["dimensions"]) == (
        "text-embedding-3-small",
        ["a", "b"],
        2,
    )


async def test_embeddings_reject_wrong_dimension() -> None:
    rec = Recorder(httpx.Response(200, json=_embeddings([[1.0, 0.0, 0.0]])))
    emb = OpenAICompatibleEmbeddings(_client(rec), "nomic-embed-text", 2, send_dimensions=False)
    with pytest.raises(ValueError, match="size 3, expected 2"):
        await emb.embed(["a"])
    assert "dimensions" not in rec.body()


async def test_embeddings_skip_empty_input() -> None:
    rec = Recorder()
    emb = OpenAICompatibleEmbeddings(_client(rec), "m", 2, send_dimensions=False)
    assert await emb.embed([]) == []
    assert rec.requests == []


async def test_azure_uses_deployment_route_and_api_version() -> None:
    rec = Recorder(httpx.Response(200, json=_chat("hi")))
    client = AsyncAzureOpenAI(
        azure_endpoint="https://example.openai.azure.com",
        api_key="az-key",
        api_version="2024-10-21",
        http_client=DefaultAsyncHttpx2Client(transport=httpx.MockTransport(rec)),
    )
    await OpenAICompatibleLLM(client, "my-deployment").complete(MSGS)

    url = rec.requests[0].url
    assert url.path == "/openai/deployments/my-deployment/chat/completions"
    assert url.params["api-version"] == "2024-10-21"
    assert rec.requests[0].headers["api-key"] == "az-key"


class SearchArgs(BaseModel):
    query: str
    industry: str | None


def _tool_chat(calls: list[dict[str, Any]]) -> dict[str, Any]:
    body = _chat(None)
    body["choices"][0]["message"]["tool_calls"] = calls
    body["choices"][0]["finish_reason"] = "tool_calls"
    return body


async def test_tool_calls_round_trip() -> None:
    call = {
        "id": "call_1",
        "type": "function",
        "function": {"name": "search", "arguments": '{"query": "price", "industry": null}'},
    }
    rec = Recorder(httpx.Response(200, json=_tool_chat([call])))
    llm = OpenAICompatibleLLM(_client(rec), "gpt-test")
    history = [
        *MSGS,
        Message(
            role="assistant", tool_calls=[ToolCall(id="call_0", name="search", arguments="{}")]
        ),
        Message(role="tool", tool_call_id="call_0", content="[S1] ..."),
    ]

    turn = await llm.complete_with_tools(
        history,
        [ToolSpec(name="search", description="Find excerpts", args_model=SearchArgs)],
        tool_choice="required",
    )

    assert turn.text is None
    assert turn.tool_calls == [
        ToolCall(id="call_1", name="search", arguments='{"query": "price", "industry": null}')
    ]
    assert turn.usage.input_tokens == 12
    body = rec.body()
    assert body["tool_choice"] == "required"
    tool = body["tools"][0]["function"]
    assert (tool["name"], tool["strict"]) == ("search", True)
    assert tool["parameters"]["additionalProperties"] is False
    assert body["messages"][2] == {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {"id": "call_0", "type": "function", "function": {"name": "search", "arguments": "{}"}}
        ],
    }
    assert body["messages"][3] == {"role": "tool", "tool_call_id": "call_0", "content": "[S1] ..."}
