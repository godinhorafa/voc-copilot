import json
from collections.abc import Sequence
from pathlib import Path

import pytest
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.llm.base import Message
from app.llm.fake import FakeLLMProvider
from evals.sql_eval import evaluate, load_questions, to_markdown
from tests.helpers import DATASET, ingest_payloads

pytestmark = pytest.mark.integration

GOLDEN = Path(__file__).parents[2] / "evals" / "datasets" / "sql_golden.jsonl"


def _llm(answers: dict[str, str]) -> FakeLLMProvider:
    def answer(schema: type[BaseModel], messages: Sequence[Message]) -> BaseModel:
        return schema.model_validate({"sql": answers[messages[-1].content], "explanation": "x"})

    return FakeLLMProvider(structured=answer)


async def test_oracle_scores_one_and_wrong_answers_are_caught(
    db: async_sessionmaker[AsyncSession], migrated_db: str
) -> None:
    lines = (DATASET / "conversations.jsonl").read_text().splitlines()[:120]
    await ingest_payloads(db, [json.loads(line) for line in lines])
    questions = load_questions(GOLDEN)
    engine = create_async_engine(migrated_db)
    try:
        oracle = await evaluate(
            questions,
            engine=engine,
            llm=_llm({q.question: q.sql for q in questions}),
            model="oracle",
            golden="sql_golden",
        )
        # Swap answers between questions: almost all results should now differ.
        rotated = [q.sql for q in questions[1:]] + [questions[0].sql]
        wrong = await evaluate(
            questions,
            engine=engine,
            llm=_llm({q.question: sql for q, sql in zip(questions, rotated, strict=True)}),
            model="wrong",
            golden="sql_golden",
        )
    finally:
        await engine.dispose()

    assert oracle.accuracy == 1.0
    assert oracle.errors == 0
    assert wrong.accuracy < 0.3
    assert "execution accuracy **1.000**" in to_markdown(oracle)
