import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import Conversation, Job, JobStatus, Turn
from tests.conftest import FakeQueue
from tests.factories import batch, conversation

pytestmark = pytest.mark.integration


async def _count(db: async_sessionmaker[AsyncSession], model: type) -> int:
    async with db() as s:
        return await s.scalar(select(func.count()).select_from(model)) or 0


async def test_upload_creates_conversations_and_queues_job(
    client: AsyncClient, queue: FakeQueue, db: async_sessionmaker[AsyncSession]
) -> None:
    response = await client.post(
        "/v1/conversations", json=batch(conversation("a"), conversation("b"))
    )

    assert response.status_code == 202
    body = response.json()
    assert body | {"job_id": None} == {
        "job_id": None,
        "created": 2,
        "updated": 0,
        "unchanged": 0,
        "queued": 2,
    }
    job_id = uuid.UUID(body["job_id"])
    assert queue.enqueued == [job_id]
    assert await _count(db, Turn) == 8

    job = (await client.get(f"/v1/jobs/{job_id}")).json()
    assert job["status"] == "queued"
    assert job["total"] == 2


async def test_reupload_of_unprocessed_conversation_queues_it_again(
    client: AsyncClient, queue: FakeQueue
) -> None:
    await client.post("/v1/conversations", json=batch(conversation("a")))
    response = await client.post("/v1/conversations", json=batch(conversation("a")))
    assert response.status_code == 202
    assert response.json()["unchanged"] == 1
    assert response.json()["queued"] == 1
    assert len(queue.enqueued) == 2


async def test_reupload_of_processed_conversation_is_a_noop(
    client: AsyncClient, queue: FakeQueue, db: async_sessionmaker[AsyncSession]
) -> None:
    await client.post("/v1/conversations", json=batch(conversation("a")))
    async with db() as s:
        conv = await s.scalar(select(Conversation))
        assert conv is not None
        conv.processed_hash = conv.content_hash
        await s.commit()

    response = await client.post("/v1/conversations", json=batch(conversation("a")))
    assert response.status_code == 200
    assert response.json() == {
        "job_id": None,
        "created": 0,
        "updated": 0,
        "unchanged": 1,
        "queued": 0,
    }
    assert len(queue.enqueued) == 1


async def test_changed_content_replaces_turns(
    client: AsyncClient, db: async_sessionmaker[AsyncSession]
) -> None:
    await client.post("/v1/conversations", json=batch(conversation("a")))
    changed = conversation("a", texts=("Hello", "Totally different", "Ok"))
    response = await client.post("/v1/conversations", json=batch(changed))

    assert response.json()["updated"] == 1
    detail = (await client.get("/v1/conversations/a")).json()
    assert [t["text"] for t in detail["turns"]] == ["Hello", "Totally different", "Ok"]
    assert await _count(db, Turn) == 3
    assert await _count(db, Conversation) == 1


async def test_enqueue_failure_returns_503_and_keeps_data(
    client: AsyncClient, queue: FakeQueue, db: async_sessionmaker[AsyncSession]
) -> None:
    async def broken(job_id: uuid.UUID) -> None:
        raise ConnectionError("redis down")

    queue.enqueue_ingestion = broken  # type: ignore[method-assign]
    response = await client.post("/v1/conversations", json=batch(conversation("a")))
    assert response.status_code == 503
    assert await _count(db, Conversation) == 1
    async with db() as s:
        job = await s.scalar(select(Job))
    assert job is not None
    assert job.status == JobStatus.QUEUED


async def test_rejects_invalid_payload(client: AsyncClient) -> None:
    response = await client.post(
        "/v1/conversations", json=batch(conversation("x"), conversation("x"))
    )
    assert response.status_code == 422


async def test_get_unknown_conversation_and_job(client: AsyncClient) -> None:
    assert (await client.get("/v1/conversations/nope")).status_code == 404
    assert (await client.get(f"/v1/jobs/{uuid.uuid4()}")).status_code == 404


async def test_customers_upsert(client: AsyncClient) -> None:
    customer = {"id": "cust-1", "industry": "telecom", "plan_code": "LM-PLUS-50"}
    first = await client.put("/v1/customers", json={"customers": [customer]})
    assert first.json() == {"upserted": 1}

    churned = customer | {"churned_at": "2026-09-10", "churn_reason": "price_increase"}
    second = await client.put("/v1/customers", json={"customers": [churned]})
    assert second.status_code == 200
