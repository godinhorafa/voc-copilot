"""Keyword search with PostgreSQL full-text search.

The question is turned into an OR of its stemmed terms (stopwords removed by
the `english` configuration): `plainto_tsquery` would AND every word and almost
never match a natural-language question. Chunks are ranked with `ts_rank_cd`
(cover density) normalized by document length. Postgres has no BM25 and no
IDF, so frequent terms weigh as much as rare ones; fusion with vector search
(and reranking) is what compensates.
"""

from sqlalchemy import func, literal_column, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Chunk, Conversation
from app.retrieval.filters import apply_filters
from app.retrieval.types import RetrievedChunk, SearchFilters

# ts_rank_cd normalization: 1 = divide by 1 + log(document length).
RANK_NORMALIZATION = 1


async def or_tsquery(session: AsyncSession, query: str) -> str | None:
    """The question's stemmed terms joined with OR, or None if nothing is left."""
    lexemes: list[str] = list(
        await session.scalars(
            text("SELECT unnest(tsvector_to_array(to_tsvector('english', :q)))"), {"q": query}
        )
    )
    # Quote each lexeme so characters like ':' or '!' can't change the query syntax.
    return " | ".join("'" + lexeme.replace("'", "''") + "'" for lexeme in sorted(lexemes)) or None


async def keyword_search(
    session: AsyncSession, query: str, *, k: int, filters: SearchFilters
) -> list[RetrievedChunk]:
    if k <= 0:
        raise ValueError("k must be positive")
    tsquery_text = await or_tsquery(session, query)
    if tsquery_text is None:
        return []
    tsquery = func.to_tsquery(literal_column("'english'"), tsquery_text)
    rank = func.ts_rank_cd(Chunk.tsv, tsquery, RANK_NORMALIZATION)
    stmt = (
        select(
            Chunk.id,
            Chunk.conversation_id,
            Conversation.external_id,
            Conversation.started_at,
            Chunk.text,
            rank.label("rank"),
        )
        .join(Conversation, Conversation.id == Chunk.conversation_id)
        .where(Chunk.tsv.op("@@")(tsquery))
        .order_by(rank.desc(), Chunk.id)
        .limit(k)
    )
    rows = (await session.execute(apply_filters(stmt, filters))).all()
    return [
        RetrievedChunk(
            chunk_id=r.id,
            conversation_id=r.conversation_id,
            external_id=r.external_id,
            started_at=r.started_at,
            text=r.text,
            score=float(r.rank),
        )
        for r in rows
    ]
