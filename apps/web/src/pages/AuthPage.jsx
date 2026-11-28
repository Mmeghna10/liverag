import { ArrowRight, BadgeCheck, Loader2, Lock, Quote, Zap } from 'lucide-react'
import { useState } from 'react'
import { Link, Navigate, useNavigate } from 'react-router-dom'
import { login, signup } from '../api.js'
import Logo from '../components/Logo.jsx'
import { useSession } from '../session.jsx'

const FEATURES = [
  { icon: Zap, title: 'Always up to date', text: 'Database changes reach search in about 0.2 seconds.' },
  { icon: Lock, title: 'Permission-aware', text: 'You only ever get answers from documents you may read.' },
  { icon: Quote, title: 'Cited answers', text: 'Every answer links to the records it came from.' },
]

// Demo accounts from the simulator seed (password = DEMO_USER_PASSWORD in .env).
// A public demo deployment sets VITE_DEMO_PASSWORD at build time so the buttons fill it in too.
const DEMO_PASSWORD = import.meta.env.VITE_DEMO_PASSWORD || ''
const DEMO = [
  { email: 'customer@example.com', label: 'Customer' },
  { email: 'agent@northwind.test', label: 'Support agent' },
  { email: 'manager@northwind.test', label: 'Manager' },
  { email: 'admin@northwind.test', label: 'Admin' },
]

const input = 'w-full rounded-xl border border-slate-200 bg-white px-3.5 py-2.5 text-sm outline-none transition ' +
  'placeholder:text-slate-400 focus:border-brand-400 focus:ring-4 focus:ring-brand-100 ' +
  'dark:border-white/10 dark:bg-white/5 dark:focus:ring-brand-900'

/** Sign in or create an account: brand panel on the left, form on the right. */
export default function AuthPage({ mode }) {
  const { token, signIn } = useSession()
  const navigate = useNavigate()
  const [name, setName] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const isSignup = mode === 'signup'

  if (token) return <Navigate to="/chat" replace />

  async function handleSubmit(event) {
    event.preventDefault()
    setError('')
    if (isSignup && password.length < 8) {
      setError('Use at least 8 characters for your password.')
      return
    }
    setBusy(true)
    try {
      const newToken = isSignup ? await signup(name, email, password) : await login(email, password)
      signIn(newToken)
      navigate('/chat')
    } catch (err) {
      setError(isSignup ? err.message : 'Wrong email or password.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="grid min-h-full lg:grid-cols-2">
      {/* Brand panel */}
      <section className="relative hidden overflow-hidden bg-brand-600 p-12 text-brand-50 lg:flex lg:flex-col">
        <div className="absolute -right-24 -top-24 h-80 w-80 rounded-full bg-brand-500/50" />
        <div className="absolute -bottom-32 -left-16 h-96 w-96 rounded-full bg-brand-700/60" />
        <div className="relative flex items-center gap-2.5">
          <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-brand-50 text-brand-700">
            <Zap size={20} strokeWidth={2.4} />
          </div>
          <span className="text-xl font-semibold tracking-tight">LiveRAG</span>
        </div>
        <div className="relative my-auto max-w-md py-10">
          <h1 className="text-4xl font-semibold leading-tight tracking-tight">
            Answers that never come from stale or revoked data.
          </h1>
          <p className="mt-4 text-brand-100">
            A company assistant whose knowledge follows the live database, change by change.
          </p>
          <ul className="mt-10 space-y-5">
            {FEATURES.map((f) => (
              <li key={f.title} className="flex gap-3.5">
                <div className="flex h-9 w-9 flex-none items-center justify-center rounded-lg bg-white/10">
                  <f.icon size={18} />
                </div>
                <div>
                  <p className="font-medium">{f.title}</p>
                  <p className="text-sm text-brand-100">{f.text}</p>
                </div>
              </li>
            ))}
          </ul>
        </div>
        <p className="relative text-xs text-brand-200">Northwind Gear demo · Dissertation project</p>
      </section>

      {/* Form */}
      <section className="flex items-center justify-center px-6 py-12">
        <div className="w-full max-w-sm">
          <div className="mb-8 lg:hidden"><Logo size="lg" /></div>
          <h2 className="text-2xl font-semibold tracking-tight">
            {isSignup ? 'Create your account' : 'Welcome back'}
          </h2>
          <p className="mt-1.5 text-sm text-slate-500 dark:text-slate-400">
            {isSignup ? 'New accounts can read all public information.' : 'Sign in to continue to LiveRAG.'}
          </p>

          <form onSubmit={handleSubmit} className="mt-8 space-y-4">
            {isSignup && (
              <label className="block text-sm font-medium">
                Name
                <input className={`${input} mt-1.5`} value={name} onChange={(e) => setName(e.target.value)}
                       placeholder="Meghna Rajput" autoComplete="name" required />
              </label>
            )}
            <label className="block text-sm font-medium">
              Email
              <input className={`${input} mt-1.5`} type="email" value={email}
                     onChange={(e) => setEmail(e.target.value)} placeholder="you@example.com"
                     autoComplete="username" required />
            </label>
            <label className="block text-sm font-medium">
              Password
              <input className={`${input} mt-1.5`} type="password" value={password}
                     onChange={(e) => setPassword(e.target.value)}
                     placeholder={isSignup ? 'At least 8 characters' : '••••••••'}
                     autoComplete={isSignup ? 'new-password' : 'current-password'} required />
            </label>
            {error && (
              <p role="alert" className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700 dark:bg-red-950/50 dark:text-red-300">
                {error}
              </p>
            )}
            <button type="submit" disabled={busy}
                    className="flex w-full items-center justify-center gap-2 rounded-xl bg-brand-600 py-2.5 text-sm font-semibold text-white transition hover:bg-brand-700 disabled:opacity-60">
              {busy ? <Loader2 size={16} className="animate-spin" /> : <ArrowRight size={16} />}
              {isSignup ? 'Create account' : 'Sign in'}
            </button>
          </form>

          <p className="mt-6 text-center text-sm text-slate-500 dark:text-slate-400">
            {isSignup ? 'Already have an account? ' : 'New to LiveRAG? '}
            <Link to={isSignup ? '/login' : '/signup'} className="font-medium text-brand-600 hover:underline dark:text-brand-300">
              {isSignup ? 'Sign in' : 'Create an account'}
            </Link>
          </p>

          {!isSignup && (
            <div className="mt-10 border-t border-slate-200 pt-6 dark:border-white/10">
              <p className="flex items-center gap-1.5 text-xs font-medium text-slate-500 dark:text-slate-400">
                <BadgeCheck size={14} /> Demo accounts (fills in the email)
              </p>
              <div className="mt-3 flex flex-wrap gap-2">
                {DEMO.map((d) => (
                  <button key={d.email} type="button" onClick={() => { setEmail(d.email); if (DEMO_PASSWORD) setPassword(DEMO_PASSWORD) }}
                          className="rounded-full border border-slate-200 px-3 py-1 text-xs transition hover:border-brand-300 hover:bg-brand-50 hover:text-brand-800 dark:border-white/10 dark:hover:bg-brand-950 dark:hover:text-brand-200">
                    {d.label}
                  </button>
                ))}
              </div>
            </div>
          )}
        </div>
      </section>
    </div>
  )
}
