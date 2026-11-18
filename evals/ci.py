"""Run the eval suite, compare with baselines, and write a Markdown report.

    uv run python -m evals.ci --database-url postgresql+asyncpg://...      # CI
    uv run python -m evals.ci --database-url ... --record                  # refresh cache
    uv run python -m evals.ci --database-url ... --update-baselines        # accept new numbers

What runs:

* Retrieval (vector, keyword, hybrid) with the offline hashed embeddings:
  deterministic, always runs.
* Extraction, text-to-SQL and agent evals need an LLM. In CI they replay
  responses recorded in `evals/cache/` (`--record` with a real provider fills
  it). Without a cache they are reported as skipped, never as passing; a
  request missing from the cache (e.g. a prompt changed) fails the run and
  asks for a re-record.

Each metric is compared with `evals/baselines.json`: a drop larger than the
tolerance fails the run. Improvements pass, but stay unrecorded until
`--update-baselines` is run and committed, so the baseline only moves on purpose.
"""

import argparse
import asyncio
import json
import sys
import tempfile
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.agent.orchestrator import Agent
from app.analytics.text_to_sql import TextToSQL
from app.core.config import get_settings
from app.db.models import DEFAULT_TENANT_ID
from app.db.session import tenant_sessionmaker
from app.llm.base import LLMProvider
from app.llm.factory import build_llm_provider
from app.llm.fake import FakeEmbeddingProvider
from app.retrieval.retriever import STRATEGIES, Retriever
from evals import agent_eval, extraction_eval, retrieval_eval, sql_eval
from evals.cache import CachedLLM, CacheMissError, Mode, ResponseStore
from evals.ingest import ingest_dataset

ROOT = Path(__file__).parents[1]
DATASET = ROOT / "synthetic" / "datasets" / "telecom-seed42-n500"
GOLDEN = ROOT / "evals" / "datasets" / "golden-telecom-seed42-n500.jsonl"
SQL_GOLDEN = ROOT / "evals" / "datasets" / "sql_golden.jsonl"
AGENT_GOLDEN = ROOT / "evals" / "datasets" / "agent_questions.jsonl"
BASELINES = ROOT / "evals" / "baselines.json"
CACHE_DIR = ROOT / "evals" / "cache"
CACHE_MANIFEST = CACHE_DIR / "manifest.json"
EXTRACTION_SAMPLE = 100


@dataclass
class Suite:
    metrics: dict[str, float] = field(default_factory=dict)
    skipped: dict[str, str] = field(default_factory=dict)
    failed: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Comparison:
    name: str
    value: float
    baseline: float | None
    regressed: bool


def compare(
    metrics: dict[str, float], baselines: dict[str, float], tolerance: float
) -> list[Comparison]:
    rows = []
    for name, value in sorted(metrics.items()):
        baseline = baselines.get(name)
        regressed = baseline is not None and value < baseline - tolerance
        rows.append(Comparison(name, value, baseline, regressed))
    return rows


def to_markdown(suite: Suite, comparisons: list[Comparison], tolerance: float) -> str:
    regressions = [c for c in comparisons if c.regressed]
    status = "❌ regression" if regressions or suite.failed else "✅ no regressions"
    lines = [
        f"## Eval report: {status}",
        "",
        f"Dataset `{DATASET.name}`. A metric fails when it drops more than {tolerance} "
        "below `evals/baselines.json`.",
        "",
        "| Metric | Value | Baseline | Δ | |",
        "|--------|------:|---------:|--:|:-:|",
    ]
    for c in comparisons:
        if c.baseline is None:
            lines.append(f"| {c.name} | {c.value:.3f} | - | - | new |")
            continue
        delta = c.value - c.baseline
        delta = 0.0 if abs(delta) < 0.0005 else delta  # don't print -0.000
        mark = "❌" if c.regressed else ("⬆️" if delta > tolerance else "✅")
        lines.append(f"| {c.name} | {c.value:.3f} | {c.baseline:.3f} | {delta:+.3f} | {mark} |")
    if suite.failed:
        lines += ["", "**Failed:**"] + [f"- `{k}`: {v}" for k, v in suite.failed.items()]
    if suite.skipped:
        lines += ["", "**Skipped (not counted as passing):**"]
        lines += [f"- `{k}`: {v}" for k, v in suite.skipped.items()]
    return "\n".join(lines) + "\n"


async def _migrate(database_url: str) -> None:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.attributes["database_url"] = database_url
    cfg.attributes["configure_logger"] = False
    await asyncio.to_thread(command.upgrade, cfg, "head")


async def run_retrieval(sessionmaker: async_sessionmaker[AsyncSession], suite: Suite) -> None:
    embeddings = FakeEmbeddingProvider()
    for strategy in STRATEGIES:
        result = await retrieval_eval.evaluate(
            GOLDEN,
            sessionmaker=sessionmaker,
            retriever=Retriever(embeddings, strategy=strategy),
            embeddings=embeddings,
            k=10,
        )
        prefix = f"retrieval.{strategy}.fake-embedding"
        suite.metrics[f"{prefix}.mrr@10"] = result.overall.mrr
        suite.metrics[f"{prefix}.recall@10"] = result.overall.recall


