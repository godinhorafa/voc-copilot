"""POST /v1/ask: the agent (default) or plain RAG, optionally streamed as SSE.

Streaming sends progress, not tokens: `tool_start` / `tool_end` events while
the agent works, then one `answer` event with the full response (the answer
arrives through the final_answer tool, so there is no text to stream earlier),
or an `error` event.

Every model call made while answering (agent turns, text-to-SQL, reranking,
embeddings) is metered, so `usage` covers the whole request.
"""

import asyncio
import json
import time
from collections.abc import AsyncIterator
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse

from app.agent.orchestrator import Agent, AgentResult
from app.analytics.text_to_sql import TextToSQL
from app.answering.rag import RagAnswerer
from app.api.auth import PrincipalDep, SessionDep, SessionmakerDep, require_role
from app.api.deps import AnalyticsEngineDep, EmbeddingsDep, LLMDep, PricesDep, SettingsDep
from app.core.rate_limit import rate_limit
from app.db.models import Role
from app.observability.tracing import current_trace_id
from app.observability.usage import PriceTable, UsageMeter, metered
from app.retrieval.rerank import LLMReranker
from app.retrieval.retriever import Retriever
from app.retrieval.types import SearchFilters
from app.schemas.ask import AskRequest, AskResponse, Citation, MetricOut, TraceStepOut
from app.schemas.usage import usage_out

router = APIRouter(
    prefix="/ask",
    tags=["ask"],
    dependencies=[
        Depends(require_role(Role.ANALYST)),
        Depends(rate_limit("ask", lambda s: s.rate_limit_ask)),
    ],
)
logger = structlog.get_logger(__name__)


def _scoped_question(body: AskRequest) -> str:
    """Filters set in the request are passed to the agent as part of the question."""
    scope = []
    if body.industry:
        scope.append(f"industry = {body.industry}")
    if body.start:
        scope.append(f"from {body.start.date().isoformat()}")
    if body.end:
        scope.append(f"until {body.end.date().isoformat()} (exclusive)")
    if not scope:
        return body.question
    note = f"Scope set by the user: {', '.join(scope)}. Apply it to every tool call."
    return f"{body.question}\n\n({note})"


def _agent_response(
    result: AgentResult, retriever: Retriever, meter: UsageMeter, prices: PriceTable
) -> AskResponse:
    return AskResponse(
        answer=result.answer,
        citations=[
            Citation(source=c.source, conversation_id=c.external_id, excerpt=c.quote)
            for c in result.citations
        ],
        metrics=[
            MetricOut(
                id=f"M{m.number}",
                question=m.question,
                sql=m.answer.sql,
                columns=m.answer.result.columns,
                rows=m.answer.result.rows,
                truncated=m.answer.result.truncated,
            )
            for m in result.metrics
        ],
        tools_used=result.tools_used,
        mode="agent",
        steps=result.steps,
        trace=[TraceStepOut(**vars(t)) for t in result.trace],
        retrieval=retriever.name,
        sources_retrieved=len(result.sources),
        dropped_citations=len(result.dropped_citations),
        model=result.model,
        usage=usage_out(meter, prices, result.latency_ms),
        trace_id=current_trace_id(),
    )


@router.post("", response_model=AskResponse)
async def ask(
    body: AskRequest,
    session: SessionDep,
    sessionmaker: SessionmakerDep,
    llm: LLMDep,
    embeddings: EmbeddingsDep,
    analytics: AnalyticsEngineDep,
    settings: SettingsDep,
    prices: PricesDep,
    principal: PrincipalDep,
    stream: bool = Query(default=False, description="Stream progress as server-sent events"),
) -> Any:
    """Answer a question from customer conversations and metrics, with citations."""
    retriever = Retriever(
        embeddings,
        strategy=settings.retrieval_strategy,
        reranker=LLMReranker(llm) if settings.retrieval_rerank else None,
        candidates=settings.retrieval_candidates,
    )

    if body.mode == "rag":
        if stream:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, "stream is only supported in agent mode"
            )
        filters = SearchFilters(industry=body.industry, start=body.start, end=body.end)
        started = time.perf_counter()
        with metered() as meter:
            rag = await RagAnswerer(llm, retriever, k=body.k).answer(
                session, body.question, filters
            )
        return AskResponse(
            answer=rag.answer,
            citations=[
                Citation(source=c.source, conversation_id=c.external_id, excerpt=c.quote)
                for c in rag.citations
            ],
            tools_used=["search_conversations"],
            mode="rag",
            steps=rag.attempts,
            retrieval=retriever.name,
            sources_retrieved=len(rag.sources),
            dropped_citations=len(rag.dropped_citations),
            model=rag.model,
            usage=usage_out(meter, prices, round((time.perf_counter() - started) * 1000)),
            trace_id=current_trace_id(),
        )

    engine, set_role = analytics
    agent = Agent(
        llm,
        sessionmaker=sessionmaker,
        retriever=retriever,
        text_to_sql=TextToSQL(
            llm,
            engine,
            max_rows=settings.sql_max_rows,
            timeout_ms=settings.sql_timeout_ms,
            set_role=set_role,
            tenant_id=principal.tenant_id,
        ),
        k=body.k,
        max_steps=settings.agent_max_steps,
    )
    question = _scoped_question(body)
    if not stream:
        with metered() as meter:
            result = await agent.run(question)
        return _agent_response(result, retriever, meter, prices)
    return StreamingResponse(
        _stream(agent, question, retriever, prices),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _sse(event: dict[str, Any]) -> str:
    return f"event: {event['type']}\ndata: {json.dumps(event, default=str)}\n\n"


async def _stream(
    agent: Agent, question: str, retriever: Retriever, prices: PriceTable
) -> AsyncIterator[str]:
    queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()

    async def run() -> None:
        try:
            with metered() as meter:
                result = await agent.run(question, on_event=queue.put)
            response = _agent_response(result, retriever, meter, prices)
            await queue.put({"type": "answer", "data": response.model_dump(mode="json")})
        except Exception:
            logger.exception("agent_stream_failed")
            await queue.put({"type": "error", "message": "The agent failed; see server logs."})
        finally:
            await queue.put(None)

    task = asyncio.create_task(run())
    try:
        while (event := await queue.get()) is not None:
            yield _sse(event)
    finally:
        if not task.done():  # client went away
            task.cancel()
