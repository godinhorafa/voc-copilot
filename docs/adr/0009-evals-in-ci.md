# 0009. Evals in CI: deterministic retrieval, recorded LLM responses, baselines

- **Status:** Accepted
- **Date:** 2026-11-02

## Context

Every phase added an eval, but they only ran by hand, so a regression would merge unnoticed. Running LLM-based evals on every pull request with a live model would make CI depend on a secret, cost money, and be flaky (model outputs vary run to run).

## Decision

`evals/ci.py` runs on every pull request (`.github/workflows/evals.yml`) against a Postgres service container:

1. **Deterministic evals run live.** The committed dataset is ingested in-process with the offline providers, and retrieval is scored for vector, keyword and hybrid.
2. **LLM evals replay recorded responses.** Extraction, text-to-SQL and agent evals use a cache of responses keyed by a hash of the full request (`evals/cache/`, recorded with `make evals-record` and a real provider).
   - A request missing from the cache, such as one from a changed prompt, **fails** the run with an instruction to re-record.
   - With no cache at all, the eval is reported as **skipped**, never as passing.
3. **Baselines gate merges.** Each metric is compared with `evals/baselines.json`; a drop beyond the tolerance (0.01) fails the check. Improvements pass but are only recorded by running `make evals-baseline` and committing the file, so the bar moves on purpose and in review.
4. **The report is visible where reviewers look:** a sticky PR comment, the job summary, and an artifact. The job fails only after reporting.

## Alternatives considered

- **Live LLM calls in CI** – realistic, but costs per push, needs secrets (unavailable to fork PRs), and is non-deterministic, so thresholds would flake.
- **Only unit tests on eval code** – they check that metrics are computed correctly, not whether the system got worse.
- **Exact-match baselines** – results vary slightly between fresh databases: tied scores are broken by chunk id, a random UUID per ingestion, and pgvector's HNSW index assigns random levels while building its graph. Across fresh runs we measured differences up to ±0.002 (e.g. keyword MRR 0.825 vs 0.826). A tolerance of 0.01 absorbs that without hiding real regressions.

## Consequences

- CI catches retrieval regressions today; LLM evals start gating as soon as responses are recorded once with an API key.
- Prompt changes require re-recording (and paying for it) in the same PR, which keeps the cache honest.
- Replayed LLM evals measure the system against fixed model outputs; they detect regressions in our code and prompts, not changes in the provider's model. Re-recording periodically measures the latter.