def _llm_for(name: str, mode: Mode, real: LLMProvider | None) -> tuple[CachedLLM, str] | str:
    """A cached LLM for one eval, or the reason it can't run."""
    manifest = json.loads(CACHE_MANIFEST.read_text()) if CACHE_MANIFEST.exists() else {}
    path = CACHE_DIR / f"{name}.jsonl"
    if mode == "replay":
        if name not in manifest or not path.exists():
            return "no recorded LLM responses (run `make evals-record` with an API key)"
        model = manifest[name]["model"]
    else:
        settings = get_settings()
        model = f"{settings.llm_provider}:{settings.llm_model}"
        manifest[name] = {"model": model}
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        CACHE_MANIFEST.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return CachedLLM(ResponseStore(path), model=model, mode=mode, inner=real), model


async def _guarded(
    suite: Suite,
    name: str,
    mode: Mode,
    real: LLMProvider | None,
    body: Callable[[CachedLLM, str], Awaitable[dict[str, float]]],
) -> None:
    llm = _llm_for(name, mode, real)
    if isinstance(llm, str):
        suite.skipped[name] = llm
        return
    cached, model = llm
    try:
        for metric, value in (await body(cached, model)).items():
            suite.metrics[f"{name}.{metric}"] = value
    except CacheMissError as exc:
        suite.failed[name] = f"{exc}; prompts changed since recording, run `make evals-record`"


async def run_llm_evals(
    engine: AsyncEngine,
    sessionmaker: async_sessionmaker[AsyncSession],
    suite: Suite,
    mode: Mode,
    real: LLMProvider | None,
) -> None:
    async def extraction(llm: CachedLLM, model: str) -> dict[str, float]:
        with tempfile.TemporaryDirectory() as out:
            result = await extraction_eval.evaluate(
                DATASET,
                limit=EXTRACTION_SAMPLE,
                seed=0,
                concurrency=1,
                out=Path(out),
                llm=llm,
                model=model,
            )
        if result.errors:
            raise CacheMissError(f"{result.errors} extractions failed")
        return {f"{f}.macro_f1": r.macro_f1 for f, r in result.fields.items()} | {
            "competitors.f1": result.competitors.f1
        }

    async def sql(llm: CachedLLM, model: str) -> dict[str, float]:
        result = await sql_eval.evaluate(
            sql_eval.load_questions(SQL_GOLDEN),
            engine=engine,
            llm=llm,
            model=model,
            golden=SQL_GOLDEN.stem,
        )
        return {"execution_accuracy": result.accuracy}

    async def agent(llm: CachedLLM, model: str) -> dict[str, float]:
        runner = Agent(
            llm,
            sessionmaker=sessionmaker,
            retriever=Retriever(FakeEmbeddingProvider(), strategy="hybrid"),
            text_to_sql=TextToSQL(llm, engine, tenant_id=DEFAULT_TENANT_ID),
        )
        result = await agent_eval.evaluate(
            agent_eval.load_questions(AGENT_GOLDEN),
            agent=runner,
            judge_llm=None,
            model=model,
            judge_model=None,
            golden=AGENT_GOLDEN.stem,
        )
        if result.errors:
            failures = [q.error for q in result.questions if q.error]
            raise CacheMissError(f"{result.errors} questions failed, e.g. {failures[0]}")
        return {
            "routing_accuracy": result.routing_accuracy,
            "citation_validity": result.citation_validity,
        }

    await _guarded(suite, "extraction", mode, real, extraction)
    await _guarded(suite, "sql", mode, real, sql)
    await _guarded(suite, "agent", mode, real, agent)


async def run(database_url: str, *, mode: Mode) -> Suite:
    await _migrate(database_url)
    engine = create_async_engine(database_url)
    sessionmaker = tenant_sessionmaker(engine, DEFAULT_TENANT_ID)
    suite = Suite()
    real = build_llm_provider(get_settings()) if mode == "record" else None
    try:
        await ingest_dataset(sessionmaker, DATASET)
        await run_retrieval(sessionmaker, suite)
        await run_llm_evals(engine, sessionmaker, suite, mode, real)
    finally:
        await engine.dispose()
    return suite


def finish(suite: Suite, *, update_baselines: bool, report: Path, baselines_path: Path) -> bool:
    """Compare with baselines (optionally replacing them), write the report, return pass/fail."""
    baselines: dict[str, Any] = (
        json.loads(baselines_path.read_text()) if baselines_path.exists() else {}
    )
    tolerance = float(baselines.get("tolerance", 0.01))
    if update_baselines:
        metrics = {k: round(v, 4) for k, v in sorted(suite.metrics.items())}
        baselines = {"tolerance": tolerance, "metrics": metrics}
        baselines_path.write_text(json.dumps(baselines, indent=2) + "\n")
    comparisons = compare(suite.metrics, baselines.get("metrics", {}), tolerance)
    markdown = to_markdown(suite, comparisons, tolerance)
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(markdown)
    print(markdown)
    return not suite.failed and not any(c.regressed for c in comparisons)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run evals and compare with baselines.")
    parser.add_argument("--database-url", default=None, help="defaults to DATABASE_URL")
    parser.add_argument("--report", type=Path, default=Path("eval-report.md"))
    parser.add_argument("--record", action="store_true", help="call the real LLM and cache")
    parser.add_argument("--update-baselines", action="store_true")
    args = parser.parse_args(argv)
    if args.record and get_settings().llm_provider == "fake":
        print("--record needs a real LLM_PROVIDER.", file=sys.stderr)
        return 2
    url = args.database_url or str(get_settings().database_url)
    suite = asyncio.run(run(url, mode="record" if args.record else "replay"))
    ok = finish(
        suite, update_baselines=args.update_baselines, report=args.report, baselines_path=BASELINES
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
