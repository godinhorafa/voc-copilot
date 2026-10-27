# 0005. Idempotent ingestion with whole-job retries

- **Status:** Accepted
- **Date:** 2026-10-27

## Context

Uploads come in batches from external systems that will retry on timeouts and resend data. Processing calls an embedding provider (and, from phase 2b, an LLM) that times out and rate-limits. We need at-least-once processing without duplicate chunks, and a way to recover from a lost queue message.

## Decision

- **Upload is an upsert keyed by `external_id`**, with a SHA-256 hash of the normalized content. Unchanged and already-processed conversations are a no-op; changed ones are replaced and reprocessed.
- **Processing state lives on the conversation** (`processed_hash == content_hash`), not on the job. Processing a conversation replaces its chunks, so running it twice is harmless.
- **A job is a list of conversation ids.** Any exception retries the whole job with linear backoff (arq `Retry`); finished conversations are skipped on retry. On the last attempt, failures are recorded per conversation and the job ends `partial` or `failed`. Job counters are recomputed from conversation state, never incremented.
- **Enqueue happens after commit.** If enqueueing fails, the API returns `503`; re-uploading the same batch queues whatever is still unprocessed.

## Alternatives considered

- **Transactional outbox** – guarantees enqueue after commit without client involvement, at the cost of a relay process. Not needed while re-upload is a valid recovery path; revisit if clients can't retry.
- **One queue message per conversation** – finer retries and parallelism, but 500 messages per upload and harder job-level status. Revisit if jobs become slow enough to need parallel workers per job.
- **Per-conversation retry inside the task** – hides provider outages inside a long-running task; whole-job retry with backoff gives the provider time to recover.

## Consequences

- Clients can retry blindly; at worst they get `unchanged` counts.
- A provider outage delays a job but doesn't lose work.
- Concurrent uploads of the same new `external_id` can collide on the unique constraint; the API returns `409` and the client retries.
- A job stuck in `queued` (enqueue failed and the client never retried) needs a re-upload; there's no sweeper yet.
