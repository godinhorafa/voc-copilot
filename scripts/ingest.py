"""Upload a generated dataset to the API and wait for processing.

    uv run python -m scripts.ingest synthetic/datasets/telecom-seed42-n500

Sends customers.jsonl and conversations.jsonl. Never reads ground_truth.jsonl:
labels are for evals only and must not reach the system under test.
"""

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import httpx

TERMINAL = {"succeeded", "partial", "failed"}


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def _batches(items: list[dict[str, Any]], size: int) -> list[list[dict[str, Any]]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


async def _wait_for_jobs(
    client: httpx.AsyncClient, job_ids: list[str], timeout_s: float
) -> dict[str, dict[str, Any]]:
    deadline = time.monotonic() + timeout_s
    jobs: dict[str, dict[str, Any]] = {}
    pending = set(job_ids)
    while pending and time.monotonic() < deadline:
        for job_id in list(pending):
            response = await client.get(f"/v1/jobs/{job_id}")
            response.raise_for_status()
            jobs[job_id] = response.json()
            if jobs[job_id]["status"] in TERMINAL:
                pending.discard(job_id)
        if pending:
            await asyncio.sleep(1.0)
    return jobs


async def ingest(
    dataset: Path, *, url: str, api_key: str, batch_size: int, wait: bool, timeout_s: float
) -> int:
    async with httpx.AsyncClient(
        base_url=url, headers={"X-API-Key": api_key}, timeout=60.0
    ) as client:
        customers_path = dataset / "customers.jsonl"
        if customers_path.exists():
            customers = _read_jsonl(customers_path)
            for chunk in _batches(customers, 1000):
                (await client.put("/v1/customers", json={"customers": chunk})).raise_for_status()
            print(f"customers: {len(customers)} upserted", file=sys.stderr)

        conversations = _read_jsonl(dataset / "conversations.jsonl")
        totals = {"created": 0, "updated": 0, "unchanged": 0, "queued": 0}
        job_ids: list[str] = []
        for chunk in _batches(conversations, batch_size):
            response = await client.post("/v1/conversations", json={"conversations": chunk})
            response.raise_for_status()
            body = response.json()
            for key in totals:
                totals[key] += body[key]
            if body["job_id"]:
                job_ids.append(body["job_id"])
        print(f"conversations: {totals}, jobs: {len(job_ids)}", file=sys.stderr)

        if not wait or not job_ids:
            return 0
        jobs = await _wait_for_jobs(client, job_ids, timeout_s)
        by_status: dict[str, int] = {}
        for job in jobs.values():
            by_status[job["status"]] = by_status.get(job["status"], 0) + 1
        failed = sum(job.get("failed", 0) for job in jobs.values())
        unfinished = len(job_ids) - sum(by_status.get(s, 0) for s in TERMINAL)
        print(
            f"jobs: {by_status}, failed conversations: {failed}, unfinished jobs: {unfinished}",
            file=sys.stderr,
        )
        return 1 if failed or unfinished else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Upload a generated dataset to the API.")
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--url", default=os.environ.get("VOC_API_URL", "http://localhost:8000"))
    parser.add_argument("--api-key", default=os.environ.get("API_KEY", "change-me"))
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--no-wait", dest="wait", action="store_false")
    parser.add_argument("--timeout", type=float, default=600.0, help="seconds to wait for jobs")
    args = parser.parse_args(argv)
    return asyncio.run(
        ingest(
            args.dataset,
            url=args.url,
            api_key=args.api_key,
            batch_size=args.batch_size,
            wait=args.wait,
            timeout_s=args.timeout,
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
