import { type FormEvent, type KeyboardEvent, useEffect, useRef, useState } from 'react'
import { useSearchParams } from 'react-router'
import { ArrowUp, Filter, RotateCcw, Sparkles, Square } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'
import { useFilters } from '@/lib/filters-context'
import { formatMonth, humanize } from '@/lib/format'

import { AnswerCard } from './answer-card'
import { useAsk } from './use-ask'

const SUGGESTIONS = [
  'Why did cancellations spike in September?',
  'Which competitors do churned customers mention?',
  'What are customers saying about their bills?',
  'How big was the network outage in October?',
]

export function AskPage() {
  const { exchanges, ask, stop, reset, running } = useAsk()
  const { filters, params } = useFilters()
  const [searchParams, setSearchParams] = useSearchParams()
  // "Ask why" links from Insights arrive as ?q=...; prefill, don't auto-send.
  const [draft, setDraft] = useState(() => searchParams.get('q') ?? '')
  const endRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (searchParams.has('q')) setSearchParams({}, { replace: true })
  }, [searchParams, setSearchParams])

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [exchanges])

  const submit = (question: string) => {
    const text = question.trim()
    if (!text || running) return
    setDraft('')
    void ask({ question: text, ...params })
  }

  const scope = [
    filters.industry !== 'all' ? humanize(filters.industry) : null,
    filters.period !== 'all' ? formatMonth(`${filters.period}-01`) : null,
  ].filter(Boolean)

  return (
    <div className="mx-auto flex min-h-full w-full max-w-3xl flex-col px-4">
      <div className="flex-1 py-6">
        {exchanges.length === 0 ? (
          <EmptyState onPick={submit} />
        ) : (
          <div className="space-y-8">
            {exchanges.map((exchange) => (
              <div key={exchange.id} className="space-y-3">
                <div className="flex justify-end">
                  <p className="max-w-[85%] rounded-2xl bg-primary px-4 py-2.5 text-[15px] text-primary-foreground">
                    {exchange.request.question}
                  </p>
                </div>
                <AnswerCard exchange={exchange} />
              </div>
            ))}
            {/* scroll-mb keeps the latest answer clear of the sticky composer */}
            <div ref={endRef} className="scroll-mb-44" />
          </div>
        )}
      </div>

      <Composer
        draft={draft}
        setDraft={setDraft}
        onSubmit={() => submit(draft)}
        onStop={stop}
        onReset={exchanges.length ? reset : undefined}
        running={running}
        scope={scope as string[]}
      />
    </div>
  )
}

function EmptyState({ onPick }: { onPick: (question: string) => void }) {
  return (
    <div className="flex flex-col items-center gap-6 pt-[12vh] text-center">
      <div className="flex size-12 items-center justify-center rounded-2xl bg-primary text-primary-foreground shadow-sm">
        <Sparkles className="size-6" aria-hidden />
      </div>
      <div className="space-y-2">
        <h1 className="text-2xl font-semibold tracking-tight">Ask about your customers</h1>
        <p className="max-w-md text-muted-foreground">
          Answers combine metrics with what customers actually said, and every claim links to the
          conversation it came from.
        </p>
      </div>
      <div className="grid w-full max-w-xl gap-2 sm:grid-cols-2">
        {SUGGESTIONS.map((q) => (
          <button
            key={q}
            type="button"
            onClick={() => onPick(q)}
            className="rounded-xl border bg-card p-3.5 text-left text-sm transition-colors hover:bg-accent/60"
          >
            {q}
          </button>
        ))}
      </div>
    </div>
  )
}

function Composer({
  draft,
  setDraft,
  onSubmit,
  onStop,
  onReset,
  running,
  scope,
}: {
  draft: string
  setDraft: (value: string) => void
  onSubmit: () => void
  onStop: () => void
  onReset?: () => void
  running: boolean
  scope: string[]
}) {
  const handleKey = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault()
      onSubmit()
    }
  }
  const handleSubmit = (event: FormEvent) => {
    event.preventDefault()
    onSubmit()
  }

  return (
    <div className="sticky bottom-0 bg-gradient-to-t from-background via-background to-background/0 pt-4 pb-4">
      <form
        onSubmit={handleSubmit}
        className="rounded-2xl border bg-card p-2 shadow-sm focus-within:ring-2 focus-within:ring-ring/30"
      >
        <label htmlFor="question" className="sr-only">
          Your question
        </label>
        <Textarea
          id="question"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={handleKey}
          placeholder="Ask why something happened, or how much…"
          rows={2}
          className="max-h-48 min-h-12 resize-none border-0 bg-transparent px-2 shadow-none focus-visible:ring-0 dark:bg-transparent"
        />
        <div className="flex items-center gap-2 px-1">
          <span className="inline-flex items-center gap-1.5 text-xs text-muted-foreground">
            <Filter className="size-3.5" aria-hidden />
            {scope.length ? scope.join(' · ') : 'All data'}
          </span>
          <div className="ml-auto flex items-center gap-1">
            {onReset && (
              <Button type="button" variant="ghost" size="sm" onClick={onReset} disabled={running}>
                <RotateCcw aria-hidden /> New
              </Button>
            )}
            {running ? (
              <Button type="button" size="icon" onClick={onStop} aria-label="Stop">
                <Square className="fill-current" aria-hidden />
              </Button>
            ) : (
              <Button type="submit" size="icon" disabled={!draft.trim()} aria-label="Send">
                <ArrowUp aria-hidden />
              </Button>
            )}
          </div>
        </div>
      </form>
      <p className="mt-2 text-center text-[11px] text-muted-foreground">
        Enter to send · Shift+Enter for a new line · Answers can be wrong; check the sources.
      </p>
    </div>
  )
}
