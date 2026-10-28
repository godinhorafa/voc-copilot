import json
from collections.abc import Sequence
from typing import Any

import pytest
from httpx import AsyncClient
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import Insight
from app.ingestion.extraction import extractor_version
from app.llm.base import Message, StructuredCompletion
from app.llm.fake import FakeEmbeddingProvider, FakeLLMProvider
from app.workers.tasks import process_ingestion_job
from tests.factories import batch, conversation

pytestmark = pytest.mark.integration

ANSWER = json.dumps(
    {
        "contact_reason": "billing_dispute",
        "sentiment": "negative",
        "resolved": False,
        "churn_risk": "medium",
        "competitors": ["NovaTel"],
        "summary": "Customer complains about an unannounced price increase.",
    }
)


class CountingEmbeddings(FakeEmbeddingProvider):
    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls += 1
        return await super().embed(texts)


class BrokenLLM(FakeLLMProvider):
    async def complete_structured[T: BaseModel](
        self, messages: Sequence[Message], schema: type[T], *, temperature: float = 0.0
    ) -> StructuredCompletion[T]:
        raise TimeoutError("llm timed out")


async def _upload(client: AsyncClient, *convs: dict[str, Any]) -> str:
    job_id: str = (await client.post("/v1/conversations", json=batch(*convs))).json()["job_id"]
    return job_id


async def test_job_stores_insight(
    client: AsyncClient, worker_ctx: dict[str, Any], db: async_sessionmaker[AsyncSession]
) -> None:
    worker_ctx["llm"] = FakeLLMProvider([ANSWER])
    await process_ingestion_job(worker_ctx, await _upload(client, conversation("a")))

    insight = (await client.get("/v1/conversations/a")).json()["insight"]
    assert insight["contact_reason"] == "billing_dispute"
    assert insight["competitors"] == ["NovaTel"]
    assert insight["extractor_version"] == extractor_version()
    assert insight["model"] == "fake-llm"
    async with db() as s:
        row = await s.scalar(select(Insight))
    assert row is not None
    assert row.input_tokens > 0


async def test_failed_extraction_keeps_chunks_and_retry_only_extracts(
    client: AsyncClient, worker_ctx: dict[str, Any]
) -> None:
    job_id = await _upload(client, conversation("a"))
    embeddings = CountingEmbeddings()
    worker_ctx["embeddings"] = embeddings
    worker_ctx["llm"] = BrokenLLM()
    worker_ctx["job_try"] = worker_ctx["settings"].ingestion_max_tries

    assert await process_ingestion_job(worker_ctx, job_id) == "failed"
    detail = (await client.get("/v1/conversations/a")).json()
    assert (detail["status"], detail["chunk_count"], detail["insight"]) == ("failed", 1, None)
    assert "llm timed out" in detail["processing_error"]
    assert embeddings.calls == 1

    # Re-upload queues it again; only extraction runs this time.
    worker_ctx["llm"] = FakeLLMProvider([ANSWER])
    worker_ctx["job_try"] = 1
    assert await process_ingestion_job(worker_ctx, await _upload(client, conversation("a"))) == (
        "succeeded"
    )
    assert embeddings.calls == 1
    detail = (await client.get("/v1/conversations/a")).json()
    assert detail["status"] == "processed"
    assert detail["insight"]["contact_reason"] == "billing_dispute"


async def test_changed_content_reextracts(client: AsyncClient, worker_ctx: dict[str, Any]) -> None:
    worker_ctx["llm"] = FakeLLMProvider([ANSWER])
    await process_ingestion_job(worker_ctx, await _upload(client, conversation("a")))

    changed = conversation("a", texts=("Hi", "My router shows ERR-4G-503", "Fixed it"))
    worker_ctx["llm"] = FakeLLMProvider([ANSWER.replace("billing_dispute", "technical_issue")])
    await process_ingestion_job(worker_ctx, await _upload(client, changed))

    insight = (await client.get("/v1/conversations/a")).json()["insight"]
    assert insight["contact_reason"] == "technical_issue"
