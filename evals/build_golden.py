"""Build a retrieval golden set from a dataset's ground truth.

    uv run python -m evals.build_golden synthetic/datasets/telecom-seed42-n500

Each question comes with search filters and the set of conversations that are
relevant to it, derived from the labels the generator decided (ADR 0004), so no
manual annotation is needed. Question types:

* reason_month  - "What did customers say about <reason> in <month>?"
* competitor    - "What did customers say about <competitor>?"
* event         - one hand-written question per injected event
* churn_month   - "Why did customers cancel in <month>?"

Questions whose relevant set is smaller than `min_relevant` are skipped: one
or two relevant conversations make the metrics swing on a single result.
"""

import argparse
import json
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from synthetic.generator import load_scenarios

# Phrased the way an analyst would ask, without copying the injected phrases.
EVENT_QUESTIONS = {
    "price_increase_2026_09": "Why are customers upset about their bills after the price change?",
    "network_outage_2026_10": "What happened with the network outage in mid October?",
    "app_outage_2026_08": "Why couldn't customers log in to the app in August?",
    "fee_increase_2026_10": "How did customers react to the new monthly fees?",
    "carrier_delays_2026_11": "Why were Black Friday orders delayed?",
}


@dataclass(frozen=True)
class GoldenQuestion:
    id: str
    type: str
    question: str
    industry: str
    start: str | None
    end: str | None
    relevant: list[str]


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def _month_bounds(month: str) -> tuple[str, str]:
    year, mon = map(int, month.split("-"))
    nxt = date(year + mon // 12, mon % 12 + 1, 1)
    return f"{month}-01T00:00:00Z", f"{nxt.isoformat()}T00:00:00Z"


def _month_name(month: str) -> str:
    return datetime.strptime(month, "%Y-%m").strftime("%B")


def build(dataset: Path, *, min_relevant: int = 3) -> list[GoldenQuestion]:
    manifest = json.loads((dataset / "manifest.json").read_text())
    industry: str = manifest["industry"]
    config = load_scenarios()[0].industries[industry]
    started = {
        c["external_id"]: c["started_at"][:7] for c in _read_jsonl(dataset / "conversations.jsonl")
    }
    truth = _read_jsonl(dataset / "ground_truth.jsonl")

    by_reason_month: dict[tuple[str, str], list[str]] = defaultdict(list)
    by_competitor: dict[str, list[str]] = defaultdict(list)
    by_event: dict[str, list[str]] = defaultdict(list)
    churn_by_month: dict[str, list[str]] = defaultdict(list)
    for t in truth:
        cid, month = t["external_id"], started[t["external_id"]]
        by_reason_month[(t["contact_reason"], month)].append(cid)
        for competitor in t["competitors"]:
            by_competitor[competitor].append(cid)
        if t["event_id"]:
            by_event[t["event_id"]].append(cid)
        if t["churned"]:
            churn_by_month[month].append(cid)

    def questions() -> Iterator[GoldenQuestion]:
        for (reason_id, month), ids in sorted(by_reason_month.items()):
            start, end = _month_bounds(month)
            description = config.reason(reason_id).description.lower()
            yield GoldenQuestion(
                id=f"reason_month:{reason_id}:{month}",
                type="reason_month",
                question=f"What did customers say about {description} in {_month_name(month)}?",
                industry=industry,
                start=start,
                end=end,
                relevant=sorted(ids),
            )
        for competitor, ids in sorted(by_competitor.items()):
            yield GoldenQuestion(
                id=f"competitor:{competitor}",
                type="competitor",
                question=f"What did customers say about {competitor}?",
                industry=industry,
                start=None,
                end=None,
                relevant=sorted(ids),
            )
        events = {e.id: e for e in config.events}
        for event_id, ids in sorted(by_event.items()):
            event = events[event_id]
            yield GoldenQuestion(
                id=f"event:{event_id}",
                type="event",
                question=EVENT_QUESTIONS[event_id],
                industry=industry,
                start=f"{event.start.isoformat()}T00:00:00Z",
                end=None,
                relevant=sorted(ids),
            )
        for month, ids in sorted(churn_by_month.items()):
            start, end = _month_bounds(month)
            yield GoldenQuestion(
                id=f"churn_month:{month}",
                type="churn_month",
                question=f"Why did customers cancel in {_month_name(month)}?",
                industry=industry,
                start=start,
                end=end,
                relevant=sorted(ids),
            )

    return [q for q in questions() if len(q.relevant) >= min_relevant]


def write(questions: list[GoldenQuestion], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for q in questions:
            f.write(json.dumps(asdict(q), ensure_ascii=False) + "\n")


def default_path(dataset: Path) -> Path:
    return Path("evals/datasets") / f"golden-{dataset.name}.jsonl"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a retrieval golden set from ground truth.")
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--min-relevant", type=int, default=3)
    args = parser.parse_args(argv)
    questions = build(args.dataset, min_relevant=args.min_relevant)
    out = args.out or default_path(args.dataset)
    write(questions, out)
    counts: dict[str, int] = defaultdict(int)
    for q in questions:
        counts[q.type] += 1
    print(f"wrote {len(questions)} questions to {out}: {dict(counts)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
