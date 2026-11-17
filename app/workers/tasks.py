"""arq tasks.

Retry policy for an ingestion job: any exception while processing a
conversation (provider timeout, rate limit, DB hiccup) retries the whole job
with linear backoff. Processing is idempotent, so a retry skips what is done.
On the last attempt, failing conversations are recorded and skipped instead,
and the job finishes as `partial` or `failed`.

Jobs carry their tenant: every database session of the job is scoped to it,
like the API request that created the job (ADR 0012).
"""

import uuid
from typing import Any

import structlog
from arq import Retry

from app.core.config import Settings
from app.db.models import DEFAULT_TENANT_ID
from app.db.session import tenant_sessionmaker
from app.ingestion.extraction import InsightExtractor
from app.ingestion.pipeline import (
    PipelineConfig,
    finish_job,
    mark_failed,
    process_conversation,
    start_job,
)
from app.observability.tracing import tracer
from app.observability.usage import DEFAULT_PRICES, load_prices, metered

logger = structlog.get_logger(__name__)


async def process_ingestion_job(
    ctx: dict[str, Any], job_id: str, tenant_id: str | None = None
) -> str:
    # Jobs queued before tenants existed carry no tenant: theirs is the default one.
    tenant = uuid.UUID(tenant_id) if tenant_id else DEFAULT_TENANT_ID
    with (
        tracer().start_as_current_span("ingestion.job") as span,
        metered() as meter,
    ):
        span.set_attribute("job.id", job_id)
        span.set_attribute("job.attempt", ctx.get("job_try", 1))
        span.set_attribute("voc.tenant_id", str(tenant))
        outcome = await _process_ingestion_job(ctx, job_id, tenant)
        settings: Settings = ctx["settings"]
        _, cost = meter.priced(load_prices(settings.prices_file or DEFAULT_PRICES))
        logger.info(
            "ingestion_job_usage",
            job_id=job_id,
            tenant_id=str(tenant),
            outcome=outcome,
            input_tokens=meter.input_tokens,
            output_tokens=meter.output_tokens,
            cost_usd=cost,
        )
        return outcome


async def _process_ingestion_job(ctx: dict[str, Any], job_id: str, tenant: uuid.UUID) -> str:
    settings: Settings = ctx["settings"]
    sessionmaker = tenant_sessionmaker(ctx["engine"], tenant)
    embeddings = ctx["embeddings"]
    extractor = InsightExtractor(ctx["llm"])
    attempt: int = ctx.get("job_try", 1)
    last_attempt = attempt >= settings.ingestion_max_tries
    job_uuid = uuid.UUID(job_id)
    log = logger.bind(job_id=job_id, tenant_id=str(tenant), attempt=attempt)

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
            await process_conversation(sessionmaker, embeddings, extractor, conversation_id, config)
        except Exception as exc:
            if not last_attempt:
                defer = settings.ingestion_retry_base_s * attempt
                log.warning("ingestion_job_retry", error=repr(exc), defer_s=defer)
                raise Retry(defer=defer) from exc
            log.error("conversation_failed", conversation_id=str(conversation_id), error=repr(exc))
            await mark_failed(sessionmaker, conversation_id, repr(exc))

    finished = await finish_job(sessionmaker, job_uuid)
    return finished.status.value
