import { useState } from 'react'
import { login } from './api.js'

// Demo accounts from the simulator seed. Clicking one only fills in the email;
// you still type the password (DEMO_USER_PASSWORD in .env).
const DEMO_USERS = [
  { email: 'customer@example.com', label: 'Customer' },
  { email: 'agent@northwind.test', label: 'Support agent' },
  { email: 'manager@northwind.test', label: 'Manager' },
  { email: 'admin@northwind.test', label: 'Admin' },
]

/**
 * Sign-in form. Calls onLogin(token) after a successful login.
 * @param {{onLogin: (token: string) => void}} props
 */
export default function Login({ onLogin }) {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  async function handleSubmit(event) {
    event.preventDefault() // stop the browser from reloading the page
    setBusy(true)
    setError('')
    try {
      onLogin(await login(email, password))
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <main className="login">
      <h1>LiveRAG</h1>
      <p className="muted">Northwind Outdoors help assistant</p>

      <form onSubmit={handleSubmit}>
        <label>
          Email
          <input type="email" value={email} onChange={(e) => setEmail(e.target.value)}
                 autoComplete="username" required />
        </label>
        <label>
          Password
          <input type="password" value={password} onChange={(e) => setPassword(e.target.value)}
                 autoComplete="current-password" required />
        </label>
        {error && <p className="error" role="alert">{error}</p>}
        <button type="submit" disabled={busy}>{busy ? 'Signing in…' : 'Sign in'}</button>
      </form>

      <p className="muted small">Demo accounts:</p>
      <div className="chips">
        {DEMO_USERS.map((user) => (
          <button key={user.email} type="button" className="chip"
                  onClick={() => setEmail(user.email)}>
            {user.label}
          </button>
        ))}
      </div>
    </main>
  )
}