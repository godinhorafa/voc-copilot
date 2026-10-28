import json
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.llm.fake import FakeEmbeddingProvider
from evals.build_golden import build, write
from evals.retrieval_eval import check_ingested, evaluate, load_golden
from tests.helpers import DATASET, ingest_payloads

pytestmark = pytest.mark.integration


async def test_retrieval_eval_end_to_end(
    db: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    golden_path = tmp_path / "golden.jsonl"
    write(build(DATASET), golden_path)
    golden = load_golden(golden_path)
    assert await check_ingested(db, golden) > 0

    lines = (DATASET / "conversations.jsonl").read_text().splitlines()
    await ingest_payloads(db, [json.loads(line) for line in lines])
    assert await check_ingested(db, golden) == 0

    result = await evaluate(golden_path, sessionmaker=db, embeddings=FakeEmbeddingProvider(), k=10)
    assert len(result.questions) == len(golden)
    assert 0 < result.overall.mrr <= 1
    assert all(len(q.top) == 10 for q in result.questions)
    # Competitor questions have no filters, so a random ranking would score
    # |relevant| / 500 on precision. Hashed bag-of-words embeddings match the
    # competitor name verbatim and should do far better than chance.
    competitor = [q for q in golden if q.type == "competitor"]
    chance = sum(len(q.relevant) / len(lines) for q in competitor) / len(competitor)
    assert result.by_type["competitor"].precision > 4 * chance
