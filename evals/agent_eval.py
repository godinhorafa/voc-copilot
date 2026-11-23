"""Agent eval: routing, citations and faithfulness.

    uv run python -m evals.agent_eval evals/datasets/agent_questions.jsonl --judge

Runs every question through the production agent against the ingested dataset,
with the configured LLM, and reports:

* routing accuracy  - the agent used every tool the question needs
* over-calling      - it also used a tool the question doesn't need
* citation validity - citations kept / (kept + dropped by verification)
* faithfulness      - (with --judge) share of answer claims an LLM judge finds
                      supported by the evidence; the judge should be a
                      different model (JUDGE_MODEL) to limit self-preference
"""

import argparse
import asyncio
import json
import sys
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from app.agent.orchestrator import Agent, AgentResult
from app.analytics.text_to_sql import TextToSQL
from app.core.config import get_settings
from app.db.models import DEFAULT_TENANT_ID
from app.db.session import create_engine, tenant_sessionmaker
from app.llm.base import LLMProvider, Message, Usage
from app.llm.factory import build_embedding_provider, build_llm_provider
from app.retrieval.retriever import Retriever
from evals.cache import ProviderUnavailableError

JUDGE_PROMPT = Path(__file__).parent / "prompts" / "judge_faithfulness.md"


@dataclass(frozen=True)
class AgentQuestion:
    id: str
    question: str
    expected_tools: list[str]


class _Claim(BaseModel):
    claim: str
    verdict: Literal["supported", "unsupported"]
    reason: str


class _Judgement(BaseModel):
    claims: list[_Claim]


@dataclass(frozen=True)
class QuestionReport:
    id: str
    tools_used: list[str]
    routed: bool
    over_called: bool
    steps: int
    citations: int
    dropped: int
    claims: int | None
    supported: int | None
    error: str | None


@dataclass(frozen=True)
class AgentEvalResult:
    golden: str
    model: str
    judge: str | None
    n: int
    routing_accuracy: float
    over_calling: float
    citation_validity: float
    faithfulness: float | None
    mean_steps: float
    errors: int
    usage: Usage
    questions: list[QuestionReport]
    created_at: str


def load_questions(path: Path) -> list[AgentQuestion]:
    with path.open(encoding="utf-8") as f:
        return [AgentQuestion(**json.loads(line)) for line in f if line.strip()]


def evidence_text(result: AgentResult) -> str:
    parts = [f"[S{n}] {c.text}" for n, c in enumerate(result.sources, start=1)]
    for m in result.metrics:
        rows = "\n".join(" | ".join(map(str, r)) for r in m.answer.result.rows[:30])
        parts.append(f"[M{m.number}] {m.question}\n{' | '.join(m.answer.result.columns)}\n{rows}")
    return "\n\n".join(parts) or "(no evidence)"


async def judge(llm: LLMProvider, answer: str, evidence: str) -> tuple[_Judgement, Usage]:
    result = await llm.complete_structured(
        [
            Message(role="system", content=JUDGE_PROMPT.read_text()),
            Message(role="user", content=f"Answer:\n{answer}\n\nEvidence:\n{evidence}"),
        ],
        _Judgement,
    )
    return result.value, result.usage


async def evaluate(
    questions: list[AgentQuestion],
    *,
    agent: Agent,
    judge_llm: LLMProvider | None,
    model: str,
    judge_model: str | None,
    golden: str,
) -> AgentEvalResult:
    reports: list[QuestionReport] = []
    usage = Usage()
    for q in questions:
        try:
            result = await agent.run(q.question)
        except ProviderUnavailableError:
            raise  # every other question would fail the same way
        except Exception as exc:  # one failing question shouldn't stop the eval
            reports.append(QuestionReport(q.id, [], False, False, 0, 0, 0, None, None, repr(exc)))
            continue
        usage = usage + result.usage
        claims = supported = None
        if judge_llm is not None:
            judgement, judge_usage = await judge(judge_llm, result.answer, evidence_text(result))
            usage = usage + judge_usage
            claims = len(judgement.claims)
            supported = sum(c.verdict == "supported" for c in judgement.claims)
        used = set(result.tools_used)
        reports.append(
            QuestionReport(
                id=q.id,
                tools_used=result.tools_used,
                routed=set(q.expected_tools) <= used,
                over_called=bool(used - set(q.expected_tools)),
                steps=result.steps,
                citations=len(result.citations),
                dropped=len(result.dropped_citations),
                claims=claims,
                supported=supported,
                error=None,
            )
        )

    ok = [r for r in reports if r.error is None]
    n = len(reports)
    cited = sum(r.citations for r in ok)
    dropped = sum(r.dropped for r in ok)
    judged = [r for r in ok if r.claims]
    return AgentEvalResult(
        golden=golden,
        model=model,
        judge=judge_model,
        n=n,
        routing_accuracy=sum(r.routed for r in ok) / n if n else 0.0,
        over_calling=sum(r.over_called for r in ok) / n if n else 0.0,
        citation_validity=cited / (cited + dropped) if cited + dropped else 1.0,
        faithfulness=(
            sum(r.supported or 0 for r in judged) / sum(r.claims or 0 for r in judged)
            if judged
            else None
        ),
        mean_steps=sum(r.steps for r in ok) / len(ok) if ok else 0.0,
        errors=n - len(ok),
        usage=usage,
        questions=reports,
        created_at=datetime.now(UTC).isoformat(timespec="seconds"),
    )


