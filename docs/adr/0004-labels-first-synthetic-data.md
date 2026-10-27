# 0004. Labels-first synthetic data

- **Status:** Accepted
- **Date:** 2026-10-27

## Context

Every claim this project makes (extraction accuracy, retrieval recall, answer faithfulness, "cancellations spiked because of the price change") needs a gold standard. Real customer service data is unavailable (PII, contracts) and unlabeled. Hand-labeling synthetic conversations after the fact is slow and inconsistent.

## Decision

Generate data in two steps. First, a seeded simulator samples the **labels** for each conversation (customer, timestamp, contact reason, outcome, sentiment, competitor mentions, churn, and the dated event behind it). Then a renderer writes text that matches those labels. Labels are written to `ground_truth.jsonl`, separate from the ingestion payload.

A consistency check runs on every rendered conversation (both speakers present, every labeled competitor mentioned, no unlabeled competitor mentioned). The LLM renderer retries with the violations as feedback and fails loudly after N attempts.

## Alternatives considered

- **Generate text freely with an LLM, then label it with an LLM** – the labels would come from the same kind of model we are trying to evaluate, which makes the evaluation circular.
- **Use a public dataset** – few have multi-turn transcripts with outcomes, churn, and a timeline; none has known causal events to ask "why" about.
- **Hand-written conversations** – realistic but too few to measure anything.

## Consequences

- Questions have known answers: we injected the September price increase, so we can check whether the agent finds it.
- Extraction can be scored per field against ground truth without manual annotation.
- Labels can only be as rich as the simulator. Subtle signals (sarcasm, mixed reasons) are under-represented, so metrics are optimistic compared to real data.
- Template-rendered text is formulaic and flatters keyword search. Retrieval evals must use LLM-rendered data.
