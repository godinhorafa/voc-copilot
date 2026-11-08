import { type ReactNode, useEffect, useState } from 'react'
import { useNavigate } from 'react-router'
import { AlertCircle, ArrowUpRight } from 'lucide-react'
import { Bar, BarChart, CartesianGrid, XAxis, YAxis } from 'recharts'

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { type ChartConfig, ChartContainer, ChartTooltip, ChartTooltipContent } from '@/components/ui/chart'
import { Skeleton } from '@/components/ui/skeleton'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { getInsights } from '@/lib/api'
import { useFilters } from '@/lib/filters-context'
import { formatMonth, formatNumber, formatPercent, humanize } from '@/lib/format'
import type { InsightsResponse } from '@/lib/types'

type State =
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'ready'; data: InsightsResponse }

type Result = { key: string } & ({ data: InsightsResponse } | { error: string })

function useInsights(): State {
  const { params } = useFilters()
  const key = JSON.stringify(params)
  const [result, setResult] = useState<Result | null>(null)

  useEffect(() => {
    const abort = new AbortController()
    getInsights(JSON.parse(key) as typeof params, abort.signal)
      .then((data) => setResult({ key, data }))
      .catch((error: Error) => !abort.signal.aborted && setResult({ key, error: error.message }))
    return () => abort.abort()
  }, [key])

  // A result for other filters means the current request is still loading.
  if (!result || result.key !== key) return { status: 'loading' }
  return 'data' in result
    ? { status: 'ready', data: result.data }
    : { status: 'error', message: result.error }
}

const volumeConfig = {
  conversations: { label: 'Conversations', color: 'var(--chart-1)' },
} satisfies ChartConfig
const churnConfig = {
  churned_customers: { label: 'Churned customers', color: 'var(--chart-2)' },
} satisfies ChartConfig
const reasonConfig = {
  conversations: { label: 'Conversations', color: 'var(--chart-1)' },
} satisfies ChartConfig
const competitorConfig = {
  mentions: { label: 'Mentions', color: 'var(--chart-1)' },
} satisfies ChartConfig

