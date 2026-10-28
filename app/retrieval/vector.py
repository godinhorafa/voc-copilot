"""Vector search over chunks with pgvector (cosine distance, HNSW index).

Filters are applied in the same query. With a selective filter (one industry,
one month) a plain HNSW scan can return fewer than k rows, because it filters
after visiting `ef_search` candidates; pgvector >= 0.8 iterative scans keep
searching until k matching rows are found.
"""

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Chunk, Conversation
from app.retrieval.types import RetrievedChunk, SearchFilters

EF_SEARCH = 100


async def vector_search(
    session: AsyncSession, query_embedding: list[float], *, k: int, filters: SearchFilters
) -> list[RetrievedChunk]:
    if k <= 0:
        raise ValueError("k must be positive")
    await session.execute(text(f"SET LOCAL hnsw.ef_search = {EF_SEARCH}"))
    await session.execute(text("SET LOCAL hnsw.iterative_scan = relaxed_order"))

    distance = Chunk.embedding.cosine_distance(query_embedding)
    stmt = (
        select(
            Chunk.id,
            Chunk.conversation_id,
            Conversation.external_id,
            Conversation.started_at,
            Chunk.text,
            distance.label("distance"),
        )
        .join(Conversation, Conversation.id == Chunk.conversation_id)
        .order_by(distance)
        .limit(k)
    )
    if filters.industry:
        stmt = stmt.where(Conversation.industry == filters.industry)
    if filters.start:
        stmt = stmt.where(Conversation.started_at >= filters.start)
    if filters.end:
        stmt = stmt.where(Conversation.started_at < filters.end)

    rows = (await session.execute(stmt)).all()
    # relaxed_order may return rows slightly out of order; restore exact order.
    return sorted(
        (
            RetrievedChunk(
                chunk_id=r.id,
                conversation_id=r.conversation_id,
                external_id=r.external_id,
                started_at=r.started_at,
                text=r.text,
                score=1.0 - float(r.distance),
            )
            for r in rows
        ),
        key=lambda c: -c.score,
    )
