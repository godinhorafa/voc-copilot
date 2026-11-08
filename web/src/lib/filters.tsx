import { type ReactNode, useMemo, useState } from 'react'

import { type Filters, FiltersContext, type FiltersContextValue, periodBounds } from './filters-context'

export function FiltersProvider({ children }: { children: ReactNode }) {
  const [filters, setFilters] = useState<Filters>({ industry: 'all', period: 'all' })
  const value = useMemo<FiltersContextValue>(
    () => ({
      filters,
      setIndustry: (industry) => setFilters((f) => ({ ...f, industry })),
      setPeriod: (period) => setFilters((f) => ({ ...f, period })),
      params: {
        industry: filters.industry === 'all' ? undefined : filters.industry,
        ...periodBounds(filters.period),
      },
    }),
    [filters],
  )
  return <FiltersContext.Provider value={value}>{children}</FiltersContext.Provider>
}
