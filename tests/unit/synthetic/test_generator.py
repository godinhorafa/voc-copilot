import json
from pathlib import Path

import pytest

from synthetic.generator import generate, main
from synthetic.schema import Conversation, GroundTruth, Manifest

FILES = ("conversations.jsonl", "ground_truth.jsonl", "customers.jsonl", "manifest.json")


async def test_writes_consistent_dataset(tmp_path: Path) -> None:
    manifest = await generate(industry="banking", n=120, seed=3, out=tmp_path)

    conversations = [
        Conversation.model_validate_json(line)
        for line in (tmp_path / "conversations.jsonl").read_text().splitlines()
    ]
    truth = [
        GroundTruth.model_validate_json(line)
        for line in (tmp_path / "ground_truth.jsonl").read_text().splitlines()
    ]
    assert len(conversations) == len(truth) == manifest.n_conversations == 120
    assert [c.external_id for c in conversations] == [t.external_id for t in truth]
    assert Manifest.model_validate_json((tmp_path / "manifest.json").read_text()) == manifest
    assert manifest.renderer == "template"


async def test_ingestion_payload_has_no_labels(tmp_path: Path) -> None:
    await generate(industry="telecom", n=20, seed=1, out=tmp_path)
    record = json.loads((tmp_path / "conversations.jsonl").read_text().splitlines()[0])
    label_fields = set(GroundTruth.model_fields) - {"external_id"}
    assert not label_fields & set(record)


async def test_output_is_byte_identical_across_runs(tmp_path: Path) -> None:
    await generate(industry="ecommerce", n=80, seed=5, out=tmp_path / "a")
    await generate(industry="ecommerce", n=80, seed=5, out=tmp_path / "b")
    for name in FILES:
        assert (tmp_path / "a" / name).read_bytes() == (tmp_path / "b" / name).read_bytes()


async def test_unknown_industry(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unknown industry"):
        await generate(industry="aviation", n=10, seed=1, out=tmp_path)


def test_cli(tmp_path: Path) -> None:
    out = tmp_path / "cli"
    assert main(["--industry", "telecom", "--n", "15", "--seed", "2", "--out", str(out)]) == 0
    assert all((out / name).exists() for name in FILES)
