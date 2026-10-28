from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class AskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=3, max_length=1000)
    industry: str | None = Field(default=None, max_length=32)
    start: datetime | None = Field(default=None, description="Conversations started at or after")
    end: datetime | None = Field(default=None, description="Conversations started before")
    k: int = Field(default=8, ge=1, le=30, description="Chunks to retrieve")

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


class Usage(BaseModel):
    input_tokens: int
    output_tokens: int
    latency_ms: int


class AskResponse(BaseModel):
    answer: str
    citations: list[Citation]
    tools_used: list[str]
    sources_retrieved: int
    dropped_citations: int = Field(description="Citations removed because they didn't verify")
    model: str | None
    usage: Usage
