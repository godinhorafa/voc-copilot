"""Score insight extraction against generator ground truth.

    uv run python -m evals.extraction_eval synthetic/datasets/telecom-seed42-n500 --limit 200

Runs the production `InsightExtractor` with the configured LLM provider
(LLM_PROVIDER, LLM_MODEL, ...) directly on `conversations.jsonl`, compares with
`ground_truth.jsonl`, and writes a Markdown and a JSON report. Each field also
gets a majority-class baseline, so the numbers have a floor to compare with.
"""

import argparse
import asyncio
import json
import random
import sys
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.core.config import get_settings
from app.ingestion.extraction import Extraction, InsightExtractor, extractor_version
from app.llm.base import LLMProvider, Usage
from app.llm.factory import build_llm_provider
from evals.metrics import FieldReport, SetScores, classification_report, set_scores

CATEGORICAL = ("contact_reason", "sentiment", "resolved", "churn_risk")


@dataclass(frozen=True)
class EvalResult:
    dataset: str
    model: str
    extractor_version: str
    n: int
    errors: int
    usage: Usage
    fields: dict[str, FieldReport]
    baselines: dict[str, float]
    competitors: SetScores
    created_at: str


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def load_sample(
    dataset: Path, limit: int | None, seed: int
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    conversations = _read_jsonl(dataset / "conversations.jsonl")
    truth = {t["external_id"]: t for t in _read_jsonl(dataset / "ground_truth.jsonl")}
    pairs = [(c, truth[c["external_id"]]) for c in conversations]
    if limit is not None and limit < len(pairs):
        pairs = random.Random(seed).sample(pairs, limit)
    return pairs


async def run_extractions(
    llm: LLMProvider, conversations: list[dict[str, Any]], concurrency: int
) -> list[Extraction | Exception]:
    extractor = InsightExtractor(llm)
    semaphore = asyncio.Semaphore(concurrency)

    async def one(conv: dict[str, Any]) -> Extraction | Exception:
        turns = [(t["speaker"], t["text"]) for t in conv["turns"]]
        async with semaphore:
            try:
                return await extractor.extract(conv["industry"], conv["channel"], turns)
            except Exception as exc:  # counted as an error, not a crash of the whole eval
                return exc

    return list(await asyncio.gather(*(one(c) for c in conversations)))


def score(
    pairs: list[tuple[dict[str, Any], dict[str, Any]]],
    results: list[Extraction | Exception],
    *,
    dataset: str,
    model: str,
) -> EvalResult:
    ok = [
        (truth, r)
        for (_, truth), r in zip(pairs, results, strict=True)
        if isinstance(r, Extraction)
    ]
    usage = Usage()
    for _, r in ok:
        usage = usage + r.usage

    fields: dict[str, FieldReport] = {}
    baselines: dict[str, float] = {}
    for name in CATEGORICAL:
        expected = [truth[name] for truth, _ in ok]
        predicted = [getattr(r.fields, name) for _, r in ok]
        fields[name] = classification_report(expected, predicted)
        majority = Counter(expected).most_common(1)
        baselines[name] = majority[0][1] / len(expected) if majority else 0.0

    competitors = set_scores(
        [set(truth["competitors"]) for truth, _ in ok],
        [set(r.fields.competitors) for _, r in ok],
    )
    return EvalResult(
        dataset=dataset,
        model=model,
        extractor_version=extractor_version(),
        n=len(ok),
        errors=len(results) - len(ok),
        usage=usage,
        fields=fields,
        baselines=baselines,
        competitors=competitors,
        created_at=datetime.now(UTC).isoformat(timespec="seconds"),
    )


def to_markdown(result: EvalResult) -> str:
    lines = [
        f"# Extraction eval: {result.dataset}",
        "",
        f"- Model: `{result.model}`; extractor `{result.extractor_version}`",
        f"- Conversations scored: {result.n} (errors: {result.errors})",
        f"- Tokens: {result.usage.input_tokens:,} in / {result.usage.output_tokens:,} out",
        f"- Generated: {result.created_at}",
        "",
        "| Field | Accuracy | Macro F1 | Majority baseline |",
        "|-------|---------:|---------:|------------------:|",
    ]
    for name, report in result.fields.items():
        lines.append(
            f"| {name} | {report.accuracy:.3f} | {report.macro_f1:.3f} | "
            f"{result.baselines[name]:.3f} |"
        )
    c = result.competitors
    lines += [
        "",
        "| Competitor mentions | Precision | Recall | F1 | Exact set match |",
        "|---------------------|----------:|-------:|---:|----------------:|",
        f"| micro | {c.precision:.3f} | {c.recall:.3f} | {c.f1:.3f} | {c.exact_match:.3f} |",
        "",
        "## Contact reason by class",
        "",
        "| Reason | Precision | Recall | F1 | Support |",
        "|--------|----------:|-------:|---:|--------:|",
    ]
    for label, s in result.fields["contact_reason"].per_class.items():
        lines.append(f"| {label} | {s.precision:.3f} | {s.recall:.3f} | {s.f1:.3f} | {s.support} |")
    lines += ["", "## Most common confusions", ""]
    for name, report in result.fields.items():
        if report.confusions:
            pairs = ", ".join(f"{e} → {p} ({n})" for e, p, n in report.confusions)
            lines.append(f"- **{name}**: {pairs}")
    return "\n".join(lines) + "\n"


async def evaluate(
    dataset: Path,
    *,
    limit: int | None,
    seed: int,
    concurrency: int,
    out: Path,
    llm: LLMProvider,
    model: str,
) -> EvalResult:
    pairs = load_sample(dataset, limit, seed)
    results = await run_extractions(llm, [c for c, _ in pairs], concurrency)
    result = score(pairs, results, dataset=dataset.name, model=model)
    await asyncio.to_thread(write_reports, result, out)
    return result


def write_reports(result: EvalResult, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    stem = f"extraction-{result.dataset}-{result.model.replace('/', '_').replace(':', '_')}"
    (out / f"{stem}.md").write_text(to_markdown(result))
    (out / f"{stem}.json").write_text(json.dumps(asdict(result), indent=2, default=str) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Score insight extraction against ground truth.")
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--limit", type=int, default=200, help="sample size (0 = all)")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--out", type=Path, default=Path("evals/reports"))
    parser.add_argument(
        "--allow-fake", action="store_true", help="run with LLM_PROVIDER=fake (pipeline check)"
    )
    args = parser.parse_args(argv)

    settings = get_settings()
    if settings.llm_provider == "fake" and not args.allow_fake:
        print(
            "LLM_PROVIDER=fake gives meaningless scores; configure a real provider "
            "or pass --allow-fake to check the pipeline only.",
            file=sys.stderr,
        )
        return 2
    model = f"{settings.llm_provider}:{settings.llm_model}"
    result = asyncio.run(
        evaluate(
            args.dataset,
            limit=args.limit or None,
            seed=args.seed,
            concurrency=args.concurrency,
            out=args.out,
            llm=build_llm_provider(settings),
            model=model,
        )
    )
    print(to_markdown(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
