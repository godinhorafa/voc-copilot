# 0010. Web UI: Vite + React + shadcn/ui, same-origin API proxy

- **Status:** Accepted
- **Date:** 2026-11-05

## Context

The API needed a face: analysts ask questions, follow the agent's progress, check citations against the original conversation, and scan trends. The API is protected by a key that must not reach a browser, and `/v1/ask?stream=true` streams server-sent events over a POST.

## Decision

- **Stack.** A single-page app in `web/` with Vite, React and TypeScript, and shadcn/ui on Radix and Tailwind for accessible, themeable components (light and dark). Charts use recharts through shadcn's chart wrapper. The insights page is lazy-loaded so the charting library stays out of the first load.
- **No key in the browser.** The UI calls same-origin `/api/*`. In development the Vite dev server proxies it to FastAPI and adds `X-API-Key`; in Docker, nginx does the same, from the `API_KEY` environment variable. Response buffering is off, so SSE progress arrives as it happens.
- **Streaming over fetch.** `EventSource` cannot POST, so the client reads the response body and parses SSE itself (`src/lib/sse.ts`, unit-tested for events split across chunks and multi-byte characters).
- **Verifiable answers.** `[S#]` and `[M#]` markers in the answer become chips: a source chip shows the quote and opens the conversation with the cited turn highlighted; a metric chip jumps to its table, with the SQL that ran. The API returns each citation's source number for this.
- **Charts follow the dataviz rules.** One series per chart (no dual axis; volume and churn are separate charts), two validated hues for light and dark, a tooltip on every bar, and a chart/table toggle so values aren't color-only.

## Alternatives considered

- **Next.js** – server rendering and API routes are not needed for an internal analytics tool behind a login, and they add a Node server to operate.
- **Streamlit / Gradio** – fastest to build, but limited control over UX (streaming progress, side sheets, citation chips) and harder to make feel like a product.
- **Calling the API from the browser with the key** – simplest, and leaks the key to every user.

## Consequences

- The key protects the API from the network, not users from each other: anyone who can open the UI can ask anything. Per-user authentication (OIDC at the proxy) is the next step for real deployments (roadmap, phase 9).
- Two images to build and deploy (API and web), both checked in CI.
- `make demo` loads the dataset with ground-truth labels as insights (marked `model = "ground-truth-oracle"`), so the UI can be explored without an LLM key.
