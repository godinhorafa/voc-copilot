from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import Chunk
from app.llm.fake import FakeLLMProvider
from app.workers.tasks import process_ingestion_job
from tests.factories import batch, conversation

pytestmark = pytest.mark.integration

PII = conversation(
    "pii",
    texts=(
        "Hi, how can I help?",
        "Email me at ana.souza@example.com or call +55 11 98765-4321.",
        "Sure. Can you confirm the card?",
        "It's 4111 1111 1111 1111, and again my email is ana.souza@example.com",
    ),
)


async def test_pii_is_redacted_before_storage_embedding_and_llm(
    client: AsyncClient,
    worker_ctx: dict[str, Any],
    db: async_sessionmaker[AsyncSession],
) -> None:
    job_id = (await client.post("/v1/conversations", json=batch(PII))).json()["job_id"]
    llm = FakeLLMProvider.permissive()
    worker_ctx["llm"] = llm
    await process_ingestion_job(worker_ctx, job_id)

    detail = (await client.get("/v1/conversations/pii")).json()
    texts = [t["text"] for t in detail["turns"]]
    assert texts[1] == "Email me at [EMAIL_1] or call [PHONE_1]."
    assert texts[3] == "It's [CARD_1], and again my email is [EMAIL_1]"
    assert detail["redactions"] == {"EMAIL": 2, "PHONE": 1, "CARD": 1}

    async with db() as s:
        chunk_texts = list(await s.scalars(select(Chunk.text)))
    joined = "\n".join(chunk_texts)
    assert "ana.souza" not in joined
    assert "[EMAIL_1]" in joined

    prompt = llm.calls[-1][-1].content
    assert "ana.souza" not in prompt
    assert "4111" not in prompt

    # The content hash is of the original payload, so a re-upload is unchanged.
    again = (await client.post("/v1/conversations", json=batch(PII))).json()
    assert again["unchanged"] == 1


async def test_redaction_can_be_turned_off(app: FastAPI, client: AsyncClient) -> None:
    app.state.redactor = None
    await client.post("/v1/conversations", json=batch(PII))
    detail = (await client.get("/v1/conversations/pii")).json()
    assert "ana.souza@example.com" in detail["turns"][1]["text"]
    assert detail["redactions"] == {}
