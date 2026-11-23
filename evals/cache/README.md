# Recorded LLM responses

LLM-based evals (extraction, text-to-SQL, agent) replay responses stored here, so they run in CI without an API key, at no cost, and deterministically. Each file is JSONL keyed by a hash of the full request (model, messages, schema or tools, temperature): change a prompt and its old responses no longer match, and the eval fails with a request to re-record instead of passing on stale data.

Record with a real provider (costs a few cents with `gpt-4o-mini`):

```bash
LLM_PROVIDER=openai OPENAI_API_KEY=... make evals-record
```

On Windows PowerShell, set the variables with `$env:LLM_PROVIDER = "openai"` and `$env:OPENAI_API_KEY = "..."`, then run `uv run python -m evals.ci --database-url <url> --record`.

Recording reuses responses already stored, so a run that was interrupted resumes without paying for the same calls twice. If the provider refuses a request in a way retrying won't fix (no credits, invalid key, no access to the model, provider unreachable), the run stops at the first refusal and prints the reason in one line.

Then review the new numbers, run `make evals-baseline` if they should become the new baseline, and commit both `evals/cache/` and `evals/baselines.json`.

`manifest.json` records which model each eval was recorded with. An eval is added to it only once its recording finishes, so CI never replays a partial one.
