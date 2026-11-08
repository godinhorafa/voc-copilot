import { lazy, Suspense } from 'react'
import { Route, Routes } from 'react-router'

import { AppSidebar } from '@/components/app-sidebar'
import { ConversationSheetProvider } from '@/components/conversation-sheet'
import { SiteHeader } from '@/components/site-header'
import { SidebarInset, SidebarProvider } from '@/components/ui/sidebar'
import { AskPage } from '@/features/ask/ask-page'
import { Skeleton } from '@/components/ui/skeleton'
import { FiltersProvider } from '@/lib/filters'

// Charts (recharts) are the heaviest dependency; load them with the page that uses them.
const InsightsPage = lazy(() =>
  import('@/features/insights/insights-page').then((m) => ({ default: m.InsightsPage })),
)

export default function App() {
  return (
    <FiltersProvider>
      <ConversationSheetProvider>
        <SidebarProvider>
          <AppSidebar />
          <SidebarInset className="min-w-0">
            <SiteHeader />
            <main className="flex-1">
              <Routes>
                <Route path="/" element={<AskPage />} />
                <Route
                  path="/insights"
                  element={
                    <Suspense fallback={<Skeleton className="m-6 h-96" />}>
                      <InsightsPage />
                    </Suspense>
                  }
                />
                <Route path="*" element={<AskPage />} />
              </Routes>
            </main>
          </SidebarInset>
        </SidebarProvider>
      </ConversationSheetProvider>
    </FiltersProvider>
  )
}
