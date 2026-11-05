# VOC Copilot web UI

React single-page app for asking questions (with live agent progress and clickable citations) and browsing insights. Built with Vite, TypeScript, [shadcn/ui](https://ui.shadcn.com) (Radix + Tailwind) and recharts. Design notes: [ADR 0010](../docs/adr/0010-web-ui.md).

## Run

```bash
npm install
npm run dev        # http://localhost:5173, proxies /api to http://localhost:8000
```

The dev server adds `X-API-Key` to `/api` requests from `API_KEY` (default `change-me`); set `VOC_API_URL` to point at another API. In Docker (`make up` at the repo root), nginx serves the build on :3000 and does the same proxying.

## Check

```bash
npm run typecheck
npm run lint
npm test
npm run build
```

## Layout

| Path | What |
|------|------|
| `src/features/ask/` | Chat page, streaming hook (`use-ask.ts`), answer card |
| `src/features/insights/` | Dashboard (lazy-loaded with recharts) |
| `src/components/` | App shell (sidebar, header, filters) and the conversation sheet |
| `src/components/ui/` | shadcn/ui components (generated; add more with `npx shadcn add <name>`) |
| `src/lib/` | API client, SSE parser, citation parsing, filters, formatting |
