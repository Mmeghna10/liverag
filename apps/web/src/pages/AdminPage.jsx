import { Activity, Database, Eye, Info, Layers, Loader2, Radio, Search } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { Navigate, useNavigate } from 'react-router-dom'
import { getGroups, getPipeline, getUsers, setMembership } from '../api.js'
import { initials, useSession } from '../session.jsx'

const STATE_TEXT = {
  live: ['Live', 'text-emerald-600 dark:text-emerald-400'],
  catching_up: ['Catching up', 'text-amber-600 dark:text-amber-400'],
  paused: ['Paused', 'text-slate-500'],
  off: ['Off', 'text-slate-500'],
}

function ago(dateText) {
  if (!dateText) return 'never'
  const s = Math.max(0, Math.round((Date.now() - new Date(dateText).getTime()) / 1000))
  if (s < 60) return `${s} s ago`
  if (s < 3600) return `${Math.round(s / 60)} min ago`
  if (s < 86400) return `${Math.round(s / 3600)} h ago`
  return `${Math.round(s / 86400)} days ago`
}

function bytes(n) {
  if (n == null) return '—'
  return n < 1024 ? `${n} B` : n < 1048576 ? `${(n / 1024).toFixed(0)} kB` : `${(n / 1048576).toFixed(1)} MB`
}

function Metric({ icon, label, value, hint, tone }) {
  const Icon = icon
  return (
    <div className="rounded-2xl border border-slate-200 bg-white p-4 dark:border-white/10 dark:bg-white/5">
      <p className="flex items-center gap-1.5 text-xs text-slate-500 dark:text-slate-400"><Icon size={14} /> {label}</p>
      <p className={`mt-1.5 text-2xl font-semibold tabular-nums ${tone ?? ''}`}>{value}</p>
      {hint && <p className="mt-0.5 text-xs text-slate-400">{hint}</p>}
    </div>
  )
}

function Toggle({ on, pending, onChange, label }) {
  return (
    <button role="switch" aria-checked={on} aria-label={label} onClick={onChange}
            className={`relative inline-flex h-6 w-11 flex-none items-center rounded-full transition ${on ? 'bg-brand-600' : 'bg-slate-200 dark:bg-white/15'}`}>
      <span className={`inline-block h-5 w-5 transform rounded-full bg-white shadow transition ${on ? 'translate-x-5' : 'translate-x-0.5'}`} />
      {pending && <Loader2 size={12} className="absolute -right-4 animate-spin text-brand-500" />}
    </button>
  )
}

