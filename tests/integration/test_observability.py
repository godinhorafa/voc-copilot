import json
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.llm.fake import FakeLLMProvider, tool_turn
from app.observability.tracing import setup_tracing
from app.workers.tasks import process_ingestion_job
from tests.factories import batch, conversation
from tests.helpers import ingest_payloads

pytestmark = pytest.mark.integration
NO_SCOPE = {"industry": None, "start": None, "end": None}


@pytest.fixture
def spans() -> Iterator[InMemorySpanExporter]:
    exporter = InMemorySpanExporter()
    processor = SimpleSpanProcessor(exporter)
    setup_tracing("test", otlp_endpoint=None).add_span_processor(processor)
    yield exporter
    processor.shutdown()


async def test_ask_reports_usage_cost_and_trace(
    app: FastAPI,
    client: AsyncClient,
    db: async_sessionmaker[AsyncSession],
    spans: InMemorySpanExporter,
) -> None:
    await ingest_payloads(db, [conversation("a")])
    spans.clear()
    app.state.llm = FakeLLMProvider(
        [json.dumps({"sql": "SELECT count(*) AS n FROM conversations", "explanation": "n"})],
        tool_turns=[
            tool_turn(
                ("query_metrics", {"question": "How many?", **NO_SCOPE}),
                ("search_conversations", {"query": "bill", **NO_SCOPE}),
            ),
            tool_turn(("final_answer", {"answer": "One.", "citations": [], "metrics": [1]})),
        ],
    )

    body = (await client.post("/v1/ask", json={"question": "How many and why?"})).json()

    usage = body["usage"]
    by_kind = {(m["provider"], m["kind"]): m for m in usage["by_model"]}
    # 2 agent turns + 1 text-to-SQL call; 1 embedding call for the search.
    assert by_kind[("fake", "chat")]["calls"] == 3
    assert by_kind[("fake", "embedding")]["calls"] == 1
    assert usage["input_tokens"] == sum(m["input_tokens"] for m in usage["by_model"])
    assert usage["cost_usd"] == 0.0  # the fake provider is free
    assert len(body["trace_id"]) == 32

    finished = spans.get_finished_spans()
    names = [s.name for s in finished]
    assert "agent.run" in names
    assert "agent.tool query_metrics" in names
    assert "agent.tool search_conversations" in names
    assert names.count("chat fake-llm") == 3
    # Everything belongs to the request's trace.
    assert {format(s.context.trace_id, "032x") for s in finished} == {body["trace_id"]}


async def test_metrics_endpoint_reports_usage(
    app: FastAPI, client: AsyncClient, spans: InMemorySpanExporter
) -> None:
    app.state.llm = FakeLLMProvider(
        [json.dumps({"sql": "SELECT count(*) AS n FROM conversations", "explanation": "n"})]
    )
    body = (await client.post("/v1/metrics", json={"question": "How many?"})).json()
    assert body["usage"]["by_model"][0]["calls"] == 1
    assert body["usage"]["cost_usd"] == 0.0
    assert len(body["trace_id"]) == 32


async def test_health_is_not_traced(client: AsyncClient, spans: InMemorySpanExporter) -> None:
    spans.clear()
    await client.get("/health")
    assert spans.get_finished_spans() == ()


async def test_ingestion_job_span(
    client: AsyncClient, worker_ctx: dict[str, Any], spans: InMemorySpanExporter
) -> None:
    job_id = (await client.post("/v1/conversations", json=batch(conversation("a")))).json()[
        "job_id"
    ]
    spans.clear()
    assert await process_ingestion_job(worker_ctx, job_id) == "succeeded"
    job = next(s for s in spans.get_finished_spans() if s.name == "ingestion.job")
    assert job.attributes is not None
    assert job.attributes["job.id"] == job_id
    children = [
        s
        for s in spans.get_finished_spans()
        if s.parent and s.parent.span_id == job.context.span_id
    ]
    assert {s.name for s in children} >= {"embeddings fake-embedding", "chat fake-llm"}
