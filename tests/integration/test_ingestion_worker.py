import uuid
from collections.abc import Sequence
from typing import Any

import pytest
from arq import Retry
from httpx import AsyncClient
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import DEFAULT_TENANT_ID, Chunk, Customer, Job
from app.llm.fake import FakeEmbeddingProvider
from app.workers.tasks import process_ingestion_job
from tests.factories import batch, conversation

pytestmark = pytest.mark.integration


class FlakyEmbeddings(FakeEmbeddingProvider):
    """Fails on texts containing a marker word."""

    def __init__(self, marker: str) -> None:
        super().__init__()
        self.marker = marker

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if any(self.marker in t for t in texts):
            raise TimeoutError("embedding provider timed out")
        return await super().embed(texts)


async def _upload(client: AsyncClient, *convs: dict[str, Any]) -> str:
    response = await client.post("/v1/conversations", json=batch(*convs))
    job_id: str = response.json()["job_id"]
    return job_id


async def _chunks(db: async_sessionmaker[AsyncSession]) -> int:
    async with db() as s:
        return await s.scalar(select(func.count()).select_from(Chunk)) or 0


async def test_job_creates_chunks_and_succeeds(
    client: AsyncClient, worker_ctx: dict[str, Any], db: async_sessionmaker[AsyncSession]
) -> None:
    job_id = await _upload(client, conversation("a"), conversation("b"))

    assert await process_ingestion_job(worker_ctx, job_id) == "succeeded"

    job = (await client.get(f"/v1/jobs/{job_id}")).json()
    assert (job["status"], job["processed"], job["failed"], job["attempts"]) == (
        "succeeded",
        2,
        0,
        1,
    )
    detail = (await client.get("/v1/conversations/a")).json()
    assert detail["status"] == "processed"
    assert detail["chunk_count"] >= 1
    async with db() as s:
        chunk = await s.scalar(select(Chunk))
    assert chunk is not None
    assert chunk.embedding_model == "fake-embedding"
    assert len(chunk.embedding) == 1536
    assert chunk.text.startswith("agent: ")


async def test_rerunning_a_job_is_idempotent(
    client: AsyncClient, worker_ctx: dict[str, Any], db: async_sessionmaker[AsyncSession]
) -> None:
    job_id = await _upload(client, conversation("a"))
    await process_ingestion_job(worker_ctx, job_id)
    before = await _chunks(db)

    # A duplicate delivery of a finished job is skipped.
    assert await process_ingestion_job(worker_ctx, job_id) == "skipped"
    # A new job for the same content does nothing either.
    async with db() as s:
        original = await s.get(Job, uuid.UUID(job_id))
        assert original is not None
        job = Job(conversation_ids=original.conversation_ids, total=1)
        s.add(job)
        await s.commit()
    assert await process_ingestion_job(worker_ctx, str(job.id)) == "succeeded"
    assert await _chunks(db) == before


async def test_updated_conversation_is_rechunked(
    client: AsyncClient, worker_ctx: dict[str, Any], db: async_sessionmaker[AsyncSession]
) -> None:
    await process_ingestion_job(worker_ctx, await _upload(client, conversation("a")))
    changed = conversation("a", texts=("Hello", "Router shows ERR-4G-503", "Rebooting now"))
    await process_ingestion_job(worker_ctx, await _upload(client, changed))

    async with db() as s:
        texts = list(await s.scalars(select(Chunk.text)))
    assert len(texts) == 1
    assert "ERR-4G-503" in texts[0]


async def test_transient_failure_retries_then_resumes(
    client: AsyncClient, worker_ctx: dict[str, Any]
) -> None:
    job_id = await _upload(
        client, conversation("ok"), conversation("bad", texts=("Hi", "TIMEOUTME please"))
    )
    worker_ctx["embeddings"] = FlakyEmbeddings("TIMEOUTME")

    with pytest.raises(Retry):
        await process_ingestion_job(worker_ctx, job_id)
    assert (await client.get("/v1/conversations/ok")).json()["status"] == "processed"

    # Provider recovers; the retry only has the failed conversation left to do.
    worker_ctx["embeddings"] = FakeEmbeddingProvider()
    worker_ctx["job_try"] = 2
    assert await process_ingestion_job(worker_ctx, job_id) == "succeeded"
    job = (await client.get(f"/v1/jobs/{job_id}")).json()
    assert (job["processed"], job["attempts"]) == (2, 2)


async def test_last_attempt_records_failures_and_finishes_partial(
    client: AsyncClient, worker_ctx: dict[str, Any]
) -> None:
    job_id = await _upload(
        client, conversation("ok"), conversation("bad", texts=("Hi", "TIMEOUTME please"))
    )
    worker_ctx["embeddings"] = FlakyEmbeddings("TIMEOUTME")
    worker_ctx["job_try"] = worker_ctx["settings"].ingestion_max_tries

    assert await process_ingestion_job(worker_ctx, job_id) == "partial"
    job = (await client.get(f"/v1/jobs/{job_id}")).json()
    assert (job["processed"], job["failed"]) == (1, 1)
    bad = (await client.get("/v1/conversations/bad")).json()
    assert bad["status"] == "failed"
    assert "timed out" in bad["processing_error"]


async def test_chunks_are_searchable_by_keyword_and_vector(
    client: AsyncClient, worker_ctx: dict[str, Any], db: async_sessionmaker[AsyncSession]
) -> None:
    billing = conversation("billing", texts=("Hi", "I was charged twice on my invoice", "Refunded"))
    network = conversation("network", texts=("Hi", "No signal, router error ERR-4G-503", "Ok"))
    await process_ingestion_job(worker_ctx, await _upload(client, billing, network))

    async with db() as s:
        keyword = await s.scalar(
            text(
                "SELECT c.external_id FROM chunks ch "
                "JOIN conversations c ON c.id = ch.conversation_id "
                "WHERE ch.tsv @@ plainto_tsquery('english', 'invoice charged')"
            )
        )
        [query_vec] = await FakeEmbeddingProvider().embed(["charged twice invoice"])
        nearest = await s.scalar(
            select(Chunk.conversation_id)
            .order_by(Chunk.embedding.cosine_distance(query_vec))
            .limit(1)
        )
        nearest_ext = await s.scalar(
            text("SELECT external_id FROM conversations WHERE id = :id"), {"id": nearest}
        )
    assert keyword == "billing"
    assert nearest_ext == "billing"


async def test_customers_are_stored(
    client: AsyncClient, db: async_sessionmaker[AsyncSession]
) -> None:
    await client.put(
        "/v1/customers",
        json={"customers": [{"id": "c1", "industry": "telecom", "churned_at": "2026-09-10"}]},
    )
    async with db() as s:
        customer = await s.get(Customer, (DEFAULT_TENANT_ID, "c1"))
    assert customer is not None
    assert customer.churned_at is not None
    assert customer.churned_at.isoformat() == "2026-09-10"
