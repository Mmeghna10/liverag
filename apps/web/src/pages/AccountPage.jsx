import { Check, Globe, KeyRound, Loader2, LogOut, Moon, Pencil, Shield, Sun } from 'lucide-react'
import { useEffect, useState } from 'react'
import { changePassword, updateMe } from '../api.js'
import { groupNames, initials, useSession } from '../session.jsx'
import { useTheme } from '../theme.js'

const card = 'rounded-2xl border border-slate-200 bg-white p-6 dark:border-white/10 dark:bg-white/5'
const input = 'w-full rounded-xl border border-slate-200 bg-white px-3.5 py-2.5 text-sm outline-none transition focus:border-brand-400 focus:ring-4 focus:ring-brand-100 dark:border-white/10 dark:bg-white/5 dark:focus:ring-brand-900'

function Stat({ label, value }) {
  return (
    <div className="rounded-xl bg-slate-50 p-4 dark:bg-white/5">
      <p className="text-xs text-slate-500 dark:text-slate-400">{label}</p>
      <p className="mt-1 text-2xl font-semibold tabular-nums">{value}</p>
    </div>
  )
}

/** Your profile, your access, your password. */
export default function AccountPage() {
  const { token, me, refresh, signOut } = useSession()
  const { dark, toggle } = useTheme()
  const [editing, setEditing] = useState(false)
  const [name, setName] = useState('')
  const [pw, setPw] = useState({ current: '', next: '' })
  const [pwState, setPwState] = useState({ busy: false, message: '', error: '' })

  // Fresh numbers (conversations, questions, groups) every time the page opens.
  useEffect(() => { refresh() }, [refresh])

  if (!me) return null
  const joined = new Date(me.created_at).toLocaleDateString(undefined, { day: 'numeric', month: 'long', year: 'numeric' })

  async function saveName(event) {
    event.preventDefault()
    await updateMe(token, name)
    await refresh()
    setEditing(false)
  }

  async function savePassword(event) {
    event.preventDefault()
    if (pw.next.length < 8) { setPwState({ busy: false, message: '', error: 'Use at least 8 characters.' }); return }
    setPwState({ busy: true, message: '', error: '' })
    try {
      await changePassword(token, pw.current, pw.next)
      setPw({ current: '', next: '' })
      setPwState({ busy: false, message: 'Password changed.', error: '' })
    } catch (err) {
      setPwState({ busy: false, message: '', error: err.message })
    }
  }

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-3xl space-y-6 px-4 py-10">
        <h1 className="text-2xl font-semibold tracking-tight">Your account</h1>

        <section className={card}>
          <div className="flex flex-wrap items-center gap-4">
            <div className="flex h-16 w-16 items-center justify-center rounded-full bg-brand-100 text-xl font-semibold text-brand-800 dark:bg-brand-900 dark:text-brand-100">
              {initials(me.display_name)}
            </div>
            <div className="min-w-0 flex-1">
              {editing ? (
                <form onSubmit={saveName} className="flex max-w-sm gap-2">
                  <input autoFocus className={input} value={name} onChange={(e) => setName(e.target.value)} required maxLength={80} />
                  <button className="rounded-xl bg-brand-600 px-3 text-white" aria-label="Save name"><Check size={16} /></button>
                </form>
              ) : (
                <p className="text-lg font-semibold">{me.display_name}</p>
              )}
              <p className="text-sm text-slate-500 dark:text-slate-400">{me.email} · joined {joined}</p>
            </div>
            {!editing && (
              <button onClick={() => { setName(me.display_name); setEditing(true) }}
                      className="flex items-center gap-1.5 rounded-xl border border-slate-200 px-3 py-2 text-sm hover:border-brand-300 hover:text-brand-700 dark:border-white/10">
                <Pencil size={14} /> Edit name
              </button>
            )}
          </div>
          <div className="mt-6 grid gap-3 sm:grid-cols-3">
            <Stat label="Conversations" value={me.conversations} />
            <Stat label="Questions asked" value={me.questions} />
            <Stat label="Groups" value={me.groups.length} />
          </div>
        </section>

        <section className={card}>
          <h2 className="font-semibold">Your access</h2>
          <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
            Answers only use documents your groups may read. Changes made by an admin apply within a second.
          </p>
          <div className="mt-4 flex flex-wrap gap-2">
            <span className="inline-flex items-center gap-1.5 rounded-full bg-slate-100 px-3 py-1 text-sm text-slate-700 dark:bg-white/10 dark:text-slate-200">
              <Globe size={14} /> Public documents
            </span>
            {groupNames(me.groups).map((g) => (
              <span key={g} className="inline-flex items-center gap-1.5 rounded-full bg-brand-100 px-3 py-1 text-sm text-brand-800 dark:bg-brand-900 dark:text-brand-100">
                <Shield size={14} /> {g}
              </span>
            ))}
            {me.groups.length === 0 && (
              <span className="rounded-full border border-dashed border-slate-300 px-3 py-1 text-sm text-slate-500 dark:border-white/20">
                No groups yet · an admin can add you
              </span>
            )}
          </div>
        </section>

        <section className={card}>
          <h2 className="flex items-center gap-2 font-semibold"><KeyRound size={18} /> Change password</h2>
          <form onSubmit={savePassword} className="mt-4 grid gap-3 sm:grid-cols-2">
            <input className={input} type="password" placeholder="Current password" autoComplete="current-password"
                   value={pw.current} onChange={(e) => setPw({ ...pw, current: e.target.value })} required />
            <input className={input} type="password" placeholder="New password (8+ characters)" autoComplete="new-password"
                   value={pw.next} onChange={(e) => setPw({ ...pw, next: e.target.value })} required />
            <div className="flex items-center gap-3 sm:col-span-2">
              <button disabled={pwState.busy} className="flex items-center gap-2 rounded-xl bg-brand-600 px-4 py-2 text-sm font-semibold text-white hover:bg-brand-700 disabled:opacity-60">
                {pwState.busy && <Loader2 size={14} className="animate-spin" />} Update password
              </button>
              {pwState.message && <span className="text-sm text-emerald-600 dark:text-emerald-400">{pwState.message}</span>}
              {pwState.error && <span className="text-sm text-red-600 dark:text-red-400">{pwState.error}</span>}
            </div>
          </form>
        </section>

        <section className={`${card} flex flex-wrap items-center justify-between gap-3`}>
          <button onClick={toggle} className="flex items-center gap-2 rounded-xl border border-slate-200 px-4 py-2 text-sm hover:border-brand-300 dark:border-white/10">
            {dark ? <Sun size={16} /> : <Moon size={16} />} {dark ? 'Switch to light mode' : 'Switch to dark mode'}
          </button>
          <button onClick={signOut} className="flex items-center gap-2 rounded-xl border border-red-200 px-4 py-2 text-sm text-red-600 hover:bg-red-50 dark:border-red-900 dark:text-red-400 dark:hover:bg-red-950/40">
            <LogOut size={16} /> Sign out
          </button>
        </section>
      </div>
    </div>
  )
}
