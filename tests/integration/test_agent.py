import json
from collections.abc import Sequence
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
)

from app.agent.orchestrator import Agent
from app.analytics.text_to_sql import TextToSQL
from app.db.models import DEFAULT_TENANT_ID
from app.llm.base import ToolTurn
from app.llm.fake import FakeEmbeddingProvider, FakeLLMProvider, tool_turn
from app.retrieval.retriever import Retriever
from tests.factories import conversation
from tests.helpers import ingest_payloads

pytestmark = pytest.mark.integration

PRICE = conversation(
    "price",
    texts=("Hi", "My bill went up and nobody told me about the new price", "Sorry about that"),
    started_at="2026-09-03T10:00:00",
)
ROUTER = conversation(
    "router",
    texts=("Hi", "The router shows ERR-4G-503 and there is no signal", "Engineers are on it"),
    started_at="2026-10-15T10:00:00",
)
SEPTEMBER = {"industry": "telecom", "start": "2026-09-01", "end": "2026-10-01"}
COUNT_SQL = json.dumps({"sql": "SELECT count(*) AS n FROM conversations", "explanation": "count"})


def final(
    answer: str, citations: Sequence[tuple[int, str]] = (), metrics: Sequence[int] = ()
) -> ToolTurn:
    return tool_turn(
        (
            "final_answer",
            {
                "answer": answer,
                "citations": [{"source": s, "quote": q} for s, q in citations],
                "metrics": list(metrics),
            },
        )
    )


def make_agent(
    llm: FakeLLMProvider, db: async_sessionmaker[AsyncSession], engine: AsyncEngine, **kw: Any
) -> Agent:
    return Agent(
        llm,
        sessionmaker=db,
        retriever=Retriever(FakeEmbeddingProvider(), strategy="hybrid"),
        text_to_sql=TextToSQL(llm, engine, tenant_id=DEFAULT_TENANT_ID),
        **kw,
    )


async def test_metrics_then_search_then_cited_answer(
    db: async_sessionmaker[AsyncSession], engine: AsyncEngine
) -> None:
    await ingest_payloads(db, [PRICE, ROUTER])
    llm = FakeLLMProvider(
        [COUNT_SQL],
        tool_turns=[
            tool_turn(("query_metrics", {"question": "How many conversations?", **SEPTEMBER})),
            tool_turn(("search_conversations", {"query": "bill went up price", **SEPTEMBER})),
            final(
                "Bills rose without notice [S1] [M1].",
                [(1, "nobody told me about the new price")],
                [1],
            ),
        ],
    )
    events: list[dict[str, Any]] = []

    async def collect(event: dict[str, Any]) -> None:
        events.append(event)

    result = await make_agent(llm, db, engine).run("Why are customers upset?", on_event=collect)

    assert result.tools_used == ["query_metrics", "search_conversations"]
    assert [c.external_id for c in result.citations] == ["price"]
    assert result.metrics[0].answer.result.rows == [[2]]
    # The September filter excluded the router conversation.
    assert [c.external_id for c in result.sources] == ["price"]
    assert (result.steps, result.dropped_citations) == (3, [])
    assert [t.tool for t in result.trace] == [
        "query_metrics",
        "search_conversations",
        "final_answer",
    ]
    assert [e["type"] for e in events] == ["tool_start", "tool_end"] * 3
    system = llm.calls[0][0].content
    assert "telecom: 2 conversations from 2026-09-03 to 2026-10-15" in system
    assert set(llm.tool_choices) == {"required"}
    # The search result handed to the model is numbered for citation.
    tool_results = [m.content for m in llm.calls[-1] if m.role == "tool"]
    assert tool_results[0].startswith("[M1] count\nSQL: SELECT COUNT(*)")
    assert tool_results[1].startswith("[S1] conversation price, 2026-09-03")


async def test_bad_citation_is_sent_back_then_dropped(
    db: async_sessionmaker[AsyncSession], engine: AsyncEngine
) -> None:
    await ingest_payloads(db, [PRICE])
    fabricated = final("Everyone is furious.", [(1, "this is outrageous")], [7])
    llm = FakeLLMProvider(
        tool_turns=[
            tool_turn(
                (
                    "search_conversations",
                    {"query": "price", "industry": None, "start": None, "end": None},
                )
            ),
            fabricated,
            fabricated,
        ]
    )
    result = await make_agent(llm, db, engine).run("Why?")

    feedback = llm.calls[2][-1].content
    assert feedback.startswith("Rejected, fix and call final_answer again")
    assert "metric M7 does not exist" in feedback
    assert result.answer == "Everyone is furious."
    assert result.citations == []
    assert len(result.dropped_citations) == 2


