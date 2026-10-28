"""End to end through Redis: ArqJobQueue enqueues, a real arq worker processes."""

import uuid
from typing import Any

import pytest
from arq.connections import ArqRedis
from arq.worker import Worker
from httpx import AsyncClient
from redis.asyncio import ConnectionPool

from app.core.config import Settings
from app.workers.queue import ArqJobQueue
from app.workers.tasks import process_ingestion_job
from tests.factories import batch, conversation

pytestmark = pytest.mark.integration


async def test_upload_is_processed_by_arq_worker(
    client: AsyncClient, settings: Settings, worker_ctx: dict[str, Any]
) -> None:
    redis = ArqRedis(ConnectionPool.from_url(str(settings.redis_url)))
    await redis.flushdb()

    response = await client.post("/v1/conversations", json=batch(conversation("a")))
    job_id = response.json()["job_id"]
    await ArqJobQueue(redis).enqueue_ingestion(uuid.UUID(job_id))
    # Enqueueing the same job twice is deduplicated by arq.
    await ArqJobQueue(redis).enqueue_ingestion(uuid.UUID(job_id))

    async def on_startup(ctx: dict[str, Any]) -> None:
        ctx.update({k: v for k, v in worker_ctx.items() if k != "job_try"})

    worker = Worker(
        functions=[process_ingestion_job],
        redis_pool=redis,
        on_startup=on_startup,
        burst=True,
        poll_delay=0.05,
        max_tries=settings.ingestion_max_tries,
        handle_signals=False,
    )
    await worker.main()
    await worker.close()
    assert (worker.jobs_complete, worker.jobs_failed) == (1, 0)

    job = (await client.get(f"/v1/jobs/{job_id}")).json()
    assert job["status"] == "succeeded"
    assert (await client.get("/v1/conversations/a")).json()["status"] == "processed"
    await redis.aclose()
