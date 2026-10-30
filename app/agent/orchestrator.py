"""Tool-calling loop (no framework, see ADR 0002).

Each step, the model must call at least one tool. `search_conversations` and
`query_metrics` add numbered evidence; `final_answer` ends the run. Its
citations are checked against the sources this run actually retrieved, and
its metric references against the metrics it computed. If anything fails, the
model gets the problems back as the tool result and another try; on the last
try, failing citations are dropped and counted. When the step budget runs out,
the model is offered only `final_answer`.

Progress is reported through an optional async callback, which `/v1/ask`
uses to stream server-sent events.
"""

import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent.tools import (
    FINAL,
    METRICS,
    SEARCH,
    TOOLS,
    Evidence,
    FinalAnswerArgs,
    MetricResult,
    MetricsArgs,
    SearchArgs,
    ToolArgumentError,
    run_metrics,
    run_search,
)
from app.analytics.text_to_sql import TextToSQL
from app.answering.citations import ValidCitation, check_citations
from app.db.models import Conversation
from app.llm.base import LLMProvider, Message, ToolCall, ToolSpec, Usage
from app.retrieval.retriever import Retriever
from app.retrieval.types import RetrievedChunk

PROMPT_PATH = Path(__file__).parent / "prompts" / "system.md"

EventCallback = Callable[[dict[str, Any]], Awaitable[None]]


@dataclass
class TraceStep:
    step: int
    tool: str
    arguments: dict[str, Any]
    summary: str
    duration_ms: int
    error: bool = False


@dataclass
class AgentResult:
    answer: str
    citations: list[ValidCitation]
    metrics: list[MetricResult]
    sources: list[RetrievedChunk]  # every excerpt retrieved, in [S#] order
    tools_used: list[str]
    trace: list[TraceStep]
    steps: int
    usage: Usage
    latency_ms: int
    model: str | None
    dropped_citations: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class _Verdict:
    accepted: bool
    feedback: str = ""
    answer: str = ""
    citations: list[ValidCitation] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)


async def data_overview(sessionmaker: async_sessionmaker[AsyncSession]) -> str:
    async with sessionmaker() as session:
        rows = (
            await session.execute(
                select(
                    Conversation.industry,
                    func.count(),
                    func.min(Conversation.started_at),
                    func.max(Conversation.started_at),
                ).group_by(Conversation.industry)
            )
        ).all()
    if not rows:
        return "No conversations have been ingested yet."
    return "\n".join(
        f"- {industry}: {n} conversations from {first:%Y-%m-%d} to {last:%Y-%m-%d}"
        for industry, n, first, last in rows
    )


def judge_final(call: ToolCall, evidence: Evidence, *, last_try: bool) -> _Verdict:
    try:
        final = FinalAnswerArgs.model_validate_json(call.arguments)
    except ValidationError as exc:
        return _Verdict(accepted=False, feedback=f"Invalid final_answer arguments: {exc}")
    check = check_citations([(c.source, c.quote) for c in final.citations], evidence.sources)
    known = {m.number for m in evidence.metrics}
    problems = check.problems + [
        f"metric M{n} does not exist" for n in final.metrics if n not in known
    ]
    if problems and not last_try:
        return _Verdict(
            accepted=False,
            feedback="Rejected, fix and call final_answer again: " + "; ".join(problems),
        )
    return _Verdict(accepted=True, answer=final.answer, citations=check.valid, dropped=problems)


def _arguments(call: ToolCall) -> dict[str, Any]:
    try:
        value = json.loads(call.arguments)
    except ValueError:
        return {"raw": call.arguments}
    return value if isinstance(value, dict) else {"value": value}


def _summary(text: str, limit: int = 160) -> str:
    first = text.strip().splitlines()[0] if text.strip() else ""
    return first if len(first) <= limit else first[: limit - 1] + "…"


def _ms(t0: float) -> int:
    return round((time.perf_counter() - t0) * 1000)


