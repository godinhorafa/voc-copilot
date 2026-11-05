"""Seed a database for a demo without an LLM.

    uv run python -m scripts.demo_seed --database-url postgresql+asyncpg://...

Ingests the committed dataset in-process with the offline embeddings, and
uses the generator's ground-truth labels as the "extraction", so the Insights
dashboard shows the real patterns in the data (the September price increase,
the October outage...) instead of placeholder values. Every insight it writes
has `model = "ground-truth-oracle"`, so this provenance stays visible in the
API and the UI. Not for evaluating anything: real extraction is measured by
`make eval-extraction`.
"""

import argparse
import asyncio
import sys
from collections.abc import Sequence
from pathlib import Path

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import get_settings
from app.db.session import create_sessionmaker
from app.ingestion.extraction import format_transcript
from app.llm.base import Message
from app.llm.fake import FakeLLMProvider
from evals.ingest import ingest_dataset, read_jsonl

ROOT = Path(__file__).parents[1]
DATASET = ROOT / "synthetic" / "datasets" / "telecom-seed42-n500"
ORACLE_MODEL = "ground-truth-oracle"


def oracle(dataset: Path) -> FakeLLMProvider:
    """A provider that answers extraction requests with the ground-truth labels."""
    truth = {t["external_id"]: t for t in read_jsonl(dataset / "ground_truth.jsonl")}
    by_transcript = {
        format_transcript([(t["speaker"], t["text"]) for t in c["turns"]]): truth[c["external_id"]]
        for c in read_jsonl(dataset / "conversations.jsonl")
    }

    def answer(schema: type[BaseModel], messages: Sequence[Message]) -> BaseModel:
        transcript = messages[-1].content.split("Conversation:\n", 1)[1]
        labels = by_transcript[transcript]
        summary = f"{labels['contact_reason'].replace('_', ' ').capitalize()}, " + (
            "resolved." if labels["resolved"] else "not resolved."
        )
        return schema.model_validate(labels | {"summary": summary})

    return FakeLLMProvider(structured=answer, model=ORACLE_MODEL)


async def seed(database_url: str, dataset: Path) -> int:
    engine = create_async_engine(database_url)
    try:
        return await ingest_dataset(create_sessionmaker(engine), dataset, llm=oracle(dataset))
    finally:
        await engine.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Seed demo data with ground-truth insights.")
    parser.add_argument("--database-url", default=None, help="defaults to DATABASE_URL")
    parser.add_argument("--dataset", type=Path, default=DATASET)
    args = parser.parse_args(argv)
    url = args.database_url or str(get_settings().database_url)
    processed = asyncio.run(seed(url, args.dataset))
    print(f"processed {processed} conversations from {args.dataset.name}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
