import { readSSE } from '@/lib/sse'
import type { AskEvent, AskRequest, Conversation, InsightsResponse } from '@/lib/types'

// Same-origin: the dev server (vite.config.ts) or nginx proxies /api to FastAPI
// and adds the API key, so it never ships to the browser.
const BASE = '/api/v1'

export class ApiError extends Error {
  status: number

  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${BASE}${path}`, init)
  if (!response.ok) {
    let detail = response.statusText
    try {
      const body = (await response.json()) as { detail?: unknown }
      detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail)
    } catch {
      // keep statusText
    }
    throw new ApiError(response.status, detail)
  }
  return (await response.json()) as T
}

export function getInsights(
  params: { industry?: string; start?: string; end?: string },
  signal?: AbortSignal,
) {
  const query = new URLSearchParams(
    Object.entries(params).filter((entry): entry is [string, string] => Boolean(entry[1])),
  )
  return request<InsightsResponse>(`/insights?${query}`, { signal })
}

export function getConversation(externalId: string, signal?: AbortSignal) {
  return request<Conversation>(`/conversations/${encodeURIComponent(externalId)}`, { signal })
}

/** Ask the agent and yield progress events, then the answer (or an error). */
export async function* askStream(
  body: AskRequest,
  signal?: AbortSignal,
): AsyncGenerator<AskEvent> {
  const response = await fetch(`${BASE}/ask?stream=true`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    signal,
  })
  if (response.status === 429) {
    const wait = response.headers.get('Retry-After')
    throw new ApiError(
      429,
      `You're asking faster than the rate limit allows. Try again${wait ? ` in ${wait} s` : ' shortly'}.`,
    )
  }
  if (!response.ok || !response.body) {
    throw new ApiError(response.status, `The question could not be sent (${response.status}).`)
  }
  for await (const message of readSSE(response.body)) {
    yield JSON.parse(message.data) as AskEvent
  }
}
