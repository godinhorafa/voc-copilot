import { lazy, Suspense } from 'react'
import { Route, Routes } from 'react-router'

import { AppSidebar } from '@/components/app-sidebar'
import { ConversationSheetProvider } from '@/components/conversation-sheet'
import { RequireExplore, SessionGate, SessionProvider } from '@/components/session'
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
    <SessionProvider>
      <SessionGate>
        <Shell />
      </SessionGate>
    </SessionProvider>
  )
}

function Shell() {
  return (
    <FiltersProvider>
      <ConversationSheetProvider>
        <SidebarProvider>
          <AppSidebar />
          <SidebarInset className="min-w-0">
            <SiteHeader />
            <main className="flex-1">
              <Routes>
                <Route
                  path="/"
                  element={
                    <RequireExplore>
                      <AskPage />
                    </RequireExplore>
                  }
                />
                <Route
                  path="/insights"
                  element={
                    <RequireExplore>
                      <Suspense fallback={<Skeleton className="m-6 h-96" />}>
                        <InsightsPage />
                      </Suspense>
                    </RequireExplore>
                  }
                />
                <Route
                  path="*"
                  element={
                    <RequireExplore>
                      <AskPage />
                    </RequireExplore>
                  }
                />
              </Routes>
            </main>
          </SidebarInset>
        </SidebarProvider>
      </ConversationSheetProvider>
    </FiltersProvider>
  )
}
