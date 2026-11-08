import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { TooltipProvider } from '@/components/ui/tooltip'
import { ConversationSheetContext } from '@/lib/conversation-sheet-context'
import type { AskResponse } from '@/lib/types'

import { AnswerCard } from './answer-card'
import type { Exchange } from './use-ask'

const response: AskResponse = {
  answer: 'Churn tripled in September [M1]. Customers blame the new price [S1].\n\n- Billing [S1]\n- Unknown [S9]',
  citations: [{ source: 1, conversation_id: 'conv_1', excerpt: 'my bill went up' }],
  metrics: [
    {
      id: 'M1',
      question: 'Churn per month',
      sql: 'SELECT 1',
      columns: ['month', 'churned'],
      rows: [['2026-09-01T00:00:00+00:00', 55]],
      truncated: false,
    },
  ],
  tools_used: ['query_metrics', 'search_conversations'],
  mode: 'agent',
  steps: 3,
  trace: [],
  retrieval: 'hybrid',
  sources_retrieved: 4,
  dropped_citations: 1,
  model: 'gpt-4o-mini',
  usage: { input_tokens: 1000, output_tokens: 200, latency_ms: 2400, cost_usd: 0.0003, by_model: [] },
  trace_id: '0123456789abcdef0123456789abcdef',
}

function renderAnswer(exchange: Exchange, open = vi.fn()) {
  render(
    <ConversationSheetContext.Provider value={open}>
      <TooltipProvider>
        <AnswerCard exchange={exchange} />
      </TooltipProvider>
    </ConversationSheetContext.Provider>,
  )
  return open
}

describe('AnswerCard', () => {
  it('renders the answer with clickable citations, metrics and run details', async () => {
    const open = renderAnswer({ id: 1, request: { question: 'Why?' }, status: 'done', steps: [], response })

    const chips = screen.getAllByRole('button', { name: /Source 1: open conversation conv_1/ })
    expect(chips).toHaveLength(2) // inline and in the bullet list
    await userEvent.click(chips[0])
    expect(open).toHaveBeenCalledWith({ id: 'conv_1', highlight: 'my bill went up' })

    expect(screen.getByRole('link', { name: 'M1' })).toHaveAttribute('href', '#metric-M1')
    expect(screen.getByText('[S9]', { exact: false })).toBeInTheDocument() // unknown stays text
    expect(screen.getByRole('region', { name: 'Metric M1' })).toHaveTextContent('2026-09-01')
    expect(screen.getByText('1 unverified citation removed')).toBeInTheDocument()
    expect(screen.getByText(/1,200 tokens/)).toBeInTheDocument()
  })

  it('shows live progress while running', () => {
    renderAnswer({
      id: 1,
      request: { question: 'Why?' },
      status: 'running',
      steps: [
        { tool: 'query_metrics', arguments: {}, status: 'done' },
        { tool: 'search_conversations', arguments: { query: 'price' }, status: 'running' },
      ],
    })
    expect(screen.getByText('Queried metrics')).toBeInTheDocument()
    expect(screen.getByText('Searching conversations')).toBeInTheDocument()
  })

  it('explains failures', () => {
    renderAnswer({ id: 1, request: { question: 'Why?' }, status: 'error', steps: [], error: 'The agent failed' })
    expect(screen.getByRole('alert')).toHaveTextContent('The agent failed')
  })
})
