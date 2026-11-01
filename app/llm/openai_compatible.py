"""LLM and embedding providers for any OpenAI-compatible API.

One implementation covers three backends, which differ only in how the client
is built (see `app.llm.factory`):

* OpenAI         - `AsyncOpenAI`
* Azure OpenAI   - `AsyncAzureOpenAI`; `model` is the deployment name
* Ollama         - `AsyncOpenAI` pointed at Ollama's `/v1` endpoint

Transport retries (429, 5xx, timeouts) are handled by the SDK's `max_retries`
with exponential backoff; anything that still fails surfaces as an exception
and the ingestion job's retry policy takes over.
"""

from collections.abc import Sequence

from openai import AsyncOpenAI, pydantic_function_tool
from openai.types.chat import ChatCompletionMessageParam, ChatCompletionToolParam
from pydantic import BaseModel

from app.llm.base import (
    Completion,
    Message,
    StructuredCompletion,
    ToolCall,
    ToolChoice,
    ToolSpec,
    ToolTurn,
    Usage,
)


class LLMRefusalError(Exception):
    """The model declined to answer or returned no parseable output."""


def _message(m: Message) -> ChatCompletionMessageParam:
    if m.role == "tool":
        return {"role": "tool", "tool_call_id": m.tool_call_id or "", "content": m.content}
    if m.role == "assistant" and m.tool_calls:
        return {
            "role": "assistant",
            "content": m.content or None,
            "tool_calls": [
                {
                    "id": c.id,
                    "type": "function",
                    "function": {"name": c.name, "arguments": c.arguments},
                }
                for c in m.tool_calls
            ],
        }
    return {"role": m.role, "content": m.content}  # type: ignore[return-value,misc]


def _messages(messages: Sequence[Message]) -> list[ChatCompletionMessageParam]:
    return [_message(m) for m in messages]


def _tools(tools: Sequence[ToolSpec]) -> list[ChatCompletionToolParam]:
    # Strict schemas: the model's arguments always match the tool's Pydantic model.
    return [
        pydantic_function_tool(t.args_model, name=t.name, description=t.description) for t in tools
    ]


def _usage(usage: object | None) -> Usage:
    if usage is None:
        return Usage()
    return Usage(
        input_tokens=getattr(usage, "prompt_tokens", 0) or 0,
        output_tokens=getattr(usage, "completion_tokens", 0) or 0,
    )


class OpenAICompatibleLLM:
    def __init__(self, client: AsyncOpenAI, model: str) -> None:
        self._client = client
        self._model = model

    async def complete(
        self,
        messages: Sequence[Message],
        *,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> Completion:
        response = await self._client.chat.completions.create(
            model=self._model,
            messages=_messages(messages),
            temperature=temperature,
            max_tokens=max_tokens,
        )
        text = response.choices[0].message.content
        if text is None:
            raise LLMRefusalError("empty completion")
        return Completion(text=text, model=response.model, usage=_usage(response.usage))

    async def complete_structured[T: BaseModel](
        self,
        messages: Sequence[Message],
        schema: type[T],
        *,
        temperature: float = 0.0,
    ) -> StructuredCompletion[T]:
        # parse() sends a strict JSON schema derived from `schema` and validates the reply.
        response = await self._client.chat.completions.parse(
            model=self._model,
            messages=_messages(messages),
            response_format=schema,
            temperature=temperature,
        )
        message = response.choices[0].message
        if message.parsed is None:
            raise LLMRefusalError(message.refusal or "no structured output returned")
        return StructuredCompletion[T](
            value=message.parsed, model=response.model, usage=_usage(response.usage)
        )

    async def complete_with_tools(
        self,
        messages: Sequence[Message],
        tools: Sequence[ToolSpec],
        *,
        tool_choice: ToolChoice = "auto",
        temperature: float = 0.0,
    ) -> ToolTurn:
        response = await self._client.chat.completions.create(
            model=self._model,
            messages=_messages(messages),
            tools=_tools(tools),
            tool_choice=tool_choice,
            temperature=temperature,
        )
        message = response.choices[0].message
        calls = [
            ToolCall(id=c.id, name=c.function.name, arguments=c.function.arguments)
            for c in message.tool_calls or []
            if c.type == "function"
        ]
        return ToolTurn(
            text=message.content,
            tool_calls=calls,
            model=response.model,
            usage=_usage(response.usage),
        )


class OpenAICompatibleEmbeddings:
    def __init__(self, client: AsyncOpenAI, model: str, dim: int, *, send_dimensions: bool) -> None:
        self._client = client
        self._model = model
        self._dim = dim
        # text-embedding-3-* accept `dimensions`; most other models reject it.
        self._send_dimensions = send_dimensions

    @property
    def model(self) -> str:
        return self._model

    @property
    def dim(self) -> int:
        return self._dim

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        if self._send_dimensions:
            response = await self._client.embeddings.create(
                model=self._model, input=list(texts), dimensions=self._dim
            )
        else:
            response = await self._client.embeddings.create(model=self._model, input=list(texts))
        vectors = [item.embedding for item in sorted(response.data, key=lambda d: d.index)]
        if any(len(v) != self._dim for v in vectors):
            raise ValueError(
                f"embedding model {self._model!r} returned vectors of size "
                f"{len(vectors[0])}, expected {self._dim}"
            )
        return vectors