class Agent:
    def __init__(
        self,
        llm: LLMProvider,
        *,
        sessionmaker: async_sessionmaker[AsyncSession],
        retriever: Retriever,
        text_to_sql: TextToSQL,
        k: int = 8,
        max_steps: int = 6,
        max_final_attempts: int = 2,
    ) -> None:
        self._llm = llm
        self._sessionmaker = sessionmaker
        self._retriever = retriever
        self._text_to_sql = text_to_sql
        self._k = k
        self._max_steps = max_steps
        self._max_final_attempts = max_final_attempts
        self._template = PROMPT_PATH.read_text()

    async def system_prompt(self) -> str:
        return self._template.format(
            today=datetime.now(UTC).date().isoformat(),
            data=await data_overview(self._sessionmaker),
        )

    async def run(self, question: str, on_event: EventCallback | None = None) -> AgentResult:
        started = time.perf_counter()

        async def emit(event: dict[str, Any]) -> None:
            if on_event is not None:
                await on_event(event)

        messages = [
            Message(role="system", content=await self.system_prompt()),
            Message(role="user", content=question),
        ]
        evidence = Evidence()
        trace: list[TraceStep] = []
        usage = Usage()
        model: str | None = None
        final_attempts = 0

        def result(step: int, verdict: _Verdict) -> AgentResult:
            return AgentResult(
                answer=verdict.answer,
                citations=verdict.citations,
                metrics=evidence.metrics,
                sources=evidence.sources,
                tools_used=list(dict.fromkeys(t.tool for t in trace if t.tool != FINAL.name)),
                trace=trace,
                steps=step,
                usage=usage,
                latency_ms=_ms(started),
                model=model,
                dropped_citations=verdict.dropped,
            )

        step = 0
        while True:
            step += 1
            # Past the budget, only final_answer is offered, so every run ends with one.
            tools: tuple[ToolSpec, ...] = TOOLS if step <= self._max_steps else (FINAL,)
            turn = await self._llm.complete_with_tools(messages, tools, tool_choice="required")
            usage = usage + turn.usage
            model = turn.model
            if not turn.tool_calls:
                # Text despite tool_choice=required: accept it as an uncited answer.
                return result(step, _Verdict(accepted=True, answer=turn.text or ""))
            messages.append(
                Message(role="assistant", content=turn.text or "", tool_calls=turn.tool_calls)
            )

            for call in turn.tool_calls:
                arguments = _arguments(call)
                await emit({"type": "tool_start", "tool": call.name, "arguments": arguments})
                t0 = time.perf_counter()
                if call.name == FINAL.name:
                    final_attempts += 1
                    verdict = judge_final(
                        call, evidence, last_try=final_attempts >= self._max_final_attempts
                    )
                    if verdict.accepted:
                        trace.append(TraceStep(step, call.name, {}, "answered", _ms(t0)))
                        await emit({"type": "tool_end", "tool": call.name, "error": False})
                        return result(step, verdict)
                    content, error = verdict.feedback, True
                else:
                    content, error = await self._execute(call, evidence)
                trace.append(
                    TraceStep(step, call.name, arguments, _summary(content), _ms(t0), error)
                )
                await emit(
                    {
                        "type": "tool_end",
                        "tool": call.name,
                        "summary": _summary(content),
                        "error": error,
                    }
                )
                messages.append(Message(role="tool", tool_call_id=call.id, content=content))

    async def _execute(self, call: ToolCall, evidence: Evidence) -> tuple[str, bool]:
        try:
            if call.name == SEARCH.name:
                text = await run_search(
                    SearchArgs.model_validate_json(call.arguments),
                    sessionmaker=self._sessionmaker,
                    retriever=self._retriever,
                    evidence=evidence,
                    k=self._k,
                )
                return text, False
            if call.name == METRICS.name:
                text, answer = await run_metrics(
                    MetricsArgs.model_validate_json(call.arguments),
                    text_to_sql=self._text_to_sql,
                    evidence=evidence,
                )
                return text, answer is None
        except (ValidationError, ToolArgumentError) as exc:
            return f"Invalid arguments for {call.name}: {exc}", True
        return f"Unknown tool {call.name!r}", True
