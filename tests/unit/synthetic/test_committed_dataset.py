from pathlib import Path

from synthetic.generator import generate
from synthetic.schema import Manifest

DATASET = Path(__file__).parents[3] / "synthetic" / "datasets" / "telecom-seed42-n500"
FILES = ("conversations.jsonl", "ground_truth.jsonl", "customers.jsonl", "manifest.json")


async def test_committed_dataset_is_up_to_date(tmp_path: Path) -> None:
    """Fails when scenarios.yaml or the generator changed without `make dataset`."""
    committed = Manifest.model_validate_json((DATASET / "manifest.json").read_text())
    await generate(
        industry=committed.industry,
        n=committed.n_conversations,
        seed=committed.seed,
        start=committed.start,
        end=committed.end,
        out=tmp_path,
    )
    stale = [f for f in FILES if (tmp_path / f).read_bytes() != (DATASET / f).read_bytes()]
    assert not stale, f"committed dataset is stale ({stale}); run `make dataset`"