async def test_invalid_arguments_become_tool_errors(
    db: async_sessionmaker[AsyncSession], engine: AsyncEngine
) -> None:
    llm = FakeLLMProvider(
        tool_turns=[
            tool_turn(
                (
                    "search_conversations",
                    {"query": "x", "industry": None, "start": "September", "end": None},
                )
            ),
            tool_turn(("delete_everything", {})),
            final("No evidence."),
        ]
    )
    result = await make_agent(llm, db, engine).run("Why?")
    assert "start must be YYYY-MM-DD" in llm.calls[1][-1].content
    assert "Unknown tool 'delete_everything'" in llm.calls[2][-1].content
    assert [t.error for t in result.trace] == [True, True, False]


async def test_step_budget_forces_final_answer(
    db: async_sessionmaker[AsyncSession], engine: AsyncEngine
) -> None:
    search = tool_turn(
        ("search_conversations", {"query": "x", "industry": None, "start": None, "end": None})
    )
    llm = FakeLLMProvider(tool_turns=[search, search, final("Done.")])
    result = await make_agent(llm, db, engine, max_steps=2).run("Why?")
    assert llm.tools_offered[-1] == ["final_answer"]
    assert len(llm.tools_offered[0]) == 3
    assert (result.answer, result.steps) == ("Done.", 3)


async def test_text_reply_is_accepted_uncited(
    db: async_sessionmaker[AsyncSession], engine: AsyncEngine
) -> None:
    llm = FakeLLMProvider(tool_turns=[tool_turn(text="I can only greet you.")])
    result = await make_agent(llm, db, engine).run("Hi")
    assert (result.answer, result.citations, result.tools_used) == ("I can only greet you.", [], [])


async def test_permissive_fake_finishes_offline(
    db: async_sessionmaker[AsyncSession], engine: AsyncEngine
) -> None:
    result = await make_agent(FakeLLMProvider.permissive(), db, engine).run("Why?")
    assert result.steps == 1
    assert result.trace[0].tool == "final_answer"


async def test_ask_endpoint_agent_mode_with_scope(
    app: FastAPI, client: AsyncClient, db: async_sessionmaker[AsyncSession]
) -> None:
    await ingest_payloads(db, [PRICE])
    llm = FakeLLMProvider(
        [COUNT_SQL],
        tool_turns=[
            tool_turn(("query_metrics", {"question": "How many?", **SEPTEMBER})),
            final("One conversation [M1].", metrics=[1]),
        ],
    )
    app.state.llm = llm
    response = await client.post(
        "/v1/ask",
        json={
            "question": "How many conversations?",
            "industry": "telecom",
            "start": "2026-09-01T00:00:00Z",
        },
    )
    body = response.json()
    assert response.status_code == 200
    assert (body["mode"], body["steps"], body["tools_used"]) == ("agent", 2, ["query_metrics"])
    assert body["metrics"][0]["id"] == "M1"
    assert body["metrics"][0]["rows"] == [[1]]
    assert body["metrics"][0]["sql"].startswith("SELECT COUNT(*) AS n FROM analytics.conversations")
    assert [t["tool"] for t in body["trace"]] == ["query_metrics", "final_answer"]
    user = llm.calls[0][1].content
    assert "Scope set by the user: industry = telecom, from 2026-09-01" in user


def _events(raw: str) -> list[tuple[str, dict[str, Any]]]:
    events = []
    for block in raw.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines())
        events.append((lines["event"], json.loads(lines["data"])))
    return events


async def test_ask_streams_progress_then_answer(
    app: FastAPI, client: AsyncClient, db: async_sessionmaker[AsyncSession]
) -> None:
    await ingest_payloads(db, [PRICE])
    app.state.llm = FakeLLMProvider(
        tool_turns=[
            tool_turn(
                (
                    "search_conversations",
                    {"query": "price", "industry": None, "start": None, "end": None},
                )
            ),
            final("Bills went up [S1].", [(1, "my bill went up")]),
        ]
    )
    response = await client.post("/v1/ask?stream=true", json={"question": "Why?"})
    assert response.headers["content-type"].startswith("text/event-stream")
    events = _events(response.text)
    assert [name for name, _ in events] == [
        "tool_start",
        "tool_end",
        "tool_start",
        "tool_end",
        "answer",
    ]
    assert events[0][1]["tool"] == "search_conversations"
    answer = events[-1][1]["data"]
    assert answer["citations"] == [
        {"source": 1, "conversation_id": "price", "excerpt": "my bill went up"}
    ]


async def test_stream_error_event(app: FastAPI, client: AsyncClient) -> None:
    app.state.llm = FakeLLMProvider()  # no scripted turns: the agent raises
    response = await client.post("/v1/ask?stream=true", json={"question": "Why?"})
    assert _events(response.text) == [
        ("error", {"type": "error", "message": "The agent failed; see server logs."})
    ]


async def test_rag_mode_cannot_stream(client: AsyncClient) -> None:
    response = await client.post("/v1/ask?stream=true", json={"question": "Why?", "mode": "rag"})
    assert response.status_code == 400
