"""LLM reranking of retrieved chunks.

Pointwise relevance grading in a single call: the model scores every
candidate 0-3 against the question, and candidates are reordered by grade
(ties keep the retrieval order). One call per query keeps latency and cost
predictable; a local cross-encoder would avoid the LLM call but adds a model
to host (see ADR 0003).
"""

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from app.llm.base import LLMProvider, Message, Usage
from app.retrieval.types import RetrievedChunk

PROMPT_PATH = Path(__file__).parent / "prompts" / "rerank.md"
MAX_CHARS_PER_CANDIDATE = 1200


class _Grade(BaseModel):
    id: int
    relevance: Literal[0, 1, 2, 3]


class _Grades(BaseModel):
    grades: list[_Grade] = Field(description="One grade per candidate")


class LLMReranker:
    def __init__(self, llm: LLMProvider) -> None:
        self._llm = llm
        self._system = PROMPT_PATH.read_text()
        self.usage = Usage()

    def build_messages(self, question: str, candidates: list[RetrievedChunk]) -> list[Message]:
        body = "\n\n".join(
            f"[{i}]\n{c.text[:MAX_CHARS_PER_CANDIDATE]}" for i, c in enumerate(candidates, start=1)
        )
        return [
            Message(role="system", content=self._system),
            Message(role="user", content=f"Question: {question}\n\nCandidates:\n\n{body}"),
        ]

    async def rerank(
        self, question: str, candidates: list[RetrievedChunk], k: int
    ) -> list[RetrievedChunk]:
        if not candidates:
            return []
        result = await self._llm.complete_structured(
            self.build_messages(question, candidates), _Grades
        )
        self.usage = self.usage + result.usage
        # Missing or out-of-range ids count as grade 0 rather than failing the query.
        grade = {g.id: g.relevance for g in result.value.grades if 1 <= g.id <= len(candidates)}
        order = sorted(range(len(candidates)), key=lambda i: (-grade.get(i + 1, 0), i))
        return [candidates[i] for i in order[:k]]
