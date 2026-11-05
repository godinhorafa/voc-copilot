import { Fragment, type ReactNode, useMemo } from 'react'
import {
  AlertCircle,
  ChevronDown,
  CircleSlash,
  Copy,
  Database,
  Loader2,
  Search,
  Sparkles,
  TriangleAlert,
} from 'lucide-react'
import { toast } from 'sonner'

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardFooter, CardHeader } from '@/components/ui/card'
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { splitAnswer, toBlocks } from '@/lib/citations'
import { useOpenConversation } from '@/lib/conversation-sheet-context'
import { formatCost, formatDuration, formatNumber, TOOL_LABELS } from '@/lib/format'
import type { AskResponse, Citation, MetricResult } from '@/lib/types'
import { cn } from '@/lib/utils'

import type { Exchange, Step } from './use-ask'

const TOOL_ICONS: Record<string, typeof Search> = {
  query_metrics: Database,
  search_conversations: Search,
  final_answer: Sparkles,
}

export function AnswerCard({ exchange }: { exchange: Exchange }) {
  const { status, steps, response, error } = exchange
  return (
    <Card className="gap-4 py-4">
      <CardHeader className="px-5">
        <Progress steps={steps} running={status === 'running'} />
      </CardHeader>
      {status === 'running' && !steps.length && (
        <CardContent className="px-5 text-sm text-muted-foreground">Thinking…</CardContent>
      )}
      {status === 'error' && (
        <CardContent className="px-5">
          <Alert variant="destructive">
            <AlertCircle aria-hidden />
            <AlertTitle>No answer this time</AlertTitle>
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        </CardContent>
      )}
      {status === 'stopped' && (
        <CardContent className="flex items-center gap-2 px-5 text-sm text-muted-foreground">
          <CircleSlash className="size-4" aria-hidden /> Stopped.
        </CardContent>
      )}
      {response && <AnswerBody response={response} />}
    </Card>
  )
}

function Progress({ steps, running }: { steps: Step[]; running: boolean }) {
  if (!steps.length && !running) return null
  return (
    <ol aria-label="Agent steps" aria-live="polite" className="flex flex-wrap gap-1.5">
      {steps.map((step, i) => {
        const Icon = TOOL_ICONS[step.tool] ?? Sparkles
        const label = TOOL_LABELS[step.tool] ?? { running: step.tool, done: step.tool }
        const query =
          typeof step.arguments.query === 'string'
            ? step.arguments.query
            : typeof step.arguments.question === 'string'
              ? step.arguments.question
              : null
        return (
          <li key={i}>
            <Tooltip>
              <TooltipTrigger asChild>
                <span
                  className={cn(
                    'inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs',
                    step.status === 'running' && 'text-foreground',
                    step.status === 'done' && 'text-muted-foreground',
                    step.status === 'error' && 'border-amber-500/40 text-amber-700 dark:text-amber-400',
                  )}
                >
                  {step.status === 'running' ? (
                    <Loader2 className="size-3.5 animate-spin" aria-hidden />
                  ) : step.status === 'error' ? (
                    <TriangleAlert className="size-3.5" aria-hidden />
                  ) : (
                    <Icon className="size-3.5" aria-hidden />
                  )}
                  {step.status === 'running' ? label.running : label.done}
                </span>
              </TooltipTrigger>
              {(query || step.summary) && (
                <TooltipContent className="max-w-xs">
                  {query && <p className="font-medium">“{query}”</p>}
                  {step.summary && <p className="opacity-80">{step.summary}</p>}
                </TooltipContent>
              )}
            </Tooltip>
          </li>
        )
      })}
    </ol>
  )
}

function AnswerBody({ response }: { response: AskResponse }) {
  const citations = useMemo(() => {
    const bySource = new Map<number, Citation>()
    for (const c of response.citations) if (!bySource.has(c.source)) bySource.set(c.source, c)
    return bySource
  }, [response.citations])
  const metrics = useMemo(
    () => new Map(response.metrics.map((m) => [Number(m.id.slice(1)), m])),
    [response.metrics],
  )

  return (
    <>
      <CardContent className="space-y-5 px-5">
        <AnswerText text={response.answer} citations={citations} metrics={metrics} />
        {response.metrics.map((metric) => (
          <MetricCard key={metric.id} metric={metric} />
        ))}
        {citations.size > 0 && <Sources citations={[...citations.values()]} />}
      </CardContent>
      <CardFooter className="px-5">
        <RunDetails response={response} />
      </CardFooter>
    </>
  )
}

function AnswerText({
  text,
  citations,
  metrics,
}: {
  text: string
  citations: Map<number, Citation>
  metrics: Map<number, MetricResult>
}) {
  const openConversation = useOpenConversation()
  const known = { sources: new Set(citations.keys()), metrics: new Set(metrics.keys()) }

  const renderInline = (line: string): ReactNode[] =>
    splitAnswer(line, known).map((segment, i) => {
      if (segment.kind === 'text') return <Fragment key={i}>{segment.text}</Fragment>
      if (segment.kind === 'metric') {
        return (
          <a
            key={i}
            href={`#metric-M${segment.number}`}
            className="mx-0.5 inline-flex h-5 items-center rounded-md bg-[var(--chart-1)]/15 px-1.5 align-baseline font-mono text-[11px] font-medium text-foreground hover:bg-[var(--chart-1)]/25"
          >
            M{segment.number}
          </a>
        )
      }
      const citation = citations.get(segment.number)!
      return (
        <Tooltip key={i}>
          <TooltipTrigger asChild>
            <button
              type="button"
              onClick={() =>
                openConversation({ id: citation.conversation_id, highlight: citation.excerpt })
              }
              className="mx-0.5 inline-flex h-5 items-center rounded-md bg-muted px-1.5 align-baseline font-mono text-[11px] font-medium text-foreground ring-1 ring-border hover:bg-accent"
              aria-label={`Source ${segment.number}: open conversation ${citation.conversation_id}`}
            >
              S{segment.number}
            </button>
          </TooltipTrigger>
          <TooltipContent className="max-w-xs">
            <p>“{citation.excerpt}”</p>
            <p className="mt-1 font-mono opacity-70">{citation.conversation_id}</p>
          </TooltipContent>
        </Tooltip>
      )
    })

  return (
    <div className="space-y-3 text-[15px] leading-7">
      {toBlocks(text).map((block, i) =>
        block.kind === 'list' ? (
          <ul key={i} className="list-disc space-y-1 pl-5">
            {block.items.map((item, j) => (
              <li key={j}>{renderInline(item)}</li>
            ))}
          </ul>
        ) : (
          <p key={i}>{renderInline(block.text)}</p>
        ),
      )}
    </div>
  )
}

