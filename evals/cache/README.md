# Recorded LLM responses

LLM-based evals (extraction, text-to-SQL, agent) replay responses stored here, so they run in CI without an API key, at no cost, and deterministically. Each file is JSONL keyed by a hash of the full request (model, messages, schema or tools, temperature): change a prompt and its old responses no longer match, and the eval fails with a request to re-record instead of passing on stale data.

Record with a real provider (costs a few cents with `gpt-4o-mini`):

```bash
LLM_PROVIDER=openai OPENAI_API_KEY=... make evals-record
```

Then review the new numbers, run `make evals-baseline` if they should become the new baseline, and commit both `evals/cache/` and `evals/baselines.json`.

`manifest.json` records which model each eval was recorded with.
