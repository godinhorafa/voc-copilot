import { createContext, useContext } from 'react'

import type { Me, Role } from '@/lib/types'

export type Session =
  | { status: 'loading' }
  | { status: 'ready'; me: Me }
  /** Signed in with the identity provider, but not a user of any tenant. */
  | { status: 'denied'; message: string }
  /** The sign-in expired (401): a reload goes through the identity provider again. */
  | { status: 'expired' }
  | { status: 'error'; message: string }

export const SessionContext = createContext<Session>({ status: 'loading' })

export function useSession(): Session {
  return useContext(SessionContext)
}

/** Roles that may ask questions and see insights (mirrors the API's route roles). */
export function canExplore(role: Role): boolean {
  return role === 'admin' || role === 'analyst'
}
