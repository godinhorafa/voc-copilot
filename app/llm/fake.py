"""Deterministic providers for tests and offline development.

`FakeLLMProvider` returns scripted responses and records every call so tests
can assert on prompts. `FakeEmbeddingProvider` hashes tokens into a fixed-size
vector: texts sharing words get similar vectors, which is enough to exercise
retrieval code without a network call.
"""

import hashlib
import json
import math
import re
import types
import typing
from collections import deque
from collections.abc import Callable, Sequence
from typing import Any

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

FAKE_MODEL = "fake-llm"
FAKE_EMBEDDING_MODEL = "fake-embedding"
_TOKEN = re.compile(r"\w+", re.UNICODE)

StructuredFactory = Callable[[type[BaseModel], Sequence[Message]], BaseModel]
ToolScript = Callable[[Sequence[Message], Sequence[ToolSpec]], ToolTurn]
FINAL_TOOL = "final_answer"


def tool_turn(*calls: tuple[str, dict[str, Any]], text: str | None = None) -> ToolTurn:
    """Build a scripted turn: `tool_turn(("search_conversations", {"query": "..."}))`."""
    return ToolTurn(
        text=text,
        tool_calls=[
            ToolCall(id=f"call_{i}", name=name, arguments=json.dumps(args))
            for i, (name, args) in enumerate(calls)
        ],
        model=FAKE_MODEL,
    )


def _count_tokens(text: str) -> int:
    return len(_TOKEN.findall(text))


def _placeholder(annotation: Any) -> Any:
    origin = typing.get_origin(annotation)
    if origin is typing.Literal:
        return typing.get_args(annotation)[0]
    if origin in (list, set, tuple):
        return []
    if origin in (typing.Union, types.UnionType):
        return None
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return placeholder_instance(annotation)
    return {bool: False, int: 0, float: 0.0, str: "fake"}.get(annotation)


def placeholder_instance(schema: type[BaseModel]) -> BaseModel:
    """A schema-valid instance with the simplest value for every field."""
    return schema.model_validate(
        {name: _placeholder(field.annotation) for name, field in schema.model_fields.items()}
    )


class FakeLLMProvider:
    def __init__(
        self,
        responses: Sequence[str] = (),
        *,
        default: str = "fake response",
        structured: StructuredFactory | None = None,
        tool_turns: Sequence[ToolTurn] = (),
        tool_script: ToolScript | None = None,
    ) -> None:
        self._responses: deque[str] = deque(responses)
        self._default = default
        self._structured = structured
        self._tool_turns: deque[ToolTurn] = deque(tool_turns)
        self._tool_script = tool_script
        self.calls: list[list[Message]] = []
        self.tool_choices: list[ToolChoice] = []
        self.tools_offered: list[list[str]] = []

    @classmethod
    def permissive(cls) -> "FakeLLMProvider":
        """Offline dev mode: placeholder structured values; with tools, finish at once
        through `final_answer` when it is offered."""
        return cls(structured=lambda schema, _: placeholder_instance(schema), tool_script=_finish)

    def queue(self, *responses: str) -> None:
        self._responses.extend(responses)

    def _usage(self, messages: Sequence[Message], output: str) -> Usage:
        return Usage(
            input_tokens=sum(_count_tokens(m.content) for m in messages),
            output_tokens=_count_tokens(output),
        )

    async def complete(
        self,
        messages: Sequence[Message],
        *,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> Completion:
        self.calls.append(list(messages))
        text = self._responses.popleft() if self._responses else self._default
        return Completion(text=text, model=FAKE_MODEL, usage=self._usage(messages, text))

    async def complete_structured[T: BaseModel](
        self,
        messages: Sequence[Message],
        schema: type[T],
        *,
        temperature: float = 0.0,
    ) -> StructuredCompletion[T]:
        self.calls.append(list(messages))
        if self._responses:
            raw = self._responses.popleft()
            value = schema.model_validate_json(raw)
        elif self._structured is not None:
            value = schema.model_validate(self._structured(schema, messages))
        else:
            raise LookupError(
                f"FakeLLMProvider has no scripted response for {schema.__name__}; "
                "queue a JSON response or pass `structured=`"
            )
        return StructuredCompletion[T](
            value=value,
            model=FAKE_MODEL,
            usage=self._usage(messages, value.model_dump_json()),
        )

    async def complete_with_tools(
        self,
        messages: Sequence[Message],
        tools: Sequence[ToolSpec],
        *,
        tool_choice: ToolChoice = "auto",
        temperature: float = 0.0,
    ) -> ToolTurn:
        self.calls.append(list(messages))
        self.tool_choices.append(tool_choice)
        self.tools_offered.append([t.name for t in tools])
        if self._tool_turns:
            turn = self._tool_turns.popleft()
        elif self._tool_script is not None:
            turn = self._tool_script(messages, tools)
        else:
            raise LookupError("FakeLLMProvider has no scripted tool turn; queue one")
        output = turn.text or "".join(c.arguments for c in turn.tool_calls)
        return turn.model_copy(update={"usage": self._usage(messages, output)})


def _finish(messages: Sequence[Message], tools: Sequence[ToolSpec]) -> ToolTurn:
    final = next((t for t in tools if t.name == FINAL_TOOL), None)
    if final is None:
        return ToolTurn(text="fake response", tool_calls=[], model=FAKE_MODEL)
    args = placeholder_instance(final.args_model).model_dump_json()
    return ToolTurn(
        text=None,
        tool_calls=[ToolCall(id="call_final", name=FINAL_TOOL, arguments=args)],
        model=FAKE_MODEL,
    )


class FakeEmbeddingProvider:
    def __init__(self, dim: int = 1536) -> None:
        self._dim = dim

    @property
    def model(self) -> str:
        return FAKE_EMBEDDING_MODEL

    @property
    def dim(self) -> int:
        return self._dim

    def _embed_one(self, text: str) -> list[float]:
        vector = [0.0] * self._dim
        for token in _TOKEN.findall(text.lower()):
            digest = hashlib.blake2b(token.encode(), digest_size=8).digest()
            bucket = int.from_bytes(digest[:4], "big") % self._dim
            sign = 1.0 if digest[4] & 1 else -1.0
            vector[bucket] += sign
        norm = math.sqrt(sum(v * v for v in vector))
        return [v / norm for v in vector] if norm else vector

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._embed_one(t) for t in texts]