def to_markdown(result: AgentEvalResult) -> str:
    faith = f"{result.faithfulness:.3f}" if result.faithfulness is not None else "not judged"
    lines = [
        f"# Agent eval: {result.golden}",
        "",
        f"- Model: `{result.model}`; judge: `{result.judge or 'none'}`",
        f"- Questions: {result.n} (errors: {result.errors}); mean steps {result.mean_steps:.1f}",
        f"- Tokens: {result.usage.input_tokens:,} in / {result.usage.output_tokens:,} out",
        f"- Generated: {result.created_at}",
        "",
        "| Routing accuracy | Over-calling | Citation validity | Faithfulness |",
        "|-----------------:|-------------:|------------------:|-------------:|",
        f"| {result.routing_accuracy:.3f} | {result.over_calling:.3f} | "
        f"{result.citation_validity:.3f} | {faith} |",
        "",
        "| Question | Tools used | Routed | Citations (dropped) | Supported claims |",
        "|----------|------------|:------:|--------------------:|-----------------:|",
    ]
    for r in result.questions:
        support = f"{r.supported}/{r.claims}" if r.claims is not None else "-"
        tools = ", ".join(r.tools_used) or (f"error: {r.error}" if r.error else "none")
        lines.append(
            f"| {r.id} | {tools} | {'yes' if r.routed else 'no'} | "
            f"{r.citations} ({r.dropped}) | {support} |"
        )
    return "\n".join(lines) + "\n"


def write_reports(result: AgentEvalResult, out: Path) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    stem = f"agent-{result.golden}-{result.model.replace('/', '_').replace(':', '_')}"
    (out / f"{stem}.md").write_text(to_markdown(result))
    (out / f"{stem}.json").write_text(json.dumps(asdict(result), indent=2, default=str) + "\n")
    return out / f"{stem}.md"


async def _run(golden: Path, out: Path, use_judge: bool) -> int:
    settings = get_settings()
    engine = create_engine(settings)
    llm = build_llm_provider(settings)
    judge_llm = None
    if use_judge:
        judge_settings = settings.model_copy(update={"llm_model": settings.judge_model})
        judge_llm = build_llm_provider(judge_settings)
    agent = Agent(
        llm,
        sessionmaker=tenant_sessionmaker(engine, DEFAULT_TENANT_ID),
        retriever=Retriever(
            build_embedding_provider(settings), strategy=settings.retrieval_strategy
        ),
        text_to_sql=TextToSQL(llm, engine, tenant_id=DEFAULT_TENANT_ID),
        max_steps=settings.agent_max_steps,
    )
    try:
        result = await evaluate(
            load_questions(golden),
            agent=agent,
            judge_llm=judge_llm,
            model=f"{settings.llm_provider}:{settings.llm_model}",
            judge_model=settings.judge_model if use_judge else None,
            golden=golden.stem,
        )
    finally:
        await engine.dispose()
    path = await asyncio.to_thread(write_reports, result, out)
    print(to_markdown(result))
    print(f"report: {path}", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Agent routing, citations and faithfulness.")
    parser.add_argument("golden", type=Path)
    parser.add_argument("--out", type=Path, default=Path("evals/reports"))
    parser.add_argument("--judge", action="store_true", help="score faithfulness with JUDGE_MODEL")
    args = parser.parse_args(argv)
    if get_settings().llm_provider == "fake":
        print(
            "LLM_PROVIDER=fake can't route or answer; configure a real provider.", file=sys.stderr
        )
        return 2
    return asyncio.run(_run(args.golden, args.out, args.judge))


if __name__ == "__main__":
    raise SystemExit(main())
