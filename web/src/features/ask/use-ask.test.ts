import { describe, expect, it } from 'vitest'

import { periodBounds } from '@/lib/filters-context'
import type { AskResponse } from '@/lib/types'

import { applyEvent, type Exchange } from './use-ask'

const start: Exchange = { id: 1, request: { question: 'Why?' }, status: 'running', steps: [] }

describe('applyEvent', () => {
  it('tracks tool steps and closes the latest running one', () => {
    let e = applyEvent(start, { type: 'tool_start', tool: 'search_conversations', arguments: { query: 'a' } })
    e = applyEvent(e, { type: 'tool_start', tool: 'search_conversations', arguments: { query: 'b' } })
    e = applyEvent(e, { type: 'tool_end', tool: 'search_conversations', summary: '[S1] …', error: false })
    expect(e.steps.map((s) => [s.arguments.query, s.status])).toEqual([
      ['a', 'running'],
      ['b', 'done'],
    ])
  })

  it('marks failed tools', () => {
    let e = applyEvent(start, { type: 'tool_start', tool: 'query_metrics', arguments: {} })
    e = applyEvent(e, { type: 'tool_end', tool: 'query_metrics', error: true })
    expect(e.steps[0].status).toBe('error')
  })

  it('finishes with an answer or an error', () => {
    const data = { answer: 'ok' } as AskResponse
    expect(applyEvent(start, { type: 'answer', data })).toMatchObject({ status: 'done', response: data })
    expect(applyEvent(start, { type: 'error', message: 'boom' })).toMatchObject({
      status: 'error',
      error: 'boom',
    })
  })
})

describe('periodBounds', () => {
  it('maps a month to an exclusive range, across year ends', () => {
    expect(periodBounds('2026-09')).toEqual({ start: '2026-09-01', end: '2026-10-01' })
    expect(periodBounds('2026-12')).toEqual({ start: '2026-12-01', end: '2027-01-01' })
    expect(periodBounds('all')).toEqual({})
  })
})
