import { useEffect, useState } from 'react'
import { useLocation } from 'react-router'
import { Monitor, Moon, Sun } from 'lucide-react'
import { useTheme } from 'next-themes'

import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Separator } from '@/components/ui/separator'
import { SidebarTrigger } from '@/components/ui/sidebar'
import { getInsights } from '@/lib/api'
import { useFilters } from '@/lib/filters-context'
import { formatMonth, humanize } from '@/lib/format'
import { NAV } from '@/lib/nav'

export function SiteHeader() {
  const { pathname } = useLocation()
  const page = NAV.find((n) => n.to === pathname) ?? NAV[0]
  return (
    <header className="sticky top-0 z-10 flex h-14 shrink-0 items-center gap-2 border-b bg-background/80 px-4 backdrop-blur">
      <SidebarTrigger className="-ml-1" />
      <Separator orientation="vertical" className="mr-1 data-[orientation=vertical]:h-4" />
      <div className="min-w-0">
        <h1 className="truncate text-sm font-medium">{page.label}</h1>
      </div>
      <div className="ml-auto flex items-center gap-2">
        <FiltersBar />
        <ThemeToggle />
      </div>
    </header>
  )
}

/** Industry and month filters; options come from the data itself. */
function FiltersBar() {
  const { filters, setIndustry, setPeriod } = useFilters()
  const [options, setOptions] = useState<{ industries: string[]; months: string[] }>({
    industries: [],
    months: [],
  })
  const industry = filters.industry === 'all' ? undefined : filters.industry

  useEffect(() => {
    const abort = new AbortController()
    getInsights({ industry }, abort.signal)
      .then((data) => {
        setOptions({ industries: data.industries, months: data.by_month.map((m) => m.month.slice(0, 7)) })
      })
      .catch(() => {
        // The pages show the error; the filters just stay empty.
      })
    return () => abort.abort()
  }, [industry])

  return (
    <div className="flex items-center gap-2">
      <Select value={filters.industry} onValueChange={setIndustry}>
        <SelectTrigger size="sm" className="w-36" aria-label="Industry">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="all">All industries</SelectItem>
          {options.industries.map((i) => (
            <SelectItem key={i} value={i}>
              {humanize(i)}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      <Select value={filters.period} onValueChange={setPeriod}>
        <SelectTrigger size="sm" className="w-32" aria-label="Period">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="all">All time</SelectItem>
          {options.months.map((m) => (
            <SelectItem key={m} value={m}>
              {formatMonth(`${m}-01`)}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  )
}

function ThemeToggle() {
  const { theme = 'system', setTheme } = useTheme()
  const Icon = theme === 'dark' ? Moon : theme === 'light' ? Sun : Monitor
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" size="icon" aria-label="Theme">
          <Icon aria-hidden />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        <DropdownMenuRadioGroup value={theme} onValueChange={setTheme}>
          <DropdownMenuRadioItem value="light">Light</DropdownMenuRadioItem>
          <DropdownMenuRadioItem value="dark">Dark</DropdownMenuRadioItem>
          <DropdownMenuRadioItem value="system">System</DropdownMenuRadioItem>
        </DropdownMenuRadioGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
