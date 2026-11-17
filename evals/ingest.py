"""In-process ingestion for evals and tests: the production pipeline without
the API, the queue or the worker."""

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.ingestion.extraction import InsightExtractor
from app.ingestion.pipeline import PipelineConfig, process_conversation
from app.ingestion.service import ingest_conversations, upsert_customers
from app.llm.base import EmbeddingProvider, LLMProvider
from app.llm.fake import FakeEmbeddingProvider, FakeLLMProvider
from app.privacy.redaction import Redactor
from app.schemas.conversation import ConversationIn
from app.schemas.customer import CustomerIn

BATCH = 500


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


async def ingest_payloads(
    sessionmaker: async_sessionmaker[AsyncSession],
    payloads: Iterable[dict[str, Any]],
    *,
    embeddings: EmbeddingProvider | None = None,
    llm: LLMProvider | None = None,
    redactor: Redactor | None = None,
) -> int:
    """Upload and fully process conversations. Defaults to the offline fakes."""
    embeddings = embeddings or FakeEmbeddingProvider()
    extractor = InsightExtractor(llm or FakeLLMProvider.permissive())
    items = [ConversationIn.model_validate(p) for p in payloads]
    processed = 0
    for i in range(0, len(items), BATCH):
        async with sessionmaker() as session:
            result = await ingest_conversations(session, items[i : i + BATCH], redactor=redactor)
        for conversation_id in result.to_process:
            await process_conversation(
                sessionmaker, embeddings, extractor, conversation_id, PipelineConfig()
            )
            processed += 1
    return processed


async def ingest_dataset(
    sessionmaker: async_sessionmaker[AsyncSession],
    dataset: Path,
    *,
    embeddings: EmbeddingProvider | None = None,
    llm: LLMProvider | None = None,
) -> int:
    """Customers and conversations of a generated dataset; never its ground truth."""
    customers = [CustomerIn.model_validate(c) for c in read_jsonl(dataset / "customers.jsonl")]
    async with sessionmaker() as session:
        await upsert_customers(session, customers)
    return await ingest_payloads(
        sessionmaker,
        read_jsonl(dataset / "conversations.jsonl"),
        embeddings=embeddings,
        llm=llm,
    )