/** Pipeline health + who may read what. Toggles write to the source DB; CDC does the rest. */
export default function AdminPage() {
  const { token, me, isAdmin, setViewAs, refresh } = useSession()
  const navigate = useNavigate()
  const [pipeline, setPipeline] = useState(null)
  const [groups, setGroups] = useState([])
  const [users, setUsers] = useState([])
  const [filter, setFilter] = useState('')
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    try {
      const [p, u] = await Promise.all([getPipeline(token), getUsers(token)])
      setPipeline(p)
      setUsers(u)
    } catch (err) { setError(err.message) }
  }, [token])

  useEffect(() => { getGroups(token).then(setGroups).catch(() => {}) }, [token])
  useEffect(() => {
    load()
    const timer = setInterval(load, 2000) // fast refresh so you can watch a change sync
    return () => clearInterval(timer)
  }, [load])

  if (me && !isAdmin) return <Navigate to="/chat" replace />

  async function toggle(user, groupId) {
    const member = !user.groups.includes(groupId)
    setError('')
    try {
      await setMembership(token, user.id, groupId, member)
      await load()
      if (user.id === me.id) setTimeout(refresh, 800)
    } catch (err) { setError(err.message) }
  }

  const [stateText, stateTone] = STATE_TEXT[pipeline?.state] ?? ['…', '']
  const shown = users.filter((u) => `${u.email} ${u.display_name}`.toLowerCase().includes(filter.toLowerCase()))

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-6xl space-y-8 px-4 py-10">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Admin console</h1>
          <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">Watch the sync pipeline and manage who can read what.</p>
        </div>

        <section className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
          <Metric icon={Activity} label="Sync" value={stateText} tone={stateTone}
                  hint={pipeline?.reader_connected ? 'CDC reader connected' : 'CDC reader not running'} />
          <Metric icon={Radio} label="Waiting to index" value={pipeline?.messages_waiting ?? '—'}
                  hint={pipeline ? `offset ${pipeline.indexer_offset ?? '—'} of ${pipeline.topic_end ?? '—'}` : ''} />
          <Metric icon={Database} label="WAL held by slot" value={bytes(pipeline?.slot_wal_bytes)} hint={pipeline?.confirmed_lsn ? `confirmed ${pipeline.confirmed_lsn}` : 'no slot'} />
          <Metric icon={Layers} label="Indexed chunks" value={pipeline?.chunks?.toLocaleString() ?? '—'} hint="in chunks_v1" />
          <Metric icon={Activity} label="Last change applied" value={ago(pipeline?.last_change_at)} hint="by the indexer" />
        </section>

        <section className="overflow-hidden rounded-2xl border border-slate-200 bg-white dark:border-white/10 dark:bg-white/5">
          <div className="flex flex-wrap items-center gap-3 border-b border-slate-200 p-4 dark:border-white/10">
            <div>
              <h2 className="font-semibold">Users and access</h2>
              <p className="text-xs text-slate-500 dark:text-slate-400">{users.length} users</p>
            </div>
            <label className="ml-auto flex items-center gap-2 rounded-xl border border-slate-200 px-3 py-1.5 text-sm dark:border-white/10">
              <Search size={14} className="text-slate-400" />
              <input value={filter} onChange={(e) => setFilter(e.target.value)} placeholder="Search users"
                     className="w-44 bg-transparent outline-none" />
            </label>
          </div>
          {error && <p className="bg-red-50 px-4 py-2 text-sm text-red-700 dark:bg-red-950/50 dark:text-red-300">{error}</p>}
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-xs text-slate-500 dark:text-slate-400">
                  <th className="px-4 py-2.5 font-medium">User</th>
                  {groups.map((g) => <th key={g.id} className="px-3 py-2.5 font-medium">{g.name === 'hr' ? 'HR' : g.name[0].toUpperCase() + g.name.slice(1)}</th>)}
                  <th className="px-4 py-2.5" />
                </tr>
              </thead>
              <tbody>
                {shown.map((u) => (
                  <tr key={u.id} className="border-t border-slate-100 dark:border-white/5">
                    <td className="px-4 py-2.5">
                      <div className="flex items-center gap-3">
                        <div className="flex h-8 w-8 flex-none items-center justify-center rounded-full bg-brand-100 text-xs font-semibold text-brand-800 dark:bg-brand-900 dark:text-brand-100">
                          {initials(u.display_name)}
                        </div>
                        <div className="min-w-0">
                          <p className="truncate font-medium">{u.display_name}{u.id === me?.id && <span className="ml-1.5 text-xs text-slate-400">(you)</span>}</p>
                          <p className="truncate text-xs text-slate-500 dark:text-slate-400">{u.email}</p>
                        </div>
                      </div>
                    </td>
                    {groups.map((g) => {
                      const on = u.groups.includes(g.id)
                      const synced = on === u.effective_groups.includes(g.id)
                      return (
                        <td key={g.id} className="px-3 py-2.5">
                          <Toggle on={on} pending={!synced} label={`${g.name} for ${u.email}`} onChange={() => toggle(u, g.id)} />
                        </td>
                      )
                    })}
                    <td className="px-4 py-2.5 text-right">
                      {u.id !== me?.id && (
                        <button onClick={() => { setViewAs(u.email); navigate('/chat') }}
                                className="inline-flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-xs font-medium text-brand-700 hover:bg-brand-50 dark:text-brand-300 dark:hover:bg-brand-950">
                          <Eye size={14} /> View as
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="flex items-start gap-2 border-t border-slate-200 px-4 py-3 text-xs text-slate-500 dark:border-white/10 dark:text-slate-400">
            <Info size={14} className="mt-px flex-none" />
            A toggle writes to the company database. The change reaches search through CDC, usually within a second.
            The spinner shows while search is still using the old access.
          </p>
        </section>
      </div>
    </div>
  )
}
