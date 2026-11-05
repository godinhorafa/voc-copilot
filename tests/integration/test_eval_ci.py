import json
from collections.abc import Sequence
from pathlib import Path

import pytest
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.llm.base import Message
from app.llm.fake import FakeLLMProvider
from evals import ci, sql_eval
from evals.cache import CachedLLM, Mode
from evals.sql_eval import load_questions
from tests.helpers import DATASET, ingest_payloads

pytestmark = pytest.mark.integration


def _oracle() -> FakeLLMProvider:
    answers = {q.question: q.sql for q in load_questions(ci.SQL_GOLDEN)}

    def answer(schema: type[BaseModel], messages: Sequence[Message]) -> BaseModel:
        return schema.model_validate({"sql": answers[messages[-1].content], "explanation": "x"})

    return FakeLLMProvider(structured=answer)


async def test_sql_eval_records_then_replays_without_a_model(
    db: async_sessionmaker[AsyncSession],
    migrated_db: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ci, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(ci, "CACHE_MANIFEST", tmp_path / "cache" / "manifest.json")
    monkeypatch.setattr(ci, "AGENT_GOLDEN", tmp_path / "none.jsonl")  # keep the test focused
    lines = (DATASET / "conversations.jsonl").read_text().splitlines()[:80]
    await ingest_payloads(db, [json.loads(line) for line in lines])
    engine = create_async_engine(migrated_db)

    async def sql_only(mode: Mode, real: FakeLLMProvider | None) -> ci.Suite:
        suite = ci.Suite()

        async def sql(llm: CachedLLM, model: str) -> dict[str, float]:
            result = await sql_eval.evaluate(
                load_questions(ci.SQL_GOLDEN), engine=engine, llm=llm, model=model, golden="g"
            )
            return {"execution_accuracy": result.accuracy}

        await ci._guarded(suite, "sql", mode, real, sql)
        return suite

    try:
        skipped = await sql_only("replay", None)
        recorded = await sql_only("record", _oracle())
        replayed = await sql_only("replay", None)  # no provider at all
        # A changed question is a request the cache has never seen.
        golden = tmp_path / "golden.jsonl"
        first = json.loads(ci.SQL_GOLDEN.read_text().splitlines()[0])
        golden.write_text(json.dumps(first | {"question": first["question"] + " Please."}) + "\n")
        monkeypatch.setattr(ci, "SQL_GOLDEN", golden)
        stale = await sql_only("replay", None)
    finally:
        await engine.dispose()

    assert "no recorded LLM responses" in skipped.skipped["sql"]
    assert recorded.metrics == {"sql.execution_accuracy": 1.0}
    assert replayed.metrics == {"sql.execution_accuracy": 1.0}
    manifest = json.loads((tmp_path / "cache" / "manifest.json").read_text())
    assert manifest["sql"]["model"] == "fake:gpt-4o-mini"
    # A stale cache fails the eval instead of scoring it.
    assert stale.metrics == {}
    assert "make evals-record" in stale.failed["sql"]
