// Who is signed in, shared with every page through React context.
import { createContext, useCallback, useContext, useEffect, useState } from 'react'
import { AuthError, getMe } from './api.js'

const TOKEN_KEY = 'liverag.token' // sessionStorage: survives a refresh, cleared when the tab closes
// eslint-disable-next-line react-refresh/only-export-components
export const GROUP_NAMES = { 1: 'support', 2: 'sales', 3: 'managers', 4: 'hr', 5: 'admins' }
export const ADMIN_GROUP_ID = 5

const SessionContext = createContext(null)

function readToken() {
  try { return sessionStorage.getItem(TOKEN_KEY) } catch { return null }
}

export function SessionProvider({ children }) {
  const [token, setToken] = useState(readToken)
  const [me, setMe] = useState(null)
  const [viewAs, setViewAs] = useState('') // admins only: answer as this email ('' = myself)

  const signOut = useCallback(() => {
    try { sessionStorage.removeItem(TOKEN_KEY) } catch { /* ignore */ }
    setToken(null)
    setMe(null)
    setViewAs('')
  }, [])

  const signIn = useCallback((newToken) => {
    try { sessionStorage.setItem(TOKEN_KEY, newToken) } catch { /* ignore */ }
    setToken(newToken)
  }, [])

  // Reload the profile; an expired token signs you out.
  const refresh = useCallback(async () => {
    if (!token) return
    try {
      setMe(await getMe(token))
    } catch (err) {
      if (err instanceof AuthError) signOut()
    }
  }, [token, signOut])

  useEffect(() => { refresh() }, [refresh])

  const isAdmin = me?.groups.includes(ADMIN_GROUP_ID) ?? false
  const value = { token, me, isAdmin, viewAs, setViewAs, signIn, signOut, refresh }
  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>
}

// eslint-disable-next-line react-refresh/only-export-components
export function useSession() {
  return useContext(SessionContext)
}

/** "Meghna Rajput" -> "MR" */
// eslint-disable-next-line react-refresh/only-export-components
export function initials(name = '') {
  return name.split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0].toUpperCase()).join('') || '?'
}

/** [1, 3] -> ["support", "managers"] */
// eslint-disable-next-line react-refresh/only-export-components
export function groupNames(ids = []) {
  return ids.map((id) => GROUP_NAMES[id] ?? `group ${id}`)
}
