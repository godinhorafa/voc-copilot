import uuid
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class SearchFilters:
    industry: str | None = None
    start: datetime | None = None  # inclusive, on conversation start time
    end: datetime | None = None  # exclusive


@dataclass(frozen=True)
class RetrievedChunk:
    chunk_id: uuid.UUID
    conversation_id: uuid.UUID
    external_id: str
    started_at: datetime
    text: str
    score: float  # higher is more similar


def conversation_ranking(chunks: list[RetrievedChunk]) -> list[str]:
    """Conversations in order of their best chunk, each listed once."""
    seen: dict[str, None] = {}
    for chunk in chunks:
        seen.setdefault(chunk.external_id, None)
    return list(seen)
