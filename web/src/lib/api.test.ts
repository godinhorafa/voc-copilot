import { afterEach, describe, expect, it, vi } from 'vitest'

import { ApiError, askStream } from './api'

afterEach(() => vi.unstubAllGlobals())

describe('askStream', () => {
  it('explains rate limiting using Retry-After', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response('{}', { status: 429, headers: { 'Retry-After': '17' } })),
    )
    const run = async () => {
      for await (const event of askStream({ question: 'Why?' })) void event
    }
    await expect(run()).rejects.toThrow(new ApiError(429, "You're asking faster than the rate limit allows. Try again in 17 s."))
  })

  it('yields parsed events', async () => {
    const body = 'event: tool_start\ndata: {"type":"tool_start","tool":"query_metrics","arguments":{}}\n\n'
    vi.stubGlobal('fetch', vi.fn(async () => new Response(body, { status: 200 })))
    const events = []
    for await (const event of askStream({ question: 'Why?' })) events.push(event)
    expect(events).toEqual([{ type: 'tool_start', tool: 'query_metrics', arguments: {} }])
  })
})
