"""Agent tools: argument models (sent to the model as strict JSON schemas) and
their implementations. Tool results are plain text the model reads; sources and
metric results are numbered across the whole run ([S1], [M1]) so the final
answer can cite them."""

from dataclasses import dataclass, field
from datetime import UTC, date, datetime

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.analytics.text_to_sql import MetricsAnswer, MetricsFilters, TextToSQL, TextToSQLError
from app.llm.base import ToolSpec
from app.retrieval.retriever import Retriever
from app.retrieval.types import RetrievedChunk, SearchFilters

MAX_EXCERPT_CHARS = 700
MAX_METRIC_ROWS_SHOWN = 30


class _Scope(BaseModel):
    industry: str | None = Field(description="telecom | banking | ecommerce, or null for all")
    start: str | None = Field(description="Inclusive start date YYYY-MM-DD, or null")
    end: str | None = Field(description="Exclusive end date YYYY-MM-DD, or null")


class SearchArgs(_Scope):
    query: str = Field(description="What to look for, in the customers' words")


class MetricsArgs(_Scope):
    question: str = Field(description="The quantitative question to answer with SQL")


class CitationArg(BaseModel):
    source: int = Field(description="Source number, e.g. 3 for [S3]")
    quote: str = Field(description="Short span copied verbatim from that source")


class FinalAnswerArgs(BaseModel):
    answer: str = Field(description="The answer for the analyst")
    citations: list[CitationArg] = Field(description="Quotes backing claims about conversations")
    metrics: list[int] = Field(description="Metric result numbers used, e.g. [1] for [M1]")


SEARCH = ToolSpec(
    name="search_conversations",
    description="Find excerpts of customer conversations relevant to a query, within filters.",
    args_model=SearchArgs,
)
METRICS = ToolSpec(
    name="query_metrics",
    description="Answer a quantitative question about conversations and customers with SQL.",
    args_model=MetricsArgs,
)
FINAL = ToolSpec(
    name="final_answer",
    description="Give the final answer with citations. Call exactly once, at the end.",
    args_model=FinalAnswerArgs,
)
TOOLS = (SEARCH, METRICS, FINAL)


class ToolArgumentError(ValueError):
    pass


def _parse_date(value: str | None, field_name: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.combine(date.fromisoformat(value[:10]), datetime.min.time(), tzinfo=UTC)
    except ValueError as exc:
        raise ToolArgumentError(f"{field_name} must be YYYY-MM-DD, got {value!r}") from exc


def scope_filters(args: _Scope) -> tuple[str | None, datetime | None, datetime | None]:
    start, end = _parse_date(args.start, "start"), _parse_date(args.end, "end")
    if start and end and end <= start:
        raise ToolArgumentError("end must be after start")
    return args.industry, start, end


@dataclass
class MetricResult:
    number: int
    question: str
    answer: MetricsAnswer


@dataclass
class Evidence:
    """Everything the tools returned during one run, numbered for citation."""

    sources: list[RetrievedChunk] = field(default_factory=list)
    metrics: list[MetricResult] = field(default_factory=list)

    def add_sources(self, chunks: list[RetrievedChunk]) -> list[tuple[int, RetrievedChunk]]:
        known = {c.chunk_id: i for i, c in enumerate(self.sources, start=1)}
        numbered = []
        for chunk in chunks:
            if chunk.chunk_id not in known:
                self.sources.append(chunk)
                known[chunk.chunk_id] = len(self.sources)
            numbered.append((known[chunk.chunk_id], chunk))
        return numbered


async def run_search(
    args: SearchArgs,
    *,
    sessionmaker: async_sessionmaker[AsyncSession],
    retriever: Retriever,
    evidence: Evidence,
    k: int,
) -> str:
    industry, start, end = scope_filters(args)
    async with sessionmaker() as session:
        chunks = await retriever.search(
            session, args.query, k=k, filters=SearchFilters(industry=industry, start=start, end=end)
        )
    if not chunks:
        return "No matching conversations for these filters."
    return "\n\n".join(
        f"[S{n}] conversation {c.external_id}, {c.started_at:%Y-%m-%d}\n"
        + c.text[:MAX_EXCERPT_CHARS]
        for n, c in evidence.add_sources(chunks)
    )


async def run_metrics(
    args: MetricsArgs, *, text_to_sql: TextToSQL, evidence: Evidence
) -> tuple[str, MetricsAnswer | None]:
    industry, start, end = scope_filters(args)
    try:
        answer = await text_to_sql.answer(
            args.question, MetricsFilters(industry=industry, start=start, end=end)
        )
    except TextToSQLError as exc:
        return f"Could not compute this metric: {'; '.join(exc.errors)}", None
    number = len(evidence.metrics) + 1
    evidence.metrics.append(MetricResult(number, args.question, answer))
    result = answer.result
    header = " | ".join(result.columns)
    rows = "\n".join(" | ".join(str(v) for v in row) for row in result.rows[:MAX_METRIC_ROWS_SHOWN])
    more = len(result.rows) - MAX_METRIC_ROWS_SHOWN
    tail = f"\n... {more} more rows" if more > 0 else ""
    text = f"[M{number}] {answer.explanation}\nSQL: {answer.sql}\n{header}\n"
    return text + (rows or "(no rows)") + tail, answer
