import { createContext, useContext } from 'react'

// Filters shared by the Ask and Insights pages. A period is either "all" or a
// month ("2026-09"); months come from the data, via the insights endpoint.

export interface Filters {
  industry: string // "all" or an industry id
  period: string // "all" or "YYYY-MM"
}

export interface FiltersContextValue {
  filters: Filters
  setIndustry: (industry: string) => void
  setPeriod: (period: string) => void
  /** API parameters for the current filters (undefined = no filter). */
  params: { industry?: string; start?: string; end?: string }
}

export const FiltersContext = createContext<FiltersContextValue | null>(null)

export function periodBounds(period: string): { start?: string; end?: string } {
  if (period === 'all') return {}
  const [year, month] = period.split('-').map(Number)
  const next = month === 12 ? `${year + 1}-01` : `${year}-${String(month + 1).padStart(2, '0')}`
  return { start: `${period}-01`, end: `${next}-01` }
}

export function useFilters(): FiltersContextValue {
  const value = useContext(FiltersContext)
  if (!value) throw new Error('useFilters must be used inside FiltersProvider')
  return value
}
