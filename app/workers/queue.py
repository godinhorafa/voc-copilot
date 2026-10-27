"""Job queue abstraction so the API does not depend on arq directly."""

import uuid
from typing import Protocol

from arq.connections import ArqRedis

INGESTION_TASK = "process_ingestion_job"


class JobQueue(Protocol):
    async def enqueue_ingestion(self, job_id: uuid.UUID) -> None: ...


class ArqJobQueue:
    def __init__(self, redis: ArqRedis) -> None:
        self._redis = redis

    async def enqueue_ingestion(self, job_id: uuid.UUID) -> None:
        # A deterministic arq job id makes double-enqueueing the same job a no-op.
        await self._redis.enqueue_job(INGESTION_TASK, str(job_id), _job_id=f"ingest:{job_id}")
