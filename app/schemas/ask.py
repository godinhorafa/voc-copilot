from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.usage import UsageOut


class AskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=3, max_length=1000)
    industry: str | None = Field(default=None, max_length=32)
    start: datetime | None = Field(default=None, description="Conversations started at or after")
    end: datetime | None = Field(default=None, description="Conversations started before")
    k: int = Field(default=8, ge=1, le=30, description="Chunks to retrieve per search")
    mode: Literal["agent", "rag"] = Field(
        default="agent",
        description="agent: tools (search + metrics) chosen by the model; rag: one search",
    )

    @field_validator("start", "end")
    @classmethod
    def _utc(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)

    @model_validator(mode="after")
    def _window(self) -> "AskRequest":
        if self.start and self.end and self.end <= self.start:
            raise ValueError("end must be after start")
        return self


class Citation(BaseModel):
    conversation_id: str = Field(description="external_id of the cited conversation")
    excerpt: str


class MetricOut(BaseModel):
    id: str = Field(description="Reference used in the answer, e.g. M1")
    question: str
    sql: str
    columns: list[str]
    rows: list[list[Any]]
    truncated: bool


class TraceStepOut(BaseModel):
    step: int
    tool: str
    arguments: dict[str, Any]
    summary: str
    duration_ms: int
    error: bool


class AskResponse(BaseModel):
    answer: str
    citations: list[Citation]
    metrics: list[MetricOut] = Field(default_factory=list)
    tools_used: list[str]
    mode: Literal["agent", "rag"]
    steps: int = Field(description="Model turns used")
    trace: list[TraceStepOut] = Field(default_factory=list)
    retrieval: str = Field(description="Retrieval strategy used, e.g. keyword or hybrid+rerank")
    sources_retrieved: int = Field(description="Distinct excerpts retrieved across searches")
    dropped_citations: int = Field(description="Citations removed because they didn't verify")
    model: str | None
    usage: UsageOut
    trace_id: str | None = Field(
        description="OpenTelemetry trace id, to find this request in traces"
    )
