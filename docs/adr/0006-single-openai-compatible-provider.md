# 0006. One OpenAI-compatible provider for OpenAI, Azure OpenAI and Ollama

- **Status:** Accepted
- **Date:** 2026-10-28

## Context

The project targets three backends: OpenAI, Azure OpenAI (what many companies are allowed to use), and Ollama (local, free, private). All three expose the OpenAI chat-completions and embeddings API: Azure natively, Ollama under `/v1`.

## Decision

Implement `LLMProvider` and `EmbeddingProvider` once, on top of the official `openai` SDK (`app/llm/openai_compatible.py`). The factory only differs in how it builds the client (`AsyncOpenAI`, `AsyncAzureOpenAI`, or `AsyncOpenAI` with Ollama's base URL). Structured output uses the SDK's `parse()` with a strict JSON schema generated from Pydantic. Transport retries (429, 5xx, timeouts) use the SDK's built-in exponential backoff. Chat and embeddings can be configured with different providers.

## Alternatives considered

- **One class per provider** – three copies of the same request/response handling, and three places for bugs.
- **LiteLLM** – covers many more providers, but adds a large dependency and an abstraction we would need to trace through, for backends we don't need (see ADR 0002).

## Consequences

- One code path to test; tests run against a mocked HTTP transport and check the request shape for each backend.
- Ollama's structured-output support depends on the model; small local models may fail strict schemas more often, which shows up as extraction errors (and retries) rather than bad data.
- The embedding dimension is fixed by the schema (1536). OpenAI's `text-embedding-3-*` can be asked for any size; other models (e.g. Ollama's `nomic-embed-text`, 768-d) need a migration or a different `EMBEDDING_PROVIDER`.
- A non-OpenAI-compatible provider (e.g. a native SDK) would need its own implementation of the two protocols; business code is unaffected.
