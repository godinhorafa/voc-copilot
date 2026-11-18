import { type ReactNode, useEffect, useState } from 'react'
import { ChevronsUpDown, KeyRound, LockKeyhole, LogOut, RefreshCw, ShieldAlert } from 'lucide-react'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { SidebarMenu, SidebarMenuButton, SidebarMenuItem } from '@/components/ui/sidebar'
import { Skeleton } from '@/components/ui/skeleton'
import { ApiError, SIGN_OUT_URL, getMe } from '@/lib/api'
import { type Session, SessionContext, canExplore, useSession } from '@/lib/session-context'
import type { Me } from '@/lib/types'

export function SessionProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<Session>({ status: 'loading' })

  useEffect(() => {
    const abort = new AbortController()
    getMe(abort.signal)
      .then((me) => setSession({ status: 'ready', me }))
      .catch((error: Error) => {
        if (abort.signal.aborted) return
        if (error instanceof ApiError && error.status === 403) {
          setSession({ status: 'denied', message: error.message })
        } else if (error instanceof ApiError && error.status === 401) {
          setSession({ status: 'expired' })
        } else {
          setSession({ status: 'error', message: error.message })
        }
      })
    return () => abort.abort()
  }, [])

  return <SessionContext.Provider value={session}>{children}</SessionContext.Provider>
}

/** Full-page screens for a session the app can't be used with. */
export function SessionGate({ children }: { children: ReactNode }) {
  const session = useSession()
  if (session.status === 'denied') {
    return (
      <Blocker
        icon={<ShieldAlert aria-hidden />}
        title="You don't have access yet"
        body={`${session.message}.`}
        action={
          <Button asChild variant="outline">
            <a href={SIGN_OUT_URL}>
              <LogOut aria-hidden /> Sign in with another account
            </a>
          </Button>
        }
      />
    )
  }
  if (session.status === 'expired') {
    return (
      <Blocker
        icon={<LockKeyhole aria-hidden />}
        title="Your session expired"
        body="Sign in again to keep working."
        action={
          <Button onClick={() => window.location.reload()}>
            <RefreshCw aria-hidden /> Sign in again
          </Button>
        }
      />
    )
  }
  return children
}

function Blocker({
  icon,
  title,
  body,
  action,
}: {
  icon: ReactNode
  title: string
  body: string
  action: ReactNode
}) {
  return (
    <main className="flex min-h-svh items-center justify-center p-6">
      <div className="flex max-w-sm flex-col items-center gap-4 text-center">
        <div className="flex size-12 items-center justify-center rounded-2xl bg-muted [&_svg]:size-6">
          {icon}
        </div>
        <div className="space-y-1.5">
          <h1 className="text-xl font-semibold tracking-tight">{title}</h1>
          <p className="text-sm text-muted-foreground">{body}</p>
        </div>
        {action}
      </div>
    </main>
  )
}

/** Pages that need the analyst or admin role; others see why instead of errors. */
export function RequireExplore({ children }: { children: ReactNode }) {
  const session = useSession()
  if (session.status !== 'ready' || canExplore(session.me.role)) return children
  return (
    <div className="flex flex-col items-center gap-3 px-6 pt-[18vh] text-center">
      <div className="flex size-11 items-center justify-center rounded-2xl bg-muted">
        <KeyRound className="size-5" aria-hidden />
      </div>
      <h2 className="text-lg font-semibold tracking-tight">Not available for your role</h2>
      <p className="max-w-sm text-sm text-muted-foreground">
        The <strong className="font-medium text-foreground">{session.me.role}</strong> role can
        upload conversations and customers, but not explore them. Ask an admin of{' '}
        {session.me.tenant.name} for analyst access.
      </p>
    </div>
  )
}

function initials(me: Me): string {
  const source = me.email?.split('@')[0] ?? me.tenant.name
  const parts = source.split(/[^a-zA-Z0-9]+/).filter(Boolean)
  return ((parts[0]?.[0] ?? '?') + (parts[1]?.[0] ?? '')).toUpperCase()
}

/** Sidebar footer: who is signed in, to which tenant, with which role. */
export function AccountMenu() {
  const session = useSession()
  if (session.status === 'loading') return <Skeleton className="h-12 w-full" />
  if (session.status !== 'ready') return null
  const { me } = session
  const name = me.email ?? 'Shared API key'

  return (
    <SidebarMenu>
      <SidebarMenuItem>
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <SidebarMenuButton
              size="lg"
              className="data-[state=open]:bg-sidebar-accent"
              aria-label="Account"
            >
              <span className="flex aspect-square size-8 items-center justify-center rounded-lg bg-sidebar-accent text-xs font-semibold">
                {initials(me)}
              </span>
              <span className="grid flex-1 text-left leading-tight">
                <span className="truncate text-sm font-medium">{name}</span>
                <span className="truncate text-xs text-muted-foreground">{me.tenant.name}</span>
              </span>
              <ChevronsUpDown className="ml-auto size-4" aria-hidden />
            </SidebarMenuButton>
          </DropdownMenuTrigger>
          <DropdownMenuContent side="top" align="start" className="w-64">
            <DropdownMenuLabel className="space-y-1.5 font-normal">
              <p className="truncate text-sm font-medium">{name}</p>
              <div className="flex items-center gap-1.5">
                <Badge variant="secondary" className="capitalize">
                  {me.role}
                </Badge>
                <span className="truncate text-xs text-muted-foreground">
                  {me.tenant.name} · {me.tenant.slug}
                </span>
              </div>
            </DropdownMenuLabel>
            <DropdownMenuSeparator />
            {me.auth === 'oidc' ? (
              <DropdownMenuItem asChild>
                <a href={SIGN_OUT_URL}>
                  <LogOut aria-hidden /> Sign out
                </a>
              </DropdownMenuItem>
            ) : (
              <p className="px-2 py-1.5 text-xs text-muted-foreground">
                Signed in with the server's API key. Enable single sign-on to give each person
                their own access.
              </p>
            )}
          </DropdownMenuContent>
        </DropdownMenu>
      </SidebarMenuItem>
    </SidebarMenu>
  )
}
