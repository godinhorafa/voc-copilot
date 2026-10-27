"""arq tasks.

Retry policy for an ingestion job: any exception while processing a
conversation (provider timeout, rate limit, DB hiccup) retries the whole job
with linear backoff. Processing is idempotent, so a retry skips what is done.
On the last attempt, failing conversations are recorded and skipped instead,
and the job finishes as `partial` or `failed`.
"""

import uuid
from typing import Any

import structlog
from arq import Retry

from app.core.config import Settings
from app.ingestion.pipeline import (
    PipelineConfig,
    finish_job,
    mark_failed,
    process_conversation,
    start_job,
)

logger = structlog.get_logger(__name__)


async def process_ingestion_job(ctx: dict[str, Any], job_id: str) -> str:
    settings: Settings = ctx["settings"]
    sessionmaker = ctx["sessionmaker"]
    embeddings = ctx["embeddings"]
    attempt: int = ctx.get("job_try", 1)
    last_attempt = attempt >= settings.ingestion_max_tries
    job_uuid = uuid.UUID(job_id)
    log = logger.bind(job_id=job_id, attempt=attempt)

    async with sessionmaker() as session:
        job = await start_job(session, job_uuid)
    if job is None:
        log.info("ingestion_job_skipped")
        return "skipped"

    config = PipelineConfig(
        chunk_max_words=settings.chunk_max_words,
        chunk_overlap_turns=settings.chunk_overlap_turns,
        embedding_batch_size=settings.embedding_batch_size,
    )
    for conversation_id in job.conversation_ids:
        try:
            await process_conversation(sessionmaker, embeddings, conversation_id, config)
        except Exception as exc:
            if not last_attempt:
                defer = settings.ingestion_retry_base_s * attempt
                log.warning("ingestion_job_retry", error=repr(exc), defer_s=defer)
                raise Retry(defer=defer) from exc
            log.error("conversation_failed", conversation_id=str(conversation_id), error=repr(exc))
            await mark_failed(sessionmaker, conversation_id, repr(exc))

    finished = await finish_job(sessionmaker, job_uuid)
    return finished.status.value
