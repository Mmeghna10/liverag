import { useEffect, useState } from 'react'
import { AuthError, getMe, getUsers } from './api.js'
import Chat from './Chat.jsx'
import Login from './Login.jsx'

// Group ids from the source DB seed (infra/postgres/source-db/init/02-seed.sql).
const GROUP_NAMES = { 1: 'support', 2: 'sales', 3: 'managers', 4: 'hr', 5: 'admins' }
const ADMIN_GROUP_ID = 5

/** [1, 3] -> "support, managers"; [] -> "public only" */
function groupLabel(ids) {
  return ids.length ? ids.map((id) => GROUP_NAMES[id] ?? `group ${id}`).join(', ') : 'public only'
}

// sessionStorage: survives a page refresh, cleared when the tab closes.
const TOKEN_KEY = 'liverag.token'

export default function App() {
  const [token, setToken] = useState(() => sessionStorage.getItem(TOKEN_KEY))
  const [user, setUser] = useState(null)
  const [error, setError] = useState('')
  const [users, setUsers] = useState([]) // filled for admins only
  const [viewAs, setViewAs] = useState('') // '' = myself

  function signIn(newToken) {
    sessionStorage.setItem(TOKEN_KEY, newToken)
    setToken(newToken)
  }

  function signOut() {
    sessionStorage.removeItem(TOKEN_KEY)
    setToken(null)
    setUser(null)
    setUsers([])
    setViewAs('')
  }

  // Whenever the token changes, ask the API who we are. An expired token sends us back to login.
  useEffect(() => {
    if (!token) return
    let cancelled = false // ignore the answer if the token changed while we waited
    getMe(token)
      .then((me) => { if (!cancelled) { setUser(me); setError('') } })
      .catch((err) => {
        if (cancelled) return
        if (err instanceof AuthError) signOut()
        else setError(err.message)
      })
    return () => { cancelled = true }
  }, [token])

  // Admins get the list of users for the "view as" picker. The server refuses everyone else.
  const isAdmin = user?.groups.includes(ADMIN_GROUP_ID) ?? false
  useEffect(() => {
    if (!isAdmin) return
    let cancelled = false
    getUsers(token)
      .then((list) => { if (!cancelled) setUsers(list) })
      .catch(() => {}) // the picker is optional; the chat still works without it
    return () => { cancelled = true }
  }, [isAdmin, token])

  if (!token) return <Login onLogin={signIn} />
  if (error) return <main className="center"><p className="error">{error}</p></main>
  if (!user) return <main className="center"><p className="muted">Loading…</p></main>

  const viewed = users.find((u) => u.email === viewAs)
  return (
    <div className="app">
      <header>
        <strong>LiveRAG</strong>
        <span className="muted">
          {user.email} · {groupLabel(user.groups)}
        </span>
        {isAdmin && users.length > 0 && (
          <label className="view-as">
            View as
            <select value={viewAs} onChange={(e) => setViewAs(e.target.value)}>
              <option value="">myself</option>
              {users.filter((u) => u.email !== user.email).map((u) => (
                <option key={u.email} value={u.email}>{u.email} ({groupLabel(u.groups)})</option>
              ))}
            </select>
          </label>
        )}
        <button type="button" onClick={signOut}>Sign out</button>
      </header>
      {viewed && (
        <p className="banner">
          Answering as <strong>{viewed.email}</strong> ({groupLabel(viewed.groups)}).
          The server applies this user's permissions.
        </p>
      )}
      {/* key: a new sign-in or "view as" choice starts a fresh chat, so answers never mix */}
      <Chat key={`${token}|${viewAs}`} token={token} viewAs={viewAs} onAuthError={signOut} />
    </div>
  )
}