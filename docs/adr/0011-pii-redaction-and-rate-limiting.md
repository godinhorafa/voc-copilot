# 0011. PII redaction at ingestion and per-client rate limits

- **Status:** Accepted
- **Date:** 2026-11-08

## Context

Real transcripts contain personal data: emails, phone numbers, card numbers, national IDs. Without redaction, that data would be stored in the database, embedded, sent to the LLM provider, and shown back in answers and the UI. Separately, `/v1/ask` and `/v1/metrics` each trigger several LLM calls, so one client in a loop can run up cost or exhaust provider quota.

## Decision

**Redact at the ingestion boundary, before anything is stored.** `app/privacy/redaction.py` replaces PII in turns before they are written. Originals never reach the database, the embeddings, the LLM or the UI.
- **Detectors** are deterministic: a pattern plus a validator (Luhn for cards, mod-97 for IBAN, check digits for CPF, valid ranges for SSN and IPv4).
- **Look-alikes survive:** prices, plan codes, error codes, dates and versions are left intact.
- **Catch-all:** long digit runs that fail every validator are still redacted, as `NUMBER`. A mistyped ID is usually the real one.
- **Placeholders** are typed and stable within a conversation (`[EMAIL_1]` every time the same address appears), so retrieval and extraction still see who said what.
- **Idempotency:** the content hash is computed on the original payload, so re-uploads are still recognized as unchanged.
- **Audit:** counts per kind are stored on the conversation and shown in the UI. Values are never stored.

**Rate limit per client on expensive endpoints.**
- **Mechanism:** a fixed window in Redis, keyed by a hash of the API key plus the client IP. The first `X-Forwarded-For` hop is trusted only when configured, as behind the web UI's nginx.
- **Defaults:** ask 20/minute, metrics 30/minute, ingest 120/minute.
- **Response:** `429` with `Retry-After` and `RateLimit-*` headers; the UI explains the wait.
- **Redis outage:** the limiter fails open and logs a warning. It protects spend, and failing closed would turn a cache outage into an API outage.

## Alternatives considered

- **Presidio / NER-based detection** – finds names and addresses, which regexes cannot. It costs a spaCy model in the image, slower ingestion, and false positives on product names. `Redactor` accepts any detector, so an NER detector can be added when names matter.
- **Redact only before the LLM call** – keeps originals in the database for follow-ups, but then PII sits in storage, backups and embeddings. Customer follow-up should go through the CRM using `customer_id`, not the transcript.
- **Reversible tokenization** (vault with original values) – supports re-identification for authorized users, at the cost of a secrets store and access controls. Worth it only with a concrete re-identification need.
- **Sliding-window or token-bucket limits** – smoother, but fixed windows with a 2x worst-case burst at the boundary are enough to cap spend.
- **Rate limiting at nginx** – simpler, but it only covers traffic through the UI proxy and can't see API-key identity.

## Consequences

- Names, street addresses and free-text identifiers are not redacted. This is documented, and the NER path is open.
- Redaction is irreversible: no one can recover the original contact details from VOC Copilot. That is intended.
- Data ingested before migration `0005` is not redacted retroactively; re-upload it.
- Tests: a labeled corpus of positives, look-alikes that must survive, near misses, stable placeholders, zero false positives on the 500 synthetic conversations, and end-to-end checks that storage, chunks and the LLM prompt are redacted. Rate limiting is tested with a controlled clock, per-endpoint groups, headers and fail-open.
