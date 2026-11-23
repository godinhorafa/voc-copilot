import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import httpx2
import openai
import pytest
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.llm.base import Message
from app.llm.fake import FakeLLMProvider
from evals import ci, sql_eval
from evals.cache import CachedLLM, Mode, ProviderUnavailableError
from evals.sql_eval import load_questions
from tests.conftest import make_settings
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


class _NoCredits(FakeLLMProvider):
    def __init__(self) -> None:
        super().__init__()
        self.calls_made = 0

    async def complete_structured(self, *args: object, **kwargs: object) -> Any:
        self.calls_made += 1
        request = httpx2.Request("POST", "https://api.openai.com/v1/chat/completions")
        raise openai.RateLimitError(
            "Error code: 429 - You have no credits remaining.",
            response=httpx2.Response(429, request=request),
            body=None,
        )


async def test_recording_stops_at_the_first_refusal(
    db: async_sessionmaker[AsyncSession],
    migrated_db: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ci, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(ci, "CACHE_MANIFEST", tmp_path / "cache" / "manifest.json")
    provider = _NoCredits()
    engine = create_async_engine(migrated_db)
    try:
        with pytest.raises(ProviderUnavailableError, match="no credits remaining"):
            await ci.run_llm_evals(engine, db, ci.Suite(), "record", provider)
    finally:
        await engine.dispose()
    # Extraction is first: one call, not one per sampled conversation.
    assert provider.calls_made == 1
    assert not (tmp_path / "cache" / "manifest.json").exists()


def test_main_reports_the_refusal_in_one_line(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    async def refused(url: str, *, mode: Mode) -> ci.Suite:
        raise ProviderUnavailableError("RateLimitError: You have no credits remaining.")

    monkeypatch.setattr(ci, "run", refused)
    monkeypatch.setattr(ci, "get_settings", lambda: make_settings(llm_provider="ollama"))
    assert ci.main(["--record", "--database-url", "postgresql+asyncpg://x@y/z"]) == 2
    err = capsys.readouterr().err
    assert "Recording stopped" in err
    assert "no credits remaining" in err
    assert "Traceback" not in err
