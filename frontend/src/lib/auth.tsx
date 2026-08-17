/* Session state.
 *
 * The token lives in localStorage rather than an httpOnly cookie, which is a
 * deliberate trade-off for a device-shared deployment: a kiosk or a ward
 * terminal is used by many people, and an explicit visible session that can be
 * ended with one tap is safer here than an invisible cookie that persists.
 */

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import { ApiError, api, tokens } from './api'
import type { User } from './api'

type AuthState = {
  user: User | null
  loading: boolean
  login: (phone: string, password: string) => Promise<User>
  register: (body: {
    phone: string
    full_name: string
    password: string
    preferred_language?: string
  }) => Promise<User>
  logout: () => void
}

const AuthContext = createContext<AuthState | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let cancelled = false
    async function restore() {
      if (!tokens.access) {
        setLoading(false)
        return
      }
      try {
        const me = await api.me()
        if (!cancelled) setUser(me)
      } catch (err) {
        // An expired or revoked token should log the person out quietly
        // rather than leaving the app in a half-authenticated state.
        if (err instanceof ApiError && err.status === 401) tokens.clear()
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    void restore()
    return () => {
      cancelled = true
    }
  }, [])

  const login = useCallback(async (phone: string, password: string) => {
    const pair = await api.login(phone, password)
    tokens.set(pair.access_token, pair.refresh_token)
    const me = await api.me()
    setUser(me)
    return me
  }, [])

  const register = useCallback<AuthState['register']>(async (body) => {
    await api.register(body)
    const pair = await api.login(body.phone, body.password)
    tokens.set(pair.access_token, pair.refresh_token)
    const me = await api.me()
    setUser(me)
    return me
  }, [])

  const logout = useCallback(() => {
    tokens.clear()
    setUser(null)
  }, [])

  const value = useMemo(
    () => ({ user, loading, login, register, logout }),
    [user, loading, login, register, logout],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used inside AuthProvider')
  return ctx
}
