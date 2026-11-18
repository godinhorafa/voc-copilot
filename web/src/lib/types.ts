// Mirrors the FastAPI response models (app/schemas/*.py).

export interface Citation {
  source: number
  conversation_id: string
  excerpt: string
}

export interface MetricResult {
  id: string
  question: string
  sql: string
  columns: string[]
  rows: unknown[][]
  truncated: boolean
}

export interface TraceStep {
  step: number
  tool: string
  arguments: Record<string, unknown>
  summary: string
  duration_ms: number
  error: boolean
}

export interface ModelUsage {
  provider: string
  model: string
  kind: 'chat' | 'embedding'
  calls: number
  input_tokens: number
  output_tokens: number
  cost_usd: number | null
}

export interface Usage {
  input_tokens: number
  output_tokens: number
  latency_ms: number
  cost_usd: number | null
  by_model: ModelUsage[]
}

export interface AskResponse {
  answer: string
  citations: Citation[]
  metrics: MetricResult[]
  tools_used: string[]
  mode: 'agent' | 'rag'
  steps: number
  trace: TraceStep[]
  retrieval: string
  sources_retrieved: number
  dropped_citations: number
  model: string | null
  usage: Usage
  trace_id: string | null
}

export interface AskRequest {
  question: string
  industry?: string
  start?: string
  end?: string
}

export type AskEvent =
  | { type: 'tool_start'; tool: string; arguments: Record<string, unknown> }
  | { type: 'tool_end'; tool: string; summary?: string; error: boolean }
  | { type: 'answer'; data: AskResponse }
  | { type: 'error'; message: string }

export interface Insight {
  contact_reason: string
  sentiment: 'negative' | 'neutral' | 'positive'
  resolved: boolean
  churn_risk: 'low' | 'medium' | 'high'
  competitors: string[]
  summary: string
  extractor_version: string
  model: string
  created_at: string
}

export interface Conversation {
  id: string
  external_id: string
  customer_id: string | null
  industry: string
  channel: 'chat' | 'voice'
  started_at: string
  status: 'pending' | 'processed' | 'failed'
  processing_error: string | null
  chunk_count: number
  redactions: Record<string, number>
  insight: Insight | null
  turns: { speaker: 'agent' | 'customer'; text: string; offset_s: number | null }[]
}

export interface InsightsResponse {
  industries: string[]
  totals: {
    conversations: number
    with_insights: number
    negative_share: number | null
    unresolved_share: number | null
    high_churn_risk_share: number | null
    churned_customers: number
  }
  by_month: {
    month: string
    conversations: number
    negative: number
    unresolved: number
    churned_customers: number
  }[]
  by_reason: {
    contact_reason: string
    conversations: number
    negative_share: number
    unresolved_share: number
  }[]
  competitors: { competitor: string; mentions: number }[]
}

export type Role = 'admin' | 'analyst' | 'ingest'

/** GET /v1/me: who is using the UI (a signed-in person, or the proxy's API key). */
export interface Me {
  auth: 'oidc' | 'api_key'
  role: Role
  email: string | null
  tenant: { id: string; slug: string; name: string }
}
