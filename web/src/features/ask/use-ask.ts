import { useCallback, useRef, useState } from 'react'

import { askStream } from '@/lib/api'
import type { AskEvent, AskRequest, AskResponse } from '@/lib/types'

export interface Step {
  tool: string
  arguments: Record<string, unknown>
  status: 'running' | 'done' | 'error'
  summary?: string
}

export interface Exchange {
  id: number
  request: AskRequest
  status: 'running' | 'done' | 'error' | 'stopped'
  steps: Step[]
  response?: AskResponse
  error?: string
}

/** Apply one streamed event to an exchange (pure, so it's easy to test). */
export function applyEvent(exchange: Exchange, event: AskEvent): Exchange {
  switch (event.type) {
    case 'tool_start':
      return {
        ...exchange,
        steps: [...exchange.steps, { tool: event.tool, arguments: event.arguments, status: 'running' }],
      }
    case 'tool_end': {
      const steps = [...exchange.steps]
      // Close the most recent running step for this tool.
      for (let i = steps.length - 1; i >= 0; i--) {
        if (steps[i].tool === event.tool && steps[i].status === 'running') {
          steps[i] = { ...steps[i], status: event.error ? 'error' : 'done', summary: event.summary }
          break
        }
      }
      return { ...exchange, steps }
    }
    case 'answer':
      return { ...exchange, status: 'done', response: event.data }
    case 'error':
      return { ...exchange, status: 'error', error: event.message }
  }
}

export function useAsk() {
  const [exchanges, setExchanges] = useState<Exchange[]>([])
  const controller = useRef<AbortController | null>(null)
  const nextId = useRef(1)

  const update = (id: number, fn: (e: Exchange) => Exchange) =>
    setExchanges((all) => all.map((e) => (e.id === id ? fn(e) : e)))

  const ask = useCallback(async (request: AskRequest) => {
    controller.current?.abort()
    const abort = new AbortController()
    controller.current = abort
    const id = nextId.current++
    setExchanges((all) => [...all, { id, request, status: 'running', steps: [] }])
    try {
      for await (const event of askStream(request, abort.signal)) {
        update(id, (e) => applyEvent(e, event))
      }
      // A stream that ends without an answer or error event was cut short.
      update(id, (e) =>
        e.status === 'running'
          ? { ...e, status: 'error', error: 'The connection closed before an answer arrived.' }
          : e,
      )
    } catch (error) {
      if (abort.signal.aborted) {
        update(id, (e) => ({ ...e, status: 'stopped' }))
      } else {
        const message = error instanceof Error ? error.message : String(error)
        update(id, (e) => ({ ...e, status: 'error', error: message }))
      }
    } finally {
      if (controller.current === abort) controller.current = null
    }
  }, [])

  const stop = useCallback(() => controller.current?.abort(), [])
  const reset = useCallback(() => {
    controller.current?.abort()
    setExchanges([])
  }, [])

  const running = exchanges.some((e) => e.status === 'running')
  return { exchanges, ask, stop, reset, running }
}
