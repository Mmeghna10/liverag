import { useEffect, useState } from 'react'
import { AuthError, getMe } from './api.js'
import Login from './Login.jsx'
import Chat from './Chat.jsx'


// Group ids from the source DB seed (infra/postgres/source-db/init/02-seed.sql).
const GROUP_NAMES = { 1: 'support', 2: 'sales', 3: 'managers', 4: 'hr', 5: 'admins' }

// sessionStorage: survives a page refresh, cleared when the tab closes.
const TOKEN_KEY = 'liverag.token'

export default function App() {
  const [token, setToken] = useState(() => sessionStorage.getItem(TOKEN_KEY))
  const [user, setUser] = useState(null)
  const [error, setError] = useState('')

  function signIn(newToken) {
    sessionStorage.setItem(TOKEN_KEY, newToken)
    setToken(newToken)
  }

  function signOut() {
    sessionStorage.removeItem(TOKEN_KEY)
    setToken(null)
    setUser(null)
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

  if (!token) return <Login onLogin={signIn} />
  if (error) return <main className="center"><p className="error">{error}</p></main>
  if (!user) return <main className="center"><p className="muted">Loading…</p></main>

  const groups = user.groups.map((id) => GROUP_NAMES[id] ?? `group ${id}`)
  return (
    <div className="app">
      <header>
        <strong>LiveRAG</strong>
        <span className="muted">
          {user.email} · {groups.length ? groups.join(', ') : 'public only'}
        </span>
        <button type="button" onClick={signOut}>Sign out</button>
      </header>
            {/* key={token}: a new sign-in starts a fresh chat, so one user never sees another's answers */}
      <Chat key={token} token={token} onAuthError={signOut} />
    </div>
  )
}