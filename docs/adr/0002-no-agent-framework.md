# 0002. No agent framework

- **Status:** Accepted
- **Date:** 2026-10-26

## Context

The agent has two tools (`search_conversations`, `query_metrics`) and a short planning loop. We need to trace every step, count tokens and cost, enforce iteration limits, validate citations, and swap LLM providers (OpenAI, Azure OpenAI, Ollama).

## Decision

Write the tool-calling loop directly against small provider protocols (`app/llm/base.py`). No LangChain, LlamaIndex, or similar.

## Alternatives considered

- **LangChain / LangGraph** – broad integrations and a graph model, but deep abstraction layers make the actual prompts and control flow hard to inspect, and upgrades churn often.
- **LlamaIndex** – strong for retrieval pipelines, but brings its own storage and query abstractions that overlap with ours.
- **Provider SDK agents** – simple, but tie the loop to one vendor.

## Consequences

- The whole loop fits in a few hundred lines that can be read, unit-tested with `FakeLLMProvider`, and traced span by span.
- We implement retries, tool-call parsing, and streaming ourselves.
- Because providers sit behind protocols, adopting a framework later would only touch `app/agent/`. **Revisit** if we need multi-agent coordination or more than ~5 tools with complex routing.
