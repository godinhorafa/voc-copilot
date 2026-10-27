"""Synthetic dataset generator (CLI).

    uv run python -m synthetic.generator --industry telecom --n 500 --seed 42 --out data/telecom

Writes a dataset directory:

    conversations.jsonl   ingestion payloads (what the API receives)
    ground_truth.jsonl    labels per conversation, for evals only
    customers.jsonl       customer timeline, including churn
    manifest.json         seed, period, renderer, and a hash of the scenario config

The same arguments always produce byte-identical files with the template
renderer. The LLM renderer is not deterministic, so its datasets are meant to
be generated once and committed or stored.
"""

import argparse
import asyncio
import hashlib
import sys
from collections.abc import Iterable
from datetime import date
from pathlib import Path

import yaml
from pydantic import BaseModel

from synthetic.render import LLMRenderer, Renderer, TemplateRenderer
from synthetic.schema import Conversation, Customer, Manifest, Scenarios
from synthetic.simulation import ConversationSpec, simulate

GENERATOR_VERSION = "1"
DEFAULT_SCENARIOS = Path(__file__).parent / "scenarios.yaml"
DEFAULT_START = date(2026, 6, 1)
DEFAULT_END = date(2026, 11, 30)


def load_scenarios(path: Path = DEFAULT_SCENARIOS) -> tuple[Scenarios, str]:
    raw = path.read_bytes()
    return Scenarios.model_validate(yaml.safe_load(raw)), hashlib.sha256(raw).hexdigest()


def build_renderer(kind: str, scenarios: Scenarios, industry: str) -> Renderer:
    competitors = scenarios.industries[industry].competitors
    if kind == "template":
        return TemplateRenderer(scenarios.common, competitors)
    if kind == "llm":
        from app.core.config import get_settings
        from app.llm.factory import build_llm_provider

        return LLMRenderer(build_llm_provider(get_settings()), competitors)
    raise ValueError(f"unknown renderer {kind!r}")


async def render_all(
    renderer: Renderer, specs: list[ConversationSpec], *, concurrency: int
) -> list[Conversation]:
    semaphore = asyncio.Semaphore(concurrency)

    async def one(spec: ConversationSpec) -> Conversation:
        async with semaphore:
            turns = await renderer.render(spec)
        return Conversation(
            external_id=spec.external_id,
            customer_id=spec.customer_id,
            industry=spec.industry,
            channel=spec.channel,
            started_at=spec.started_at,
            turns=turns,
        )

    # gather preserves input order, so output order is stable under concurrency.
    return list(await asyncio.gather(*(one(s) for s in specs)))


def _write_jsonl(path: Path, records: Iterable[BaseModel]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for record in records:
            f.write(record.model_dump_json() + "\n")


def write_dataset(
    out: Path,
    manifest: Manifest,
    conversations: list[Conversation],
    specs: list[ConversationSpec],
    customers: list[Customer],
) -> None:
    out.mkdir(parents=True, exist_ok=True)
    _write_jsonl(out / "conversations.jsonl", conversations)
    _write_jsonl(out / "ground_truth.jsonl", (s.ground_truth() for s in specs))
    _write_jsonl(out / "customers.jsonl", customers)
    (out / "manifest.json").write_text(manifest.model_dump_json(indent=2) + "\n")


async def generate(
    *,
    industry: str,
    n: int,
    seed: int,
    out: Path,
    start: date = DEFAULT_START,
    end: date = DEFAULT_END,
    renderer_kind: str = "template",
    scenarios_path: Path = DEFAULT_SCENARIOS,
    concurrency: int = 8,
) -> Manifest:
    scenarios, scenario_sha = load_scenarios(scenarios_path)
    if industry not in scenarios.industries:
        raise ValueError(
            f"unknown industry {industry!r}; choose from {sorted(scenarios.industries)}"
        )

    sim = simulate(
        scenarios.industries[industry], industry=industry, n=n, start=start, end=end, seed=seed
    )
    renderer = build_renderer(renderer_kind, scenarios, industry)
    conversations = await render_all(renderer, sim.specs, concurrency=concurrency)

    manifest = Manifest(
        industry=industry,
        seed=seed,
        start=start,
        end=end,
        n_conversations=len(conversations),
        n_customers=len(sim.customers),
        renderer=renderer.name,
        scenario_sha256=scenario_sha,
        generator_version=GENERATOR_VERSION,
    )

    await asyncio.to_thread(write_dataset, out, manifest, conversations, sim.specs, sim.customers)
    return manifest


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0] if __doc__ else None)
    parser.add_argument("--industry", required=True)
    parser.add_argument("--n", type=int, default=500, help="number of conversations")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--out", type=Path, required=True, help="output directory")
    parser.add_argument("--start", type=date.fromisoformat, default=DEFAULT_START)
    parser.add_argument("--end", type=date.fromisoformat, default=DEFAULT_END)
    parser.add_argument("--renderer", choices=["template", "llm"], default="template")
    parser.add_argument("--scenarios", type=Path, default=DEFAULT_SCENARIOS)
    parser.add_argument("--concurrency", type=int, default=8)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    manifest = asyncio.run(
        generate(
            industry=args.industry,
            n=args.n,
            seed=args.seed,
            out=args.out,
            start=args.start,
            end=args.end,
            renderer_kind=args.renderer,
            scenarios_path=args.scenarios,
            concurrency=args.concurrency,
        )
    )
    print(
        f"wrote {manifest.n_conversations} conversations, {manifest.n_customers} customers "
        f"({manifest.renderer} renderer) to {args.out}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