export function InsightsPage() {
  const state = useInsights()
  const navigate = useNavigate()

  if (state.status === 'error') {
    return (
      <div className="p-6">
        <Alert variant="destructive">
          <AlertCircle aria-hidden />
          <AlertTitle>Couldn't load insights</AlertTitle>
          <AlertDescription>{state.message}</AlertDescription>
        </Alert>
      </div>
    )
  }
  const data = state.status === 'ready' ? state.data : null
  const months = data?.by_month.map((m) => ({ ...m, label: formatMonth(m.month) })) ?? []
  const reasons =
    data?.by_reason.map((r) => ({ ...r, label: humanize(r.contact_reason) })) ?? []

  const askWhy = (reason: string) =>
    navigate(`/?q=${encodeURIComponent(`Why do customers contact us about ${reason.toLowerCase()}?`)}`)

  return (
    <div className="mx-auto w-full max-w-6xl space-y-6 p-4 md:p-6">
      <section aria-label="Totals" className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Kpi
          label="Conversations"
          value={data && formatNumber(data.totals.conversations)}
          hint={
            data &&
            data.totals.with_insights < data.totals.conversations &&
            `${formatNumber(data.totals.with_insights)} analyzed so far`
          }
        />
        <Kpi label="Negative sentiment" value={data && formatPercent(data.totals.negative_share)} hint="Customer's mood at the end" />
        <Kpi label="Left unresolved" value={data && formatPercent(data.totals.unresolved_share)} hint="Issue not solved in the contact" />
        <Kpi label="Churned customers" value={data && formatNumber(data.totals.churned_customers)} hint="Churn date in the period" />
      </section>

      <div className="grid gap-4 lg:grid-cols-2">
        <ChartCard
          title="Conversations per month"
          description="Contact volume over time"
          loading={!data}
          empty={!months.length}
          chart={
            <ChartContainer config={volumeConfig} className="aspect-auto h-64 w-full">
              <BarChart data={months} margin={{ left: -16, right: 8 }}>
                <CartesianGrid vertical={false} strokeDasharray="3 3" />
                <XAxis dataKey="label" tickLine={false} axisLine={false} tickMargin={8} />
                <YAxis tickLine={false} axisLine={false} width={48} allowDecimals={false} />
                <ChartTooltip cursor={{ fillOpacity: 0.4 }} content={<ChartTooltipContent />} />
                <Bar isAnimationActive={false} dataKey="conversations" fill="var(--color-conversations)" radius={[4, 4, 0, 0]} maxBarSize={48} />
              </BarChart>
            </ChartContainer>
          }
          table={
            <SimpleTable
              head={['Month', 'Conversations', 'Negative', 'Unresolved']}
              rows={months.map((m) => [m.label, m.conversations, m.negative, m.unresolved])}
            />
          }
        />
        <ChartCard
          title="Churned customers per month"
          description="Customers whose churn date falls in each month"
          loading={!data}
          empty={!months.some((m) => m.churned_customers)}
          chart={
            <ChartContainer config={churnConfig} className="aspect-auto h-64 w-full">
              <BarChart data={months} margin={{ left: -16, right: 8 }}>
                <CartesianGrid vertical={false} strokeDasharray="3 3" />
                <XAxis dataKey="label" tickLine={false} axisLine={false} tickMargin={8} />
                <YAxis tickLine={false} axisLine={false} width={48} allowDecimals={false} />
                <ChartTooltip cursor={{ fillOpacity: 0.4 }} content={<ChartTooltipContent />} />
                <Bar isAnimationActive={false} dataKey="churned_customers" fill="var(--color-churned_customers)" radius={[4, 4, 0, 0]} maxBarSize={48} />
              </BarChart>
            </ChartContainer>
          }
          table={
            <SimpleTable
              head={['Month', 'Churned customers']}
              rows={months.map((m) => [m.label, m.churned_customers])}
            />
          }
        />
      </div>

      <div className="grid gap-4 lg:grid-cols-5">
        <ChartCard
          className="lg:col-span-3"
          title="Why customers contact us"
          description="Click a reason to ask the agent why"
          loading={!data}
          empty={!reasons.length}
          chart={
            <ChartContainer config={reasonConfig} className="aspect-auto w-full" style={{ height: Math.max(160, reasons.length * 40) }}>
              <BarChart data={reasons} layout="vertical" margin={{ left: 8, right: 16 }}>
                <CartesianGrid horizontal={false} strokeDasharray="3 3" />
                <XAxis type="number" tickLine={false} axisLine={false} allowDecimals={false} />
                <YAxis type="category" dataKey="label" tickLine={false} axisLine={false} width={128} />
                <ChartTooltip
                  cursor={{ fillOpacity: 0.4 }}
                  content={
                    <ChartTooltipContent
                      formatter={(value, _name, item) => (
                        <div className="grid gap-0.5">
                          <span className="font-medium tabular-nums">{formatNumber(Number(value))} conversations</span>
                          <span className="text-muted-foreground">
                            {formatPercent(item.payload.negative_share)} negative ·{' '}
                            {formatPercent(item.payload.unresolved_share)} unresolved
                          </span>
                        </div>
                      )}
                    />
                  }
                />
                <Bar
                  isAnimationActive={false}
                  dataKey="conversations"
                  fill="var(--color-conversations)"
                  radius={[0, 4, 4, 0]}
                  maxBarSize={24}
                  className="cursor-pointer"
                  onClick={(entry: { payload?: { label: string } }) => entry.payload && askWhy(entry.payload.label)}
                />
              </BarChart>
            </ChartContainer>
          }
          table={
            <SimpleTable
              head={['Reason', 'Conversations', 'Negative', 'Unresolved', '']}
              rows={reasons.map((r) => [
                r.label,
                r.conversations,
                formatPercent(r.negative_share),
                formatPercent(r.unresolved_share),
                <button key="ask" type="button" className="inline-flex items-center gap-1 text-xs underline-offset-4 hover:underline" onClick={() => askWhy(r.label)}>
                  Ask why <ArrowUpRight className="size-3" aria-hidden />
                </button>,
              ])}
            />
          }
        />
        <ChartCard
          className="lg:col-span-2"
          title="Competitors mentioned"
          description="Times customers named a competitor"
          loading={!data}
          empty={!data?.competitors.length}
          chart={
            <ChartContainer config={competitorConfig} className="aspect-auto w-full" style={{ height: Math.max(160, (data?.competitors.length ?? 0) * 40) }}>
              <BarChart data={data?.competitors ?? []} layout="vertical" margin={{ left: 8, right: 16 }}>
                <CartesianGrid horizontal={false} strokeDasharray="3 3" />
                <XAxis type="number" tickLine={false} axisLine={false} allowDecimals={false} />
                <YAxis type="category" dataKey="competitor" tickLine={false} axisLine={false} width={112} />
                <ChartTooltip cursor={{ fillOpacity: 0.4 }} content={<ChartTooltipContent />} />
                <Bar isAnimationActive={false} dataKey="mentions" fill="var(--color-mentions)" radius={[0, 4, 4, 0]} maxBarSize={24} />
              </BarChart>
            </ChartContainer>
          }
          table={
            <SimpleTable
              head={['Competitor', 'Mentions']}
              rows={(data?.competitors ?? []).map((c) => [c.competitor, c.mentions])}
            />
          }
        />
      </div>
    </div>
  )
}

