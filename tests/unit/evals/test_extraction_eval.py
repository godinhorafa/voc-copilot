import json
from collections.abc import Sequence
from pathlib import Path

import pytest
from pydantic import BaseModel

from app.ingestion.extraction import format_transcript
from app.llm.base import Message
from app.llm.fake import FakeLLMProvider, placeholder_instance
from evals.extraction_eval import evaluate, load_sample, main

DATASET = Path(__file__).parents[3] / "synthetic" / "datasets" / "telecom-seed42-n500"


def _oracle() -> FakeLLMProvider:
    """Answers with the ground truth, found by matching the transcript."""
    by_transcript = {
        format_transcript([(t["speaker"], t["text"]) for t in conv["turns"]]): truth
        for conv, truth in load_sample(DATASET, None, seed=0)
    }

    def answer(schema: type[BaseModel], messages: Sequence[Message]) -> BaseModel:
        transcript = messages[-1].content.split("Conversation:\n", 1)[1]
        return schema.model_validate(by_transcript[transcript] | {"summary": "oracle"})

    return FakeLLMProvider(structured=answer)


def test_sample_is_deterministic_and_aligned() -> None:
    a = load_sample(DATASET, 50, seed=1)
    assert a == load_sample(DATASET, 50, seed=1)
    assert all(c["external_id"] == t["external_id"] for c, t in a)
    assert len(load_sample(DATASET, None, seed=1)) == 500


async def test_oracle_scores_perfectly(tmp_path: Path) -> None:
    result = await evaluate(
        DATASET, limit=60, seed=0, concurrency=4, out=tmp_path, llm=_oracle(), model="oracle"
    )
    assert result.n == 60
    assert result.errors == 0
    assert all(f.accuracy == 1.0 for f in result.fields.values())
    assert result.competitors.f1 == 1.0
    report = (tmp_path / "extraction-telecom-seed42-n500-oracle.md").read_text()
    assert "| contact_reason | 1.000 | 1.000 |" in report
    assert (
        json.loads((tmp_path / "extraction-telecom-seed42-n500-oracle.json").read_text())["n"] == 60
    )


async def test_placeholder_answers_score_near_baseline_and_errors_are_counted(
    tmp_path: Path,
) -> None:
    calls = {"n": 0}

    def flaky(schema: type[BaseModel], messages: Sequence[Message]) -> BaseModel:
        calls["n"] += 1
        if calls["n"] % 10 == 0:
            raise TimeoutError
        return placeholder_instance(schema)

    result = await evaluate(
        DATASET,
        limit=50,
        seed=0,
        concurrency=1,
        out=tmp_path,
        llm=FakeLLMProvider(structured=flaky),
        model="placeholder",
    )
    assert (result.n, result.errors) == (45, 5)
    assert result.fields["contact_reason"].accuracy < 0.5


def test_cli_refuses_fake_provider(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "fake")
    from app.core.config import get_settings

    get_settings.cache_clear()
    try:
        assert main([str(DATASET), "--out", str(tmp_path)]) == 2
    finally:
        get_settings.cache_clear()
