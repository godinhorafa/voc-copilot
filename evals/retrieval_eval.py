"""Retrieval eval: run golden questions through the production search.

    uv run python -m evals.retrieval_eval evals/datasets/golden-telecom-seed42-n500.jsonl

Needs the dataset ingested into DATABASE_URL with the same embedding provider
configured here (`make seed` or `scripts/ingest.py`). Search results are chunks;
they are turned into a conversation ranking (best chunk first) and scored
against each question's relevant conversations with MRR, precision@k and
recall@k (normalized by min(k, |relevant|), see `evals.metrics`).
"""

import argparse
import asyncio
import json
import sys
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.db.models import Conversation
from app.db.session import create_engine, create_sessionmaker
from app.llm.base import EmbeddingProvider
from app.llm.factory import build_embedding_provider, build_llm_provider
from app.retrieval.rerank import LLMReranker
from app.retrieval.retriever import STRATEGIES, Retriever, Strategy
from app.retrieval.types import SearchFilters, conversation_ranking
from evals.build_golden import GoldenQuestion
from evals.metrics import RankingScores, mean_ranking, ranking_scores


@dataclass(frozen=True)
class QuestionResult:
    id: str
    type: str
    relevant: int
    scores: RankingScores
    top: list[str]


@dataclass(frozen=True)
class RetrievalResult:
    golden: str
    strategy: str
    embedding_model: str
    k: int
    overall: RankingScores
    by_type: dict[str, RankingScores]
    questions: list[QuestionResult]
    created_at: str


def load_golden(path: Path) -> list[GoldenQuestion]:
    with path.open(encoding="utf-8") as f:
        return [GoldenQuestion(**json.loads(line)) for line in f if line.strip()]


def _dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


async def check_ingested(
    sessionmaker: async_sessionmaker[AsyncSession], golden: list[GoldenQuestion]
) -> int:
    """How many of the golden set's relevant conversations are not in the database."""
    wanted = {cid for q in golden for cid in q.relevant}
    async with sessionmaker() as session:
        present = await session.scalar(
            select(func.count())
            .select_from(Conversation)
            .where(
                Conversation.external_id.in_(wanted),
                Conversation.chunked_hash == Conversation.content_hash,
            )
        )
    return len(wanted) - (present or 0)


async def evaluate(
    golden_path: Path,
    *,
    sessionmaker: async_sessionmaker[AsyncSession],
    retriever: Retriever,
    embeddings: EmbeddingProvider,
    k: int,
) -> RetrievalResult:
    golden = load_golden(golden_path)
    vectors = await embeddings.embed([q.question for q in golden])
    results: list[QuestionResult] = []
    for q, vector in zip(golden, vectors, strict=True):
        filters = SearchFilters(industry=q.industry, start=_dt(q.start), end=_dt(q.end))
        async with sessionmaker() as session:
            # Over-fetch chunks so k distinct conversations survive deduplication.
            chunks = await retriever.search(
                session, q.question, k=k * 3, filters=filters, query_embedding=vector
            )
        ranked = conversation_ranking(chunks)[:k]
        relevant = set(q.relevant)
        results.append(
            QuestionResult(
                id=q.id,
                type=q.type,
                relevant=len(relevant),
                scores=ranking_scores(ranked, relevant, k),
                top=ranked,
            )
        )

    grouped: dict[str, list[RankingScores]] = defaultdict(list)
    for r in results:
        grouped[r.type].append(r.scores)
    return RetrievalResult(
        golden=golden_path.stem,
        strategy=retriever.name,
        embedding_model=embeddings.model,
        k=k,
        overall=mean_ranking([r.scores for r in results]),
        by_type={t: mean_ranking(s) for t, s in sorted(grouped.items())},
        questions=results,
        created_at=datetime.now(UTC).isoformat(timespec="seconds"),
    )


def to_markdown(result: RetrievalResult) -> str:
    k = result.k
    lines = [
        f"# Retrieval eval: {result.golden}",
        "",
        f"- Strategy: `{result.strategy}`; embeddings `{result.embedding_model}`",
        f"- Questions: {len(result.questions)}; k = {k}",
        f"- Generated: {result.created_at}",
        "",
        f"| Questions | n | MRR@{k} | Precision@{k} | Recall@{k} |",
        "|-----------|--:|-------:|-------------:|----------:|",
    ]

    def row(name: str, n: int, s: RankingScores) -> str:
        return f"| {name} | {n} | {s.mrr:.3f} | {s.precision:.3f} | {s.recall:.3f} |"

    counts: dict[str, int] = defaultdict(int)
    for q in result.questions:
        counts[q.type] += 1
    lines.append(row("**all**", len(result.questions), result.overall))
    lines += [row(t, counts[t], s) for t, s in result.by_type.items()]
    lines += ["", "## Hardest questions", ""]
    worst = sorted(result.questions, key=lambda q: (q.scores.recall, q.scores.mrr))[:5]
    lines += [
        f"- `{q.id}`: recall {q.scores.recall:.2f}, MRR {q.scores.mrr:.2f} ({q.relevant} relevant)"
        for q in worst
    ]
    return "\n".join(lines) + "\n"


def write_reports(result: RetrievalResult, out: Path) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    stem = f"retrieval-{result.golden}-{result.strategy}-{result.embedding_model.replace('/', '_')}"
    (out / f"{stem}.md").write_text(to_markdown(result))
    (out / f"{stem}.json").write_text(json.dumps(asdict(result), indent=2) + "\n")
    return out / f"{stem}.md"


async def _run(golden: Path, k: int, out: Path, strategy: Strategy, rerank: bool) -> int:
    settings = get_settings()
    engine = create_engine(settings)
    sessionmaker = create_sessionmaker(engine)
    try:
        missing = await check_ingested(sessionmaker, load_golden(golden))
        if missing:
            print(
                f"{missing} relevant conversations are not ingested (or not chunked) in "
                f"{settings.database_url.hosts()[0]['host']}; run `make seed` first.",
                file=sys.stderr,
            )
            return 2
        embeddings = build_embedding_provider(settings)
        reranker = LLMReranker(build_llm_provider(settings)) if rerank else None
        retriever = Retriever(embeddings, strategy=strategy, reranker=reranker)
        result = await evaluate(
            golden, sessionmaker=sessionmaker, retriever=retriever, embeddings=embeddings, k=k
        )
    finally:
        await engine.dispose()
    path = await asyncio.to_thread(write_reports, result, out)
    print(to_markdown(result))
    print(f"report: {path}", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Score retrieval on a golden set.")
    parser.add_argument("golden", type=Path)
    parser.add_argument("--k", type=int, default=10)
    parser.add_argument("--strategy", choices=STRATEGIES, default="vector")
    parser.add_argument("--rerank", action="store_true", help="rerank with the configured LLM")
    parser.add_argument("--out", type=Path, default=Path("evals/reports"))
    args = parser.parse_args(argv)
    strategy: Strategy = args.strategy
    return asyncio.run(_run(args.golden, args.k, args.out, strategy, args.rerank))


if __name__ == "__main__":
    raise SystemExit(main())
