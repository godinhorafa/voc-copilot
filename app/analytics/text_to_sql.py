"""Question -> guarded SQL -> rows, with one repair round.

The model writes SQL from the view catalog; the guard checks it; the executor
runs it with database-level limits. A guard rejection or a database error goes
back to the model as feedback for another attempt.
"""

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import structlog
from pydantic import BaseModel, Field
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.analytics.catalog import describe
from app.analytics.executor import QueryResult, execute
from app.analytics.sql_guard import SQLGuardError, check
from app.llm.base import LLMProvider, Message, Usage

PROMPT_PATH = Path(__file__).parent / "prompts" / "text_to_sql.md"
logger = structlog.get_logger(__name__)


class _GeneratedSQL(BaseModel):
    sql: str = Field(description="One PostgreSQL SELECT over the analytics views")
    explanation: str = Field(description="One sentence on what the query computes")


class TextToSQLError(Exception):
    def __init__(self, message: str, errors: list[str]) -> None:
        super().__init__(message)
        self.errors = errors


@dataclass(frozen=True)
class MetricsFilters:
    industry: str | None = None
    start: datetime | None = None
    end: datetime | None = None

    def describe(self) -> str:
        parts = []
        if self.industry:
            parts.append(f"industry = '{self.industry}'")
        if self.start:
            parts.append(f"started_at >= '{self.start.isoformat()}'")
        if self.end:
            parts.append(f"started_at < '{self.end.isoformat()}'")
        if not parts:
            return ""
        return (
            "## Scope\n\nThe analyst is looking at: "
            + ", ".join(parts)
            + (". Apply these filters unless the question explicitly asks otherwise.")
        )


@dataclass
class MetricsAnswer:
    sql: str
    explanation: str
    result: QueryResult
    attempts: int
    usage: Usage
    errors: list[str] = field(default_factory=list)  # problems fixed along the way


class TextToSQL:
    def __init__(
        self,
        llm: LLMProvider,
        engine: AsyncEngine,
        *,
        max_rows: int = 200,
        timeout_ms: int = 5000,
        max_attempts: int = 2,
        set_role: bool = True,
    ) -> None:
        self._llm = llm
        self._engine = engine
        self._set_role = set_role
        self._max_rows = max_rows
        self._timeout_ms = timeout_ms
        self._max_attempts = max_attempts
        self._template = PROMPT_PATH.read_text()

    def build_messages(self, question: str, filters: MetricsFilters) -> list[Message]:
        system = self._template.format(schema=describe(), filters=filters.describe()).strip()
        return [
            Message(role="system", content=system),
            Message(role="user", content=question),
        ]

    async def answer(self, question: str, filters: MetricsFilters | None = None) -> MetricsAnswer:
        messages = self.build_messages(question, filters or MetricsFilters())
        usage = Usage()
        errors: list[str] = []
        for attempt in range(1, self._max_attempts + 1):
            generated = await self._llm.complete_structured(messages, _GeneratedSQL)
            usage = usage + generated.usage
            try:
                guarded = check(generated.value.sql, max_rows=self._max_rows)
                result = await execute(
                    self._engine,
                    guarded,
                    max_rows=self._max_rows,
                    timeout_ms=self._timeout_ms,
                    set_role=self._set_role,
                )
            except SQLGuardError as exc:
                error = f"rejected by the SQL guard: {exc}"
            except DBAPIError as exc:
                # The driver's message (e.g. "column x does not exist") is what the model needs.
                error = f"database error: {str(exc.orig).splitlines()[0]}"
            else:
                return MetricsAnswer(
                    sql=guarded.sql,
                    explanation=generated.value.explanation,
                    result=result,
                    attempts=attempt,
                    usage=usage,
                    errors=errors,
                )
            logger.info("text_to_sql_retry", attempt=attempt, error=error)
            errors.append(error)
            messages = [
                *messages,
                Message(role="assistant", content=generated.value.model_dump_json()),
                Message(role="user", content=f"That query failed ({error}). Fix it."),
            ]
        raise TextToSQLError(f"no valid query after {self._max_attempts} attempts", errors)
