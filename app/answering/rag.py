"""Retrieval-augmented answering with validated citations (no agent yet).

Flow: retrieve (strategy from `Retriever`) -> numbered sources -> structured
answer with citations -> drop citations that don't check out. If citations
were dropped, the model gets one retry with the problems as feedback.
"""

import time
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.answering.citations import ValidCitation, check_citations
from app.llm.base import LLMProvider, Message, Usage
from app.retrieval.retriever import Retriever
from app.retrieval.types import RetrievedChunk, SearchFilters

PROMPT_PATH = Path(__file__).parent / "prompts" / "answer.md"
NO_EVIDENCE = "I couldn't find conversations relevant to this question with the given filters."


class _Citation(BaseModel):
    source: int = Field(description="Source number, e.g. 3 for [S3]")
    quote: str = Field(description="Short span copied verbatim from that source")


class _Answer(BaseModel):
    answer: str
    citations: list[_Citation]


@dataclass
class RagAnswer:
    answer: str
    citations: list[ValidCitation]
    sources: list[RetrievedChunk]
    dropped_citations: list[str] = field(default_factory=list)
    attempts: int = 0
    usage: Usage = field(default_factory=Usage)
    latency_ms: int = 0
    model: str | None = None


def format_sources(chunks: list[RetrievedChunk]) -> str:
    return "\n\n".join(
        f"[S{i}] conversation {c.external_id}, {c.started_at:%Y-%m-%d}\n{c.text}"
        for i, c in enumerate(chunks, start=1)
    )


class RagAnswerer:
    def __init__(
        self,
        llm: LLMProvider,
        retriever: Retriever,
        *,
        k: int = 8,
        max_attempts: int = 2,
    ) -> None:
        self._llm = llm
        self._retriever = retriever
        self._k = k
        self._max_attempts = max_attempts
        self._system = PROMPT_PATH.read_text()

    async def answer(
        self, session: AsyncSession, question: str, filters: SearchFilters
    ) -> RagAnswer:
        started = time.perf_counter()
        sources = await self._retriever.search(session, question, k=self._k, filters=filters)
        if not sources:
            return RagAnswer(answer=NO_EVIDENCE, citations=[], sources=[])

        messages = [
            Message(role="system", content=self._system),
            Message(
                role="user",
                content=f"Sources:\n\n{format_sources(sources)}\n\nQuestion: {question}",
            ),
        ]
        result = RagAnswer(answer="", citations=[], sources=sources)
        for attempt in range(1, self._max_attempts + 1):
            response = await self._llm.complete_structured(messages, _Answer)
            result.usage = result.usage + response.usage
            result.model = response.model
            result.attempts = attempt
            check = check_citations(
                [(c.source, c.quote) for c in response.value.citations], sources
            )
            result.answer = response.value.answer
            result.citations = check.valid
            result.dropped_citations = check.problems
            if not check.problems:
                break
            messages = [
                *messages,
                Message(role="assistant", content=response.value.model_dump_json()),
                Message(
                    role="user",
                    content="Some citations could not be verified: "
                    + "; ".join(check.problems)
                    + ". Answer again, quoting text exactly as it appears in the sources.",
                ),
            ]
        result.latency_ms = round((time.perf_counter() - started) * 1000)
        return result
