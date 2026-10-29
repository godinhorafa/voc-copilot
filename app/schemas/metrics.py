from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class MetricsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=3, max_length=1000)
    industry: str | None = Field(default=None, max_length=32)
    start: datetime | None = None
    end: datetime | None = None

    @field_validator("start", "end")
    @classmethod
    def _utc(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class MetricsUsage(BaseModel):
    input_tokens: int
    output_tokens: int


class MetricsResponse(BaseModel):
    sql: str = Field(description="The query that ran, as normalized by the SQL guard")
    explanation: str
    columns: list[str]
    rows: list[list[Any]]
    truncated: bool
    attempts: int
    usage: MetricsUsage
