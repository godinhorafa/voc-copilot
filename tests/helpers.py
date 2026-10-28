"""Test helpers that need the database."""

from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.ingestion.extraction import InsightExtractor
from app.ingestion.pipeline import PipelineConfig, process_conversation
from app.ingestion.service import ingest_conversations
from app.llm.fake import FakeEmbeddingProvider, FakeLLMProvider
from app.schemas.conversation import ConversationIn

DATASET = Path(__file__).parents[1] / "synthetic" / "datasets" / "telecom-seed42-n500"


async def ingest_payloads(
    db: async_sessionmaker[AsyncSession], payloads: list[dict[str, Any]]
) -> None:
    """Upload and fully process conversations in-process, with fake providers."""
    items = [ConversationIn.model_validate(p) for p in payloads]
    async with db() as session:
        result = await ingest_conversations(session, items)
    extractor = InsightExtractor(FakeLLMProvider.permissive())
    for conversation_id in result.to_process:
        await process_conversation(
            db, FakeEmbeddingProvider(), extractor, conversation_id, PipelineConfig()
        )
