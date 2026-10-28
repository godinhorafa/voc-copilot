"""Deterministic providers for tests and offline development.

`FakeLLMProvider` returns scripted responses and records every call so tests
can assert on prompts. `FakeEmbeddingProvider` hashes tokens into a fixed-size
vector: texts sharing words get similar vectors, which is enough to exercise
retrieval code without a network call.
"""

import hashlib
import math
import re
import types
import typing
from collections import deque
from collections.abc import Callable, Sequence
from typing import Any

from pydantic import BaseModel

from app.llm.base import Completion, Message, StructuredCompletion, Usage

FAKE_MODEL = "fake-llm"
FAKE_EMBEDDING_MODEL = "fake-embedding"
_TOKEN = re.compile(r"\w+", re.UNICODE)

StructuredFactory = Callable[[type[BaseModel], Sequence[Message]], BaseModel]


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
    ) -> None:
        self._responses: deque[str] = deque(responses)
        self._default = default
        self._structured = structured
        self.calls: list[list[Message]] = []

    @classmethod
    def permissive(cls) -> "FakeLLMProvider":
        """Answers any structured request with placeholder values (offline dev mode)."""
        return cls(structured=lambda schema, _: placeholder_instance(schema))

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