function Kpi({ label, value, hint }: { label: string; value: string | null; hint?: string | false | null }) {
  return (
    <Card className="gap-2 py-5">
      <CardHeader className="px-5">
        <CardDescription>{label}</CardDescription>
        <CardTitle className="text-3xl font-semibold tabular-nums">
          {value ?? <Skeleton className="h-9 w-24" />}
        </CardTitle>
      </CardHeader>
      {hint && <CardContent className="px-5 text-xs text-muted-foreground">{hint}</CardContent>}
    </Card>
  )
}

function ChartCard({
  title,
  description,
  chart,
  table,
  loading,
  empty,
  className,
}: {
  title: string
  description: string
  chart: ReactNode
  table: ReactNode
  loading: boolean
  empty: boolean
  className?: string
}) {
  const [view, setView] = useState<'chart' | 'table'>('chart')
  return (
    <Card className={className}>
      <CardHeader>
        <div className="flex items-start justify-between gap-3">
          <div className="space-y-1">
            <CardTitle>{title}</CardTitle>
            <CardDescription>{description}</CardDescription>
          </div>
          <Tabs value={view} onValueChange={(v) => setView(v as 'chart' | 'table')}>
            <TabsList className="h-8">
              <TabsTrigger value="chart" className="text-xs">Chart</TabsTrigger>
              <TabsTrigger value="table" className="text-xs">Table</TabsTrigger>
            </TabsList>
          </Tabs>
        </div>
      </CardHeader>
      <CardContent>
        {loading ? (
          <Skeleton className="h-64 w-full" />
        ) : empty ? (
          <p className="flex h-40 items-center justify-center text-sm text-muted-foreground">
            No data for these filters.
          </p>
        ) : view === 'chart' ? (
          chart
        ) : (
          table
        )}
      </CardContent>
    </Card>
  )
}

function SimpleTable({ head, rows }: { head: string[]; rows: ReactNode[][] }) {
  return (
    <div className="max-h-80 overflow-auto rounded-md border">
      <Table>
        <TableHeader>
          <TableRow>
            {head.map((h, i) => (
              <TableHead key={i} className={i ? 'text-right' : undefined}>{h}</TableHead>
            ))}
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((row, i) => (
            <TableRow key={i}>
              {row.map((cell, j) => (
                <TableCell key={j} className={j ? 'text-right tabular-nums' : undefined}>
                  {typeof cell === 'number' ? formatNumber(cell) : cell}
                </TableCell>
              ))}
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  )
}
