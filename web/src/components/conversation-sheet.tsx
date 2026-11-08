import { type ReactNode, useCallback, useEffect, useMemo, useState } from 'react'
import { AlertCircle, Headphones, MessageSquare, ShieldCheck } from 'lucide-react'

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Separator } from '@/components/ui/separator'
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from '@/components/ui/sheet'
import { Skeleton } from '@/components/ui/skeleton'
import { getConversation } from '@/lib/api'
import { ConversationSheetContext, type ConversationTarget } from '@/lib/conversation-sheet-context'
import { formatDateTime, humanize } from '@/lib/format'
import type { Conversation } from '@/lib/types'
import { cn } from '@/lib/utils'

type Target = ConversationTarget

export function ConversationSheetProvider({ children }: { children: ReactNode }) {
  const [target, setTarget] = useState<Target | null>(null)
  const open = useCallback((t: Target) => setTarget(t), [])
  return (
    <ConversationSheetContext.Provider value={open}>
      {children}
      <Sheet open={target !== null} onOpenChange={(isOpen) => !isOpen && setTarget(null)}>
        <SheetContent className="w-full gap-0 p-0 sm:max-w-xl">
          {/* Keyed so each conversation starts from a fresh loading state. */}
          {target && <ConversationDetail key={target.id} target={target} />}
        </SheetContent>
      </Sheet>
    </ConversationSheetContext.Provider>
  )
}

type State =
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'ready'; conversation: Conversation }

function ConversationDetail({ target }: { target: Target }) {
  const [state, setState] = useState<State>({ status: 'loading' })

  useEffect(() => {
    const abort = new AbortController()
    getConversation(target.id, abort.signal)
      .then((conversation) => setState({ status: 'ready', conversation }))
      .catch(
        (error: Error) =>
          !abort.signal.aborted && setState({ status: 'error', message: error.message }),
      )
    return () => abort.abort()
  }, [target.id])

  const conversation = state.status === 'ready' ? state.conversation : null
  return (
    <>
      <SheetHeader className="border-b p-5">
        <SheetTitle className="font-mono text-sm">{target.id}</SheetTitle>
        <SheetDescription asChild>
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
            {conversation ? (
              <>
                <span className="inline-flex items-center gap-1">
                  {conversation.channel === 'voice' ? (
                    <Headphones className="size-3.5" aria-hidden />
                  ) : (
                    <MessageSquare className="size-3.5" aria-hidden />
                  )}
                  {humanize(conversation.channel)}
                </span>
                <span>{formatDateTime(conversation.started_at)} UTC</span>
                <span>{humanize(conversation.industry)}</span>
                {conversation.customer_id && <span>Customer {conversation.customer_id}</span>}
              </>
            ) : (
              <Skeleton className="h-4 w-56" />
            )}
          </div>
        </SheetDescription>
      </SheetHeader>

      {state.status === 'error' && (
        <Alert variant="destructive" className="m-5 w-auto">
          <AlertCircle aria-hidden />
          <AlertTitle>Couldn't load this conversation</AlertTitle>
          <AlertDescription>{state.message}</AlertDescription>
        </Alert>
      )}
      {state.status === 'loading' && (
        <div className="space-y-3 p-5">
          {Array.from({ length: 5 }, (_, i) => (
            <Skeleton key={i} className={cn('h-14', i % 2 ? 'ml-10' : 'mr-10')} />
          ))}
        </div>
      )}
      {conversation && (
        <ScrollArea className="min-h-0 flex-1">
          <div className="space-y-5 p-5">
            {conversation.insight && <InsightSummary conversation={conversation} />}
            <Redactions counts={conversation.redactions} />
            <Separator />
            <Transcript conversation={conversation} highlight={target.highlight} />
          </div>
        </ScrollArea>
      )}
    </>
  )
}

const SENTIMENT_TONE: Record<string, string> = {
  negative: 'border-red-500/30 bg-red-500/10 text-red-700 dark:text-red-300',
  neutral: '',
  positive: 'border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300',
}

function InsightSummary({ conversation }: { conversation: Conversation }) {
  const insight = conversation.insight!
  return (
    <section aria-label="Extracted insight" className="space-y-3">
      <p className="text-sm leading-relaxed">{insight.summary}</p>
      <div className="flex flex-wrap gap-1.5">
        <Badge variant="secondary">{humanize(insight.contact_reason)}</Badge>
        <Badge variant="outline" className={SENTIMENT_TONE[insight.sentiment]}>
          Sentiment: {insight.sentiment}
        </Badge>
        <Badge variant="outline">{insight.resolved ? 'Resolved' : 'Not resolved'}</Badge>
        <Badge variant="outline">Churn risk: {insight.churn_risk}</Badge>
        {insight.competitors.map((name) => (
          <Badge key={name} variant="outline">
            Mentions {name}
          </Badge>
        ))}
      </div>
    </section>
  )
}

function Redactions({ counts }: { counts: Record<string, number> }) {
  const entries = Object.entries(counts ?? {})
  if (!entries.length) return null
  const total = entries.reduce((sum, [, n]) => sum + n, 0)
  const detail = entries.map(([kind, n]) => `${n} ${kind.toLowerCase()}`).join(', ')
  return (
    <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
      <ShieldCheck className="size-3.5" aria-hidden />
      {total} personal detail{total === 1 ? '' : 's'} redacted at ingestion ({detail})
    </p>
  )
}

function Transcript({ conversation, highlight }: { conversation: Conversation; highlight?: string }) {
  const needle = useMemo(() => highlight?.toLowerCase().replace(/\s+/g, ' ').trim(), [highlight])
  return (
    <ol aria-label="Transcript" className="space-y-3">
      {conversation.turns.map((turn, i) => {
        const isCustomer = turn.speaker === 'customer'
        const cited = needle ? turn.text.toLowerCase().replace(/\s+/g, ' ').includes(needle) : false
        return (
          <li key={i} className={cn('flex', isCustomer ? 'justify-start' : 'justify-end')}>
            <div
              className={cn(
                'max-w-[85%] rounded-xl px-3.5 py-2.5 text-sm leading-relaxed',
                isCustomer ? 'bg-muted' : 'bg-primary/5 ring-1 ring-border',
                cited && 'ring-2 ring-amber-500/70',
              )}
            >
              <div className="mb-1 flex items-center gap-2 text-xs text-muted-foreground">
                <span className="font-medium capitalize">{turn.speaker}</span>
                {turn.offset_s != null && <span>{turn.offset_s}s</span>}
                {cited && <span className="text-amber-700 dark:text-amber-400">Cited</span>}
              </div>
              {turn.text}
            </div>
          </li>
        )
      })}
    </ol>
  )
}
