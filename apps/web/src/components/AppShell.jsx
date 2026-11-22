import { Check, LogOut, Menu, MessageSquare, Moon, Pencil, Plus, Shield, Sun, Trash2, User, X } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { Link, NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom'
import { deleteConversation, listConversations, renameConversation } from '../api.js'
import { initials, useSession } from '../session.jsx'
import { useTheme } from '../theme.js'
import Logo from './Logo.jsx'
import StatusPill from './StatusPill.jsx'

/** "Today", "Yesterday", "Previous 7 days" or "Older", for grouping the chat list. */
function bucket(dateText) {
  const days = (Date.now() - new Date(dateText).getTime()) / 86_400_000
  if (new Date(dateText).toDateString() === new Date().toDateString()) return 'Today'
  if (days < 2) return 'Yesterday'
  if (days < 7) return 'Previous 7 days'
  return 'Older'
}

function ConversationItem({ conversation, active, onRename, onDelete }) {
  const [editing, setEditing] = useState(false)
  const [title, setTitle] = useState(conversation.title)

  if (editing) {
    return (
      <form className="flex items-center gap-1 rounded-lg bg-white px-2 py-1 shadow-sm dark:bg-white/10"
            onSubmit={(e) => { e.preventDefault(); onRename(title); setEditing(false) }}>
        <input autoFocus value={title} onChange={(e) => setTitle(e.target.value)}
               className="min-w-0 flex-1 bg-transparent text-sm outline-none" />
        <button type="submit" aria-label="Save title" className="p-1 text-brand-600"><Check size={14} /></button>
        <button type="button" aria-label="Cancel" onClick={() => setEditing(false)} className="p-1 text-slate-400"><X size={14} /></button>
      </form>
    )
  }
  return (
    <div className={`group flex items-center rounded-lg text-sm transition ${active
      ? 'bg-brand-100/70 text-brand-900 dark:bg-brand-900/50 dark:text-brand-100'
      : 'text-slate-600 hover:bg-slate-200/60 dark:text-slate-300 dark:hover:bg-white/5'}`}>
      <Link to={`/chat/${conversation.id}`} className="min-w-0 flex-1 truncate px-3 py-2">{conversation.title}</Link>
      <div className="hidden flex-none pr-1.5 group-hover:flex">
        <button aria-label="Rename" onClick={() => setEditing(true)} className="rounded p-1 hover:text-brand-600"><Pencil size={13} /></button>
        <button aria-label="Delete" onClick={onDelete} className="rounded p-1 hover:text-red-600"><Trash2 size={13} /></button>
      </div>
    </div>
  )
}

/** Signed-in layout: chat list on the left, page on the right, top bar with status and menu. */
export default function AppShell() {
  const { token, me, isAdmin, signOut } = useSession()
  const { dark, toggle } = useTheme()
  const [conversations, setConversations] = useState([])
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [menuOpen, setMenuOpen] = useState(false)
  const navigate = useNavigate()
  const location = useLocation()
  // The layout sits above /chat/:id, so read the open conversation's id from the URL itself.
  const activeId = location.pathname.match(/^\/chat\/([^/]+)/)?.[1]

  const reload = useCallback(() => {
    listConversations(token).then(setConversations).catch(() => {})
  }, [token])
  useEffect(() => { reload() }, [reload])
  useEffect(() => { setSidebarOpen(false); setMenuOpen(false) }, [location.pathname])

  async function remove(id) {
    if (!window.confirm('Delete this conversation? This cannot be undone.')) return
    await deleteConversation(token, id)
    reload()
    if (id === activeId) navigate('/chat')
  }

  async function rename(id, title) {
    await renameConversation(token, id, title)
    reload()
  }

  const groups = {}
  for (const c of conversations) (groups[bucket(c.updated_at)] ??= []).push(c)

  const sidebar = (
    <aside className="flex h-full w-72 flex-col border-r border-slate-200 bg-slate-100 dark:border-white/10 dark:bg-[#12111c]">
      <div className="flex items-center justify-between px-4 py-4">
        <Link to="/chat"><Logo /></Link>
        <button className="rounded-lg p-1.5 text-slate-500 lg:hidden" aria-label="Close menu" onClick={() => setSidebarOpen(false)}><X size={18} /></button>
      </div>
      <div className="px-3">
        <button onClick={() => navigate('/chat')}
                className="flex w-full items-center justify-center gap-2 rounded-xl bg-brand-600 py-2.5 text-sm font-semibold text-white shadow-sm transition hover:bg-brand-700">
          <Plus size={16} /> New chat
        </button>
      </div>
      <nav className="mt-4 flex-1 space-y-5 overflow-y-auto px-3 pb-4">
        {conversations.length === 0 && (
          <p className="px-3 text-sm text-slate-500 dark:text-slate-400">
            <MessageSquare size={14} className="mb-1" />Your conversations will appear here.
          </p>
        )}
        {['Today', 'Yesterday', 'Previous 7 days', 'Older'].filter((g) => groups[g]).map((g) => (
          <div key={g}>
            <p className="px-3 pb-1 text-xs font-medium text-slate-400">{g}</p>
            <div className="space-y-0.5">
              {groups[g].map((c) => (
                <ConversationItem key={c.id} conversation={c} active={c.id === activeId}
                                  onRename={(t) => rename(c.id, t)} onDelete={() => remove(c.id)} />
              ))}
            </div>
          </div>
        ))}
      </nav>
      <div className="border-t border-slate-200 p-3 dark:border-white/10">
        {isAdmin && (
          <NavLink to="/admin" className={({ isActive }) => `mb-1 flex items-center gap-2 rounded-lg px-3 py-2 text-sm ${isActive ? 'bg-brand-100/70 text-brand-900 dark:bg-brand-900/50 dark:text-brand-100' : 'text-slate-600 hover:bg-slate-200/60 dark:text-slate-300 dark:hover:bg-white/5'}`}>
            <Shield size={16} /> Admin console
          </NavLink>
        )}
        <NavLink to="/account" className="flex items-center gap-3 rounded-lg px-2 py-2 hover:bg-slate-200/60 dark:hover:bg-white/5">
          <div className="flex h-9 w-9 flex-none items-center justify-center rounded-full bg-brand-100 text-sm font-semibold text-brand-800 dark:bg-brand-900 dark:text-brand-100">
            {initials(me?.display_name)}
          </div>
          <div className="min-w-0">
            <p className="truncate text-sm font-medium">{me?.display_name ?? '…'}</p>
            <p className="truncate text-xs text-slate-500 dark:text-slate-400">{me?.email}</p>
          </div>
        </NavLink>
      </div>
    </aside>
  )

  return (
    <div className="flex h-full">
      <div className="hidden lg:block">{sidebar}</div>
      {sidebarOpen && (
        <div className="fixed inset-0 z-40 flex lg:hidden">
          <div className="absolute inset-0 bg-black/30" onClick={() => setSidebarOpen(false)} />
          <div className="relative">{sidebar}</div>
        </div>
      )}
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-14 flex-none items-center gap-3 border-b border-slate-200 bg-white/80 px-4 backdrop-blur dark:border-white/10 dark:bg-[#0e0d16]/80">
          <button className="rounded-lg p-1.5 text-slate-500 lg:hidden" aria-label="Open menu" onClick={() => setSidebarOpen(true)}><Menu size={20} /></button>
          <div className="lg:hidden"><Logo showName={false} /></div>
          <div className="ml-auto flex items-center gap-2">
            <StatusPill />
            <button onClick={toggle} aria-label="Toggle dark mode"
                    className="rounded-lg p-2 text-slate-500 transition hover:bg-slate-100 dark:text-slate-400 dark:hover:bg-white/5">
              {dark ? <Sun size={18} /> : <Moon size={18} />}
            </button>
            <div className="relative">
              <button onClick={() => setMenuOpen((o) => !o)} aria-label="Account menu"
                      className="flex h-9 w-9 items-center justify-center rounded-full bg-brand-100 text-sm font-semibold text-brand-800 ring-brand-300 transition hover:ring-2 dark:bg-brand-900 dark:text-brand-100">
                {initials(me?.display_name)}
              </button>
              {menuOpen && (
                <div className="absolute right-0 z-30 mt-2 w-56 overflow-hidden rounded-xl border border-slate-200 bg-white py-1 text-sm shadow-lg dark:border-white/10 dark:bg-[#1a1926]">
                  <div className="border-b border-slate-100 px-4 py-2.5 dark:border-white/10">
                    <p className="truncate font-medium">{me?.display_name}</p>
                    <p className="truncate text-xs text-slate-500 dark:text-slate-400">{me?.email}</p>
                  </div>
                  <Link to="/account" className="flex items-center gap-2 px-4 py-2 hover:bg-slate-50 dark:hover:bg-white/5"><User size={15} /> Your account</Link>
                  {isAdmin && <Link to="/admin" className="flex items-center gap-2 px-4 py-2 hover:bg-slate-50 dark:hover:bg-white/5"><Shield size={15} /> Admin console</Link>}
                  <button onClick={signOut} className="flex w-full items-center gap-2 px-4 py-2 text-left text-red-600 hover:bg-red-50 dark:text-red-400 dark:hover:bg-red-950/40"><LogOut size={15} /> Sign out</button>
                </div>
              )}
            </div>
          </div>
        </header>
        <main className="min-h-0 flex-1">
          <Outlet context={{ reloadConversations: reload }} />
        </main>
      </div>
    </div>
  )
}