function MetricCard({ metric }: { metric: MetricResult }) {
  return (
    <section
      id={`metric-${metric.id}`}
      aria-label={`Metric ${metric.id}`}
      className="scroll-mt-24 rounded-lg border"
    >
      <div className="flex items-start justify-between gap-3 border-b px-3.5 py-2.5">
        <div className="flex items-center gap-2 text-sm">
          <Badge variant="secondary" className="font-mono">
            {metric.id}
          </Badge>
          <span className="text-muted-foreground">{metric.question}</span>
        </div>
      </div>
      <div className="max-h-72 overflow-auto">
        <Table>
          <TableHeader>
            <TableRow>
              {metric.columns.map((c) => (
                <TableHead key={c} className="h-9 px-3.5 text-xs">
                  {c}
                </TableHead>
              ))}
            </TableRow>
          </TableHeader>
          <TableBody>
            {metric.rows.map((row, i) => (
              <TableRow key={i}>
                {row.map((cell, j) => (
                  <TableCell
                    key={j}
                    className={cn('px-3.5 py-2 text-sm', typeof cell === 'number' && 'tabular-nums')}
                  >
                    {formatCell(cell)}
                  </TableCell>
                ))}
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
      {metric.truncated && (
        <p className="border-t px-3.5 py-2 text-xs text-muted-foreground">
          Showing the first {metric.rows.length} rows.
        </p>
      )}
      <Collapsible>
        <CollapsibleTrigger asChild>
          <button
            type="button"
            className="group flex w-full items-center gap-1 border-t px-3.5 py-2 text-xs text-muted-foreground hover:text-foreground"
          >
            <ChevronDown className="size-3.5 transition-transform group-data-[state=open]:rotate-180" />
            SQL
          </button>
        </CollapsibleTrigger>
        <CollapsibleContent>
          <pre className="overflow-x-auto bg-muted/50 px-3.5 py-3 font-mono text-xs leading-relaxed whitespace-pre-wrap">
            {metric.sql}
          </pre>
        </CollapsibleContent>
      </Collapsible>
    </section>
  )
}

function formatCell(cell: unknown): string {
  if (cell == null) return '—'
  if (typeof cell === 'number') return Number.isInteger(cell) ? formatNumber(cell) : cell.toFixed(3)
  if (typeof cell === 'string' && /^\d{4}-\d{2}-\d{2}T00:00:00/.test(cell)) return cell.slice(0, 10)
  return String(cell)
}

function Sources({ citations }: { citations: Citation[] }) {
  const openConversation = useOpenConversation()
  return (
    <section aria-label="Sources" className="space-y-2">
      <h3 className="text-xs font-medium tracking-wide text-muted-foreground uppercase">Sources</h3>
      <ul className="grid gap-2 sm:grid-cols-2">
        {citations.map((c) => (
          <li key={c.source}>
            <button
              type="button"
              onClick={() => openConversation({ id: c.conversation_id, highlight: c.excerpt })}
              className="flex h-full w-full flex-col gap-1.5 rounded-lg border p-3 text-left text-sm transition-colors hover:bg-accent/50"
            >
              <span className="flex items-center gap-2 text-xs text-muted-foreground">
                <span className="rounded bg-muted px-1.5 font-mono font-medium text-foreground">
                  S{c.source}
                </span>
                <span className="truncate font-mono">{c.conversation_id}</span>
              </span>
              <span className="line-clamp-3">“{c.excerpt}”</span>
            </button>
          </li>
        ))}
      </ul>
    </section>
  )
}

function RunDetails({ response }: { response: AskResponse }) {
  const { usage } = response
  const copyTrace = async () => {
    if (!response.trace_id) return
    await navigator.clipboard.writeText(response.trace_id)
    toast.success('Trace id copied')
  }
  return (
    <div className="flex w-full flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
      <span>{formatDuration(usage.latency_ms)}</span>
      <span>
        {formatNumber(usage.input_tokens + usage.output_tokens)} tokens ·{' '}
        {formatCost(usage.cost_usd)}
      </span>
      <span>
        {response.steps} step{response.steps === 1 ? '' : 's'} · {response.retrieval}
      </span>
      {response.model && <span className="font-mono">{response.model}</span>}
      {response.dropped_citations > 0 && (
        <span className="text-amber-700 dark:text-amber-400">
          {response.dropped_citations} unverified citation
          {response.dropped_citations === 1 ? '' : 's'} removed
        </span>
      )}
      {response.trace_id && (
        <Button variant="ghost" size="xs" className="ml-auto font-mono" onClick={copyTrace}>
          <Copy aria-hidden /> {response.trace_id.slice(0, 8)}
        </Button>
      )}
    </div>
  )
}
