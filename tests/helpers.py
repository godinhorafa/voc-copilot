"""Test helpers that need the database."""

from pathlib import Path

from evals.ingest import ingest_payloads

DATASET = Path(__file__).parents[1] / "synthetic" / "datasets" / "telecom-seed42-n500"

__all__ = ["DATASET", "ingest_payloads"]
