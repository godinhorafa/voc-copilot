import hashlib
import json
import uuid
from collections import Counter
from datetime import UTC, datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MAX_CONVERSATIONS_PER_REQUEST = 500

Speaker = Literal["agent", "customer"]
Channel = Literal["chat", "voice"]
ProcessingStatus = Literal["pending", "processed", "failed"]

_ID_PATTERN = r"^[A-Za-z0-9_.:\-]+$"


class TurnIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    speaker: Speaker
    text: str = Field(min_length=1, max_length=10_000)
    offset_s: int | None = Field(default=None, ge=0)


class ConversationIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    external_id: str = Field(min_length=1, max_length=128, pattern=_ID_PATTERN)
    customer_id: str | None = Field(default=None, max_length=64)
    industry: str = Field(min_length=1, max_length=32)
    channel: Channel
    started_at: datetime
    turns: list[TurnIn] = Field(min_length=1, max_length=500)

    @field_validator("started_at")
    @classmethod
    def _assume_utc(cls, value: datetime) -> datetime:
        # Naive timestamps are interpreted as UTC rather than rejected.
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)

    def content_hash(self) -> str:
        """Hash of everything that affects processing; equal hash means nothing to redo."""
        payload = self.model_dump(mode="json", exclude={"external_id"})
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(canonical.encode()).hexdigest()


class IngestRequest(BaseModel):
    conversations: list[ConversationIn] = Field(
        min_length=1, max_length=MAX_CONVERSATIONS_PER_REQUEST
    )

    @model_validator(mode="after")
    def _unique_external_ids(self) -> Self:
        counts = Counter(c.external_id for c in self.conversations)
        dupes = [external_id for external_id, n in counts.items() if n > 1]
        if dupes:
            raise ValueError(f"duplicate external_id in request: {sorted(dupes)}")
        return self


class IngestResponse(BaseModel):
    job_id: uuid.UUID | None = Field(description="Null when nothing needed processing")
    created: int
    updated: int
    unchanged: int
    queued: int = Field(description="Conversations queued for processing")


class TurnOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    speaker: Speaker
    text: str
    offset_s: int | None


class ConversationOut(BaseModel):
    id: uuid.UUID
    external_id: str
    customer_id: str | None
    industry: str
    channel: Channel
    started_at: datetime
    status: ProcessingStatus
    processing_error: str | None
    chunk_count: int
    turns: list[TurnOut]
