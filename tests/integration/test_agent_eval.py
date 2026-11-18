import json
from collections.abc import Sequence
from pathlib import Path

import pytest
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.agent.orchestrator import Agent
from app.agent.tools import TOOLS
from app.analytics.text_to_sql import TextToSQL
from app.db.models import DEFAULT_TENANT_ID
from app.llm.base import Message, ToolSpec, ToolTurn
from app.llm.fake import FakeEmbeddingProvider, FakeLLMProvider, tool_turn
from app.retrieval.retriever import Retriever
from evals.agent_eval import AgentQuestion, evaluate, load_questions, to_markdown
from tests.factories import conversation
from tests.helpers import ingest_payloads

pytestmark = pytest.mark.integration

GOLDEN = Path(__file__).parents[2] / "evals" / "datasets" / "agent_questions.jsonl"
NO_SCOPE = {"industry": None, "start": None, "end": None}


def test_golden_questions_name_real_tools() -> None:
    names = {t.name for t in TOOLS} - {"final_answer"}
    questions = load_questions(GOLDEN)
    assert len(questions) >= 15
    assert all(set(q.expected_tools) <= names and q.expected_tools for q in questions)


def scripted(messages: Sequence[Message], tools: Sequence[ToolSpec]) -> ToolTurn:
    """Routes "how many" to metrics and everything else to search; answers after one tool.
    Questions mentioning "fail" make the scripted model cite a fabricated quote."""
    question = messages[1].content.lower()
    done = [m for m in messages if m.role == "tool"]
    if not done:
        if "how many" in question:
            return tool_turn(("query_metrics", {"question": question, **NO_SCOPE}))
        return tool_turn(("search_conversations", {"query": "bill price", **NO_SCOPE}))
    quote = "made up quote here" if "fail" in question else "my bill went up"
    citations = [] if "how many" in question else [{"source": 1, "quote": quote}]
    metrics = [1] if "how many" in question else []
    return tool_turn(
        ("final_answer", {"answer": "Answer.", "citations": citations, "metrics": metrics})
    )


def judge(schema: type[BaseModel], messages: Sequence[Message]) -> BaseModel:
    if "sql" in schema.model_fields:  # text-to-SQL inside the agent
        return schema.model_validate(
            {"sql": "SELECT count(*) FROM conversations", "explanation": "n"}
        )
    claims = [
        {"claim": "a", "verdict": "supported", "reason": "in S1"},
        {"claim": "b", "verdict": "unsupported", "reason": "not shown"},
    ]
    return schema.model_validate({"claims": claims})


async def test_agent_eval_scores_routing_citations_and_faithfulness(
    db: async_sessionmaker[AsyncSession], migrated_db: str, tmp_path: Path
) -> None:
    await ingest_payloads(
        db, [conversation("p", texts=("Hi", "My bill went up this month", "Sorry"))]
    )
    engine = create_async_engine(migrated_db)
    llm = FakeLLMProvider(structured=judge, tool_script=scripted)
    agent = Agent(
        llm,
        sessionmaker=db,
        retriever=Retriever(FakeEmbeddingProvider(), strategy="hybrid"),
        text_to_sql=TextToSQL(llm, engine, tenant_id=DEFAULT_TENANT_ID),
        max_final_attempts=1,
    )
    questions = [
        AgentQuestion("metric", "How many conversations?", ["query_metrics"]),
        AgentQuestion("why", "Why are bills a problem?", ["search_conversations"]),
        AgentQuestion("both", "Why did bills spike?", ["query_metrics", "search_conversations"]),
        AgentQuestion("bad_cite", "Why do things fail?", ["search_conversations"]),
    ]
    try:
        result = await evaluate(
            questions,
            agent=agent,
            judge_llm=llm,
            model="scripted",
            judge_model="fake-judge",
            golden="test",
        )
    finally:
        await engine.dispose()

    assert result.routing_accuracy == 0.75  # "both" only searched
    assert result.over_calling == 0.0
    assert result.citation_validity == pytest.approx(2 / 3)  # 2 kept, 1 dropped
    assert result.faithfulness == 0.5
    assert result.errors == 0
    report = to_markdown(result)
    assert "| 0.750 | 0.000 | 0.667 | 0.500 |" in report
    assert "| bad_cite | search_conversations | yes | 0 (1) | 1/2 |" in report


async def test_agent_errors_are_counted_not_raised(
    db: async_sessionmaker[AsyncSession], migrated_db: str
) -> None:
    engine = create_async_engine(migrated_db)
    llm = FakeLLMProvider()  # no script: every run raises
    agent = Agent(
        llm,
        sessionmaker=db,
        retriever=Retriever(FakeEmbeddingProvider()),
        text_to_sql=TextToSQL(llm, engine, tenant_id=DEFAULT_TENANT_ID),
    )
    try:
        result = await evaluate(
            [AgentQuestion("q", "Why?", ["search_conversations"])],
            agent=agent,
            judge_llm=None,
            model="broken",
            judge_model=None,
            golden="test",
        )
    finally:
        await engine.dispose()
    assert (result.errors, result.routing_accuracy, result.faithfulness) == (1, 0.0, None)
    assert "LookupError" in (result.questions[0].error or "")
    json.dumps(result.questions[0].__dict__)  # report rows stay serializable
