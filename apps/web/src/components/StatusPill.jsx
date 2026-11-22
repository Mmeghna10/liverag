import { useEffect, useState } from 'react'
import { getStatus } from '../api.js'
import { useSession } from '../session.jsx'

const STYLES = {
  live: { dot: 'bg-emerald-500', text: 'Live sync', pill: 'bg-emerald-50 text-emerald-800 dark:bg-emerald-950/60 dark:text-emerald-300' },
  catching_up: { dot: 'bg-amber-500', text: 'Catching up', pill: 'bg-amber-50 text-amber-800 dark:bg-amber-950/60 dark:text-amber-300' },
  paused: { dot: 'bg-slate-400', text: 'Sync paused', pill: 'bg-slate-100 text-slate-600 dark:bg-white/5 dark:text-slate-400' },
  off: { dot: 'bg-slate-400', text: 'Sync off', pill: 'bg-slate-100 text-slate-600 dark:bg-white/5 dark:text-slate-400' },
}

/** Shows whether the CDC pipeline is running, refreshed every 5 seconds. */
export default function StatusPill() {
  const { token } = useSession()
  const [status, setStatus] = useState(null)

  useEffect(() => {
    let alive = true
    const load = () => getStatus(token).then((s) => alive && setStatus(s)).catch(() => {})
    load()
    const timer = setInterval(load, 5000)
    return () => { alive = false; clearInterval(timer) }
  }, [token])

  if (!status) return null
  const style = STYLES[status.state] ?? STYLES.off
  const extra = status.state === 'catching_up' && status.messages_waiting
    ? ` · ${status.messages_waiting} waiting` : ''
  return (
    <span title="Is the search index following the database right now?"
          className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium ${style.pill}`}>
      <span className="relative flex h-2 w-2">
        {status.state === 'live' && <span className={`absolute inline-flex h-full w-full animate-ping rounded-full opacity-60 ${style.dot}`} />}
        <span className={`relative inline-flex h-2 w-2 rounded-full ${style.dot}`} />
      </span>
      {style.text}{extra}
    </span>
  )
}
