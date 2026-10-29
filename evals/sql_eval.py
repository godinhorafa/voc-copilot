"""Text-to-SQL eval: execution accuracy on a golden set.

    uv run python -m evals.sql_eval evals/datasets/sql_golden.jsonl

Each golden question has a reference query. Both the reference and the
generated query run against the same database (the dataset must be ingested),
and the results are compared as tables: column names are ignored, rows are
compared as multisets unless the question requires an order, and numbers
match within a small tolerance. This is the "execution accuracy" used by
text-to-SQL benchmarks; comparing SQL strings would penalize equivalent queries.

Questions about insight columns (contact_reason, sentiment, ...) depend on the
extraction that ran during ingestion; the reference query reads the same data,
so the eval measures SQL generation, not extraction.
"""

import argparse
import asyncio
import json
import math
import sys
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncEngine

from app.analytics.executor import execute
from app.analytics.sql_guard import check
from app.analytics.text_to_sql import MetricsFilters, TextToSQL, TextToSQLError
from app.core.config import get_settings
from app.db.session import create_engine
from app.llm.base import LLMProvider, Usage
from app.llm.factory import build_llm_provider

REL_TOL = 1e-3


@dataclass(frozen=True)
class SQLQuestion:
    id: str
    question: str
    industry: str | None
    sql: str
    ordered: bool


@dataclass(frozen=True)
class QuestionOutcome:
    id: str
    correct: bool
    attempts: int
    generated_sql: str | None
    error: str | None


@dataclass(frozen=True)
class SQLEvalResult:
    golden: str
    model: str
    n: int
    accuracy: float
    first_try_accuracy: float
    errors: int
    usage: Usage
    outcomes: list[QuestionOutcome]
    created_at: str


def load_questions(path: Path) -> list[SQLQuestion]:
    with path.open(encoding="utf-8") as f:
        return [SQLQuestion(**json.loads(line)) for line in f if line.strip()]


def _cell(value: Any) -> Any:
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, int | float):
        return round(float(value), 6)
    return str(value)


def _same_cell(a: Any, b: Any) -> bool:
    if isinstance(a, float) and isinstance(b, float):
        return math.isclose(a, b, rel_tol=REL_TOL, abs_tol=1e-9)
    return bool(a == b)


def _same_row(a: tuple[Any, ...], b: tuple[Any, ...]) -> bool:
    return len(a) == len(b) and all(_same_cell(x, y) for x, y in zip(a, b, strict=True))


def results_match(expected: list[list[Any]], actual: list[list[Any]], *, ordered: bool) -> bool:
    """Compare query results as tables, ignoring column names.

    Columns may come in a different order: each row is compared as a sorted
    tuple of its cells, which is lenient but avoids failing `SELECT n, month`
    against `SELECT month, n`.
    """
    if len(expected) != len(actual):
        return False

    exp_rows, act_rows = _normalize(expected), _normalize(actual)
    if ordered:
        return all(_same_row(e, a) for e, a in zip(exp_rows, act_rows, strict=True))
    # Multiset comparison with tolerance: match each expected row to an unused actual row.
    remaining = list(range(len(act_rows)))
    for row in exp_rows:
        match = next((i for i in remaining if _same_row(row, act_rows[i])), None)
        if match is None:
            return False
        remaining.remove(match)
    return True


def _normalize(rows: list[list[Any]]) -> list[tuple[Any, ...]]:
    def key(value: Any) -> tuple[str, str]:
        return (type(value).__name__, str(value))

    return [tuple(sorted((_cell(v) for v in row), key=key)) for row in rows]


async def evaluate(
    questions: list[SQLQuestion],
    *,
    engine: AsyncEngine,
    llm: LLMProvider,
    model: str,
    golden: str,
    max_rows: int = 1000,
) -> SQLEvalResult:
    text_to_sql = TextToSQL(llm, engine, max_rows=max_rows)
    outcomes: list[QuestionOutcome] = []
    usage = Usage()
    for q in questions:
        reference = await execute(engine, check(q.sql, max_rows=max_rows), max_rows=max_rows)
        try:
            answer = await text_to_sql.answer(q.question, MetricsFilters(industry=q.industry))
        except TextToSQLError as exc:
            outcomes.append(QuestionOutcome(q.id, False, len(exc.errors), None, exc.errors[-1]))
            continue
        usage = usage + answer.usage
        correct = results_match(reference.rows, answer.result.rows, ordered=q.ordered)
        outcomes.append(QuestionOutcome(q.id, correct, answer.attempts, answer.sql, None))

    n = len(outcomes)
    return SQLEvalResult(
        golden=golden,
        model=model,
        n=n,
        accuracy=sum(o.correct for o in outcomes) / n if n else 0.0,
        first_try_accuracy=sum(o.correct and o.attempts == 1 for o in outcomes) / n if n else 0.0,
        errors=sum(o.error is not None for o in outcomes),
        usage=usage,
        outcomes=outcomes,
        created_at=datetime.now(UTC).isoformat(timespec="seconds"),
    )


def to_markdown(result: SQLEvalResult) -> str:
    lines = [
        f"# Text-to-SQL eval: {result.golden}",
        "",
        f"- Model: `{result.model}`",
        f"- Questions: {result.n}; execution accuracy **{result.accuracy:.3f}** "
        f"(first try {result.first_try_accuracy:.3f}); failed to produce a valid query: "
        f"{result.errors}",
        f"- Tokens: {result.usage.input_tokens:,} in / {result.usage.output_tokens:,} out",
        f"- Generated: {result.created_at}",
        "",
        "| Question | Correct | Attempts |",
        "|----------|:-------:|---------:|",
    ]
    lines += [
        f"| {o.id} | {'yes' if o.correct else 'no'} | {o.attempts} |" for o in result.outcomes
    ]
    return "\n".join(lines) + "\n"


def write_reports(result: SQLEvalResult, out: Path) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    stem = f"sql-{result.golden}-{result.model.replace('/', '_').replace(':', '_')}"
    (out / f"{stem}.md").write_text(to_markdown(result))
    (out / f"{stem}.json").write_text(json.dumps(asdict(result), indent=2, default=str) + "\n")
    return out / f"{stem}.md"


async def _run(golden: Path, out: Path) -> int:
    settings = get_settings()
    engine = create_engine(settings)
    try:
        result = await evaluate(
            load_questions(golden),
            engine=engine,
            llm=build_llm_provider(settings),
            model=f"{settings.llm_provider}:{settings.llm_model}",
            golden=golden.stem,
        )
    finally:
        await engine.dispose()
    path = await asyncio.to_thread(write_reports, result, out)
    print(to_markdown(result))
    print(f"report: {path}", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Text-to-SQL execution accuracy.")
    parser.add_argument("golden", type=Path)
    parser.add_argument("--out", type=Path, default=Path("evals/reports"))
    parser.add_argument("--allow-fake", action="store_true")
    args = parser.parse_args(argv)
    if get_settings().llm_provider == "fake" and not args.allow_fake:
        print("LLM_PROVIDER=fake can't write SQL; configure a real provider.", file=sys.stderr)
        return 2
    return asyncio.run(_run(args.golden, args.out))


if __name__ == "__main__":
    raise SystemExit(main())
