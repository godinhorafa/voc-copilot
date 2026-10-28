import json
from pathlib import Path
from typing import Any

import pytest

from evals.build_golden import _month_bounds, build, main

DATASET = Path(__file__).parents[3] / "synthetic" / "datasets" / "telecom-seed42-n500"
COMMITTED = Path(__file__).parents[3] / "evals" / "datasets" / "golden-telecom-seed42-n500.jsonl"


def _truth() -> dict[str, dict[str, Any]]:
    lines = (DATASET / "ground_truth.jsonl").read_text().splitlines()
    return {t["external_id"]: t for t in map(json.loads, lines)}


def test_relevant_sets_agree_with_ground_truth() -> None:
    truth = _truth()
    questions = build(DATASET)
    assert {q.type for q in questions} == {"reason_month", "competitor", "event", "churn_month"}
    for q in questions:
        assert len(q.relevant) >= 3
        labels = [truth[cid] for cid in q.relevant]
        if q.type == "reason_month":
            reason = q.id.split(":")[1]
            assert all(t["contact_reason"] == reason for t in labels)
        elif q.type == "competitor":
            competitor = q.id.split(":", 1)[1]
            assert all(competitor in t["competitors"] for t in labels)
        elif q.type == "event":
            assert all(t["event_id"] == q.id.split(":")[1] for t in labels)
        else:
            assert all(t["churned"] for t in labels)


def test_competitor_sets_are_complete() -> None:
    truth = _truth()
    by_id = {q.id: set(q.relevant) for q in build(DATASET)}
    expected = {cid for cid, t in truth.items() if "NovaTel" in t["competitors"]}
    assert by_id["competitor:NovaTel"] == expected


def test_min_relevant_filters_small_questions() -> None:
    assert len(build(DATASET, min_relevant=20)) < len(build(DATASET))


@pytest.mark.parametrize(
    ("month", "bounds"),
    [
        ("2026-09", ("2026-09-01T00:00:00Z", "2026-10-01T00:00:00Z")),
        ("2026-12", ("2026-12-01T00:00:00Z", "2027-01-01T00:00:00Z")),
    ],
)
def test_month_bounds(month: str, bounds: tuple[str, str]) -> None:
    assert _month_bounds(month) == bounds


def test_committed_golden_set_is_up_to_date(tmp_path: Path) -> None:
    out = tmp_path / "golden.jsonl"
    assert main([str(DATASET), "--out", str(out)]) == 0
    assert out.read_bytes() == COMMITTED.read_bytes(), "run `make golden`"
