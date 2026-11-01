"""Provider-agnostic LLM interfaces.

Business logic depends only on these protocols; concrete providers live in
sibling modules and are selected by `app.llm.factory`.
"""

from collections.abc import Sequence
from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel, Field

Role = Literal["system", "user", "assistant", "tool"]


class ToolCall(BaseModel):
    id: str
    name: str
    arguments: str  # raw JSON, validated by the caller against the tool's model


class Message(BaseModel):
    role: Role
    content: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)  # assistant turns
    tool_call_id: str | None = None  # tool results


class ToolSpec(BaseModel):
    """A tool the model may call; arguments are validated against `args_model`."""

    name: str
    description: str
    args_model: type[BaseModel]


class Usage(BaseModel):
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
        )


class Completion(BaseModel):
    text: str
    model: str
    usage: Usage = Usage()


class ToolTurn(BaseModel):
    """One model turn: tool calls to run, and/or text."""

    text: str | None
    tool_calls: list[ToolCall]
    model: str
    usage: Usage = Usage()


ToolChoice = Literal["auto", "required"]


class StructuredCompletion[T: BaseModel](BaseModel):
    value: T
    model: str
    usage: Usage = Usage()


@runtime_checkable
class LLMProvider(Protocol):
    async def complete(
        self,
        messages: Sequence[Message],
        *,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> Completion: ...

    async def complete_structured[T: BaseModel](
        self,
        messages: Sequence[Message],
        schema: type[T],
        *,
        temperature: float = 0.0,
    ) -> StructuredCompletion[T]: ...

    async def complete_with_tools(
        self,
        messages: Sequence[Message],
        tools: Sequence[ToolSpec],
        *,
        tool_choice: ToolChoice = "auto",
        temperature: float = 0.0,
    ) -> ToolTurn: ...


@runtime_checkable
class EmbeddingProvider(Protocol):
    @property
    def model(self) -> str: ...

    @property
    def dim(self) -> int: ...

    async def embed(self, texts: Sequence[str]) -> list[list[float]]: ...
