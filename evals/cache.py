"""Record and replay LLM and embedding calls, so LLM-based evals can run in CI
without an API key, at no cost, and deterministically.

    record:  call the real provider, store each response under a request key
    replay:  answer from the store only; a request that isn't stored raises
             CacheMissError (the eval reports it instead of calling a model)

The key is a hash of everything that determines the response: the method, the
model, the messages (with tool calls), the response schema or tool schemas,
and the temperature. Change a prompt and its key changes, so stale answers are
never replayed; re-record with a real provider after prompt changes.

Cache files are JSONL, one call per line, small enough to commit.
"""

import hashlib
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel

from app.llm.base import (
    Completion,
    EmbeddingProvider,
    LLMProvider,
    Message,
    StructuredCompletion,
    ToolChoice,
    ToolSpec,
    ToolTurn,
)

Mode = Literal["record", "replay"]


class CacheMissError(LookupError):
    pass


def request_key(method: str, model: str, payload: dict[str, Any]) -> str:
    canonical = json.dumps({"method": method, "model": model, **payload}, sort_keys=True)
    return hashlib.sha256(canonical.encode()).hexdigest()


def _messages(messages: Sequence[Message]) -> list[dict[str, Any]]:
    return [m.model_dump(mode="json") for m in messages]


class ResponseStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._entries: dict[str, Any] = {}
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    entry = json.loads(line)
                    self._entries[entry["key"]] = entry["response"]
        self.hits = 0
        self.misses = 0

    def __len__(self) -> int:
        return len(self._entries)

    def get(self, key: str) -> Any:
        if key in self._entries:
            self.hits += 1
            return self._entries[key]
        self.misses += 1
        raise CacheMissError(f"no cached response for request {key[:12]} in {self.path.name}")

    def put(self, key: str, method: str, response: Any) -> None:
        if key in self._entries:
            return
        self._entries[key] = response
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"key": key, "method": method, "response": response}) + "\n")


class CachedLLM:
    """LLMProvider that records or replays another provider's responses."""

    def __init__(
        self, store: ResponseStore, *, model: str, mode: Mode, inner: LLMProvider | None = None
    ) -> None:
        if mode == "record" and inner is None:
            raise ValueError("record mode needs a real provider")
        self._store = store
        self._model = model
        self._mode = mode
        self._inner = inner

    async def _cached(self, method: str, payload: dict[str, Any], call: Any) -> Any:
        key = request_key(method, self._model, payload)
        if self._mode == "replay":
            return self._store.get(key)
        response = await call()
        self._store.put(key, method, response)
        return response

    async def complete(
        self,
        messages: Sequence[Message],
        *,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> Completion:
        async def call() -> Any:
            assert self._inner is not None
            result = await self._inner.complete(
                messages, temperature=temperature, max_tokens=max_tokens
            )
            return result.model_dump(mode="json")

        payload = {"messages": _messages(messages), "temperature": temperature}
        return Completion.model_validate(await self._cached("complete", payload, call))

    async def complete_structured[T: BaseModel](
        self,
        messages: Sequence[Message],
        schema: type[T],
        *,
        temperature: float = 0.0,
    ) -> StructuredCompletion[T]:
        async def call() -> Any:
            assert self._inner is not None
            result = await self._inner.complete_structured(
                messages, schema, temperature=temperature
            )
            return {
                "value": result.value.model_dump(mode="json"),
                "model": result.model,
                "usage": result.usage.model_dump(),
            }

        payload = {
            "messages": _messages(messages),
            "schema": schema.model_json_schema(),
            "temperature": temperature,
        }
        raw = await self._cached("complete_structured", payload, call)
        return StructuredCompletion[T](
            value=schema.model_validate(raw["value"]), model=raw["model"], usage=raw["usage"]
        )

    async def complete_with_tools(
        self,
        messages: Sequence[Message],
        tools: Sequence[ToolSpec],
        *,
        tool_choice: ToolChoice = "auto",
        temperature: float = 0.0,
    ) -> ToolTurn:
        async def call() -> Any:
            assert self._inner is not None
            result = await self._inner.complete_with_tools(
                messages, tools, tool_choice=tool_choice, temperature=temperature
            )
            return result.model_dump(mode="json")

        payload = {
            "messages": _messages(messages),
            "tools": [
                {
                    "name": t.name,
                    "description": t.description,
                    "schema": t.args_model.model_json_schema(),
                }
                for t in tools
            ],
            "tool_choice": tool_choice,
            "temperature": temperature,
        }
        return ToolTurn.model_validate(await self._cached("complete_with_tools", payload, call))


class CachedEmbeddings:
    """EmbeddingProvider that records or replays another provider's vectors."""

    def __init__(
        self,
        store: ResponseStore,
        *,
        model: str,
        dim: int,
        mode: Mode,
        inner: EmbeddingProvider | None = None,
    ) -> None:
        if mode == "record" and inner is None:
            raise ValueError("record mode needs a real provider")
        self._store = store
        self._model = model
        self._dim = dim
        self._mode = mode
        self._inner = inner

    @property
    def model(self) -> str:
        return self._model

    @property
    def dim(self) -> int:
        return self._dim

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        key = request_key("embed", self._model, {"texts": list(texts), "dim": self._dim})
        if self._mode == "replay":
            vectors: list[list[float]] = self._store.get(key)
            return vectors
        assert self._inner is not None
        vectors = await self._inner.embed(texts)
        self._store.put(key, "embed", vectors)
        return vectors
