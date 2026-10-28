"""Read side of ingestion: turn stored conversations into searchable chunks.

`process_conversation` is idempotent. It skips conversations whose current
content was already processed and replaces any previous chunks, so a job can be
retried from the start and will resume where it stopped.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

import structlog
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from app.db.models import Chunk, Conversation, Job, JobStatus
from app.ingestion.chunking import chunk_turns
from app.llm.base import EmbeddingProvider

logger = structlog.get_logger(__name__)


class Outcome(StrEnum):
    PROCESSED = "processed"
    SKIPPED = "skipped"


@dataclass(frozen=True)
class PipelineConfig:
    chunk_max_words: int = 160
    chunk_overlap_turns: int = 1
    embedding_batch_size: int = 64


async def _embed(
    embeddings: EmbeddingProvider, texts: list[str], batch_size: int
) -> list[list[float]]:
    vectors: list[list[float]] = []
    for i in range(0, len(texts), batch_size):
        vectors.extend(await embeddings.embed(texts[i : i + batch_size]))
    if len(vectors) != len(texts):
        raise RuntimeError(f"embedding provider returned {len(vectors)} vectors for {len(texts)}")
    return vectors


async def process_conversation(
    sessionmaker: async_sessionmaker[AsyncSession],
    embeddings: EmbeddingProvider,
    conversation_id: uuid.UUID,
    config: PipelineConfig,
) -> Outcome:
    async with sessionmaker() as session:
        conv = await session.get(
            Conversation, conversation_id, options=[selectinload(Conversation.turns)]
        )
        if conv is None or conv.is_processed:
            return Outcome.SKIPPED
        # Remember which content we processed; a concurrent re-upload changes the
        # hash and leaves this conversation unprocessed for the next job.
        content_hash = conv.content_hash

        specs = chunk_turns(
            conv.turns,
            max_words=config.chunk_max_words,
            overlap_turns=config.chunk_overlap_turns,
        )
        vectors = await _embed(embeddings, [s.text for s in specs], config.embedding_batch_size)

        await session.execute(delete(Chunk).where(Chunk.conversation_id == conv.id))
        session.add_all(
            Chunk(
                conversation_id=conv.id,
                turn_start=spec.turn_start,
                turn_end=spec.turn_end,
                text=spec.text,
                embedding=vector,
                embedding_model=embeddings.model,
            )
            for spec, vector in zip(specs, vectors, strict=True)
        )
        conv.processed_hash = content_hash
        conv.processing_error = None
        await session.commit()
        return Outcome.PROCESSED


async def mark_failed(
    sessionmaker: async_sessionmaker[AsyncSession], conversation_id: uuid.UUID, error: str
) -> None:
    async with sessionmaker() as session:
        conv = await session.get(Conversation, conversation_id)
        if conv is not None:
            conv.processing_error = error[:2000]
            await session.commit()


async def start_job(session: AsyncSession, job_id: uuid.UUID) -> Job | None:
    """Mark a job as running. Returns None if it doesn't exist or already finished."""
    job = await session.get(Job, job_id, with_for_update=True)
    if job is None or job.status in (JobStatus.SUCCEEDED, JobStatus.PARTIAL, JobStatus.FAILED):
        return None
    job.status = JobStatus.RUNNING
    job.attempts += 1
    job.started_at = job.started_at or datetime.now(UTC)
    await session.commit()
    return job


async def finish_job(
    sessionmaker: async_sessionmaker[AsyncSession], job_id: uuid.UUID, error: str | None = None
) -> Job:
    """Recount outcomes from the conversations themselves and set the final status.

    Counting from current state (instead of incrementing as we go) keeps the
    numbers right when a job was retried part-way through.
    """
    async with sessionmaker() as session:
        job = await session.get(Job, job_id, with_for_update=True)
        assert job is not None
        ids = job.conversation_ids
        processed = await session.scalar(
            select(func.count())
            .select_from(Conversation)
            .where(
                Conversation.id.in_(ids), Conversation.processed_hash == Conversation.content_hash
            )
        )
        job.processed = processed or 0
        job.failed = job.total - job.processed
        if job.failed == 0:
            job.status = JobStatus.SUCCEEDED
        elif job.processed == 0:
            job.status = JobStatus.FAILED
        else:
            job.status = JobStatus.PARTIAL
        job.error = error
        job.finished_at = datetime.now(UTC)
        await session.commit()
        logger.info(
            "ingestion_job_finished",
            job_id=str(job.id),
            status=job.status.value,
            processed=job.processed,
            failed=job.failed,
            attempts=job.attempts,
        )
        return job
