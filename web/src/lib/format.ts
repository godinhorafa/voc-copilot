/** "billing_dispute" -> "Billing dispute" */
export function humanize(id: string): string {
  const words = id.replace(/[_-]+/g, ' ').trim()
  return words.charAt(0).toUpperCase() + words.slice(1)
}

export const formatNumber = (n: number) => new Intl.NumberFormat('en-US').format(n)

export function formatPercent(share: number | null | undefined, digits = 0): string {
  return share == null ? '-' : `${(share * 100).toFixed(digits)}%`
}

export function formatCost(usd: number | null): string {
  if (usd == null) return 'unpriced'
  if (usd === 0) return '$0'
  return usd < 0.01 ? `$${usd.toFixed(4)}` : `$${usd.toFixed(2)}`
}

export function formatDuration(ms: number): string {
  return ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1)} s`
}

export function formatMonth(iso: string): string {
  return new Date(`${iso.slice(0, 10)}T00:00:00Z`).toLocaleDateString('en-US', {
    month: 'short',
    year: '2-digit',
    timeZone: 'UTC',
  })
}

export function formatDateTime(iso: string): string {
  return new Date(iso).toLocaleString('en-US', {
    dateStyle: 'medium',
    timeStyle: 'short',
    timeZone: 'UTC',
  })
}

export const TOOL_LABELS: Record<string, { running: string; done: string }> = {
  query_metrics: { running: 'Querying metrics', done: 'Queried metrics' },
  search_conversations: { running: 'Searching conversations', done: 'Searched conversations' },
  final_answer: { running: 'Writing the answer', done: 'Wrote the answer' },
}
