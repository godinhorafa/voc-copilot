# Committed datasets

Small, reproducible datasets for tests and evals. Each directory is the output of `synthetic.generator`; see `manifest.json` for the exact parameters and the hash of the scenario config used.

| Directory               | Industry | Conversations | Seed | Renderer |
|-------------------------|----------|---------------|------|----------|
| `telecom-seed42-n500/`  | telecom  | 500           | 42   | template |

Files:

- `conversations.jsonl` – ingestion payloads. This is the only file the system under test may read.
- `ground_truth.jsonl` – labels per conversation (reason, sentiment, outcome, churn, competitors, event). Evals only.
- `customers.jsonl` – customer timeline, including churn date and reason.
- `manifest.json` – generation parameters.

`tests/unit/synthetic/test_committed_dataset.py` regenerates `telecom-seed42-n500` and fails if it drifted. After changing `scenarios.yaml` or the generator, run `make dataset` and commit the result.

Template-rendered text is formulaic: good enough to exercise the pipeline and the label-driven evals, but it makes keyword retrieval look better than it will on natural text. Once a real LLM provider is wired (phase 2), an LLM-rendered dataset will be added for retrieval and answer evals.
