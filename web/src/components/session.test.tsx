import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { SidebarProvider } from '@/components/ui/sidebar'
import type { Me } from '@/lib/types'

import { AccountMenu, RequireExplore, SessionGate, SessionProvider } from './session'

afterEach(() => {
  vi.unstubAllGlobals()
  // An open Radix menu disables pointer events on <body>; don't leak that between tests.
  document.body.style.pointerEvents = ''
})

const ME: Me = {
  auth: 'oidc',
  role: 'analyst',
  email: 'ana.lima@acme.test',
  tenant: { id: 't1', slug: 'acme', name: 'Acme Telecom' },
}

function respond(status: number, body: unknown) {
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(body), { status })))
}

function renderApp(children = <p>the app</p>) {
  return render(
    <SessionProvider>
      <SessionGate>
        <SidebarProvider>{children}</SidebarProvider>
      </SessionGate>
    </SessionProvider>,
  )
}

describe('session', () => {
  it('blocks signed-in people who are not users of any tenant', async () => {
    respond(403, { detail: 'bo@x.test has no access to VOC Copilot; ask an admin to add you' })
    renderApp()
    expect(await screen.findByText("You don't have access yet")).toBeInTheDocument()
    expect(screen.getByText(/bo@x.test has no access/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /another account/ })).toHaveAttribute(
      'href',
      '/oauth2/sign_out?rd=%2F',
    )
    expect(screen.queryByText('the app')).not.toBeInTheDocument()
  })

  it('asks to sign in again when the session expired', async () => {
    respond(401, { detail: 'token expired' })
    renderApp()
    expect(await screen.findByText('Your session expired')).toBeInTheDocument()
  })

  it('explains pages a role cannot use instead of failing', async () => {
    respond(200, { ...ME, role: 'ingest' })
    renderApp(
      <RequireExplore>
        <p>insights</p>
      </RequireExplore>,
    )
    expect(await screen.findByText('Not available for your role')).toBeInTheDocument()
    expect(screen.queryByText('insights')).not.toBeInTheDocument()
  })

  it('shows the account with its tenant, role and sign-out', async () => {
    respond(200, ME)
    renderApp(
      <>
        <AccountMenu />
        <RequireExplore>
          <p>insights</p>
        </RequireExplore>
      </>,
    )
    expect(await screen.findByText('insights')).toBeInTheDocument()
    expect(screen.getByText('AL')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Account' }))
    expect(await screen.findByText('analyst')).toBeInTheDocument()
    expect(screen.getByText('Acme Telecom · acme')).toBeInTheDocument()
    expect(screen.getByRole('menuitem', { name: /Sign out/ })).toHaveAttribute(
      'href',
      '/oauth2/sign_out?rd=%2F',
    )
  })

  it('has no sign-out for the shared API key', async () => {
    respond(200, { ...ME, auth: 'api_key', role: 'admin', email: null })
    renderApp(<AccountMenu />)
    await userEvent.click(await screen.findByRole('button', { name: 'Account' }))
    expect(await screen.findByText(/Signed in with the server's API key/)).toBeInTheDocument()
    expect(screen.queryByRole('menuitem', { name: /Sign out/ })).not.toBeInTheDocument()
  })
})
