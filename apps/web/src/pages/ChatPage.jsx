import { ArrowUp, Check, Copy, Eye, FileText, Lock, Package, PanelRightClose, PanelRightOpen, Sparkles, Zap } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { useNavigate, useOutletContext, useParams } from 'react-router-dom'
import { AuthError, getConversation, streamChat } from '../api.js'
import { useSession } from '../session.jsx'

// [S1] or grouped [S1, S2] markers, as written by the model and validated by the API.
const MARKER = /\[(S\d+(?:\s*,\s*S\d+)*)\]/g

const SUGGESTIONS = [
  { title: 'Product prices', question: 'How much does the Alpine 2 Tent cost?' },
  { title: 'Returns', question: 'What is the return window for hiking boots?' },
  { title: 'Internal policy', question: 'What discount can I give for a damaged item?' },
  { title: 'Warranty', question: 'How do I make a warranty claim?' },
]

const REASONS = {
  low_retrieval_score: 'Nothing you can access matches this question closely enough.',
  model_declined: 'The sources found did not answer the question.',
  no_valid_citations: 'The answer could not be backed by a source, so it was withheld.',
  error: 'Something went wrong while answering.',
}

function greeting() {
  const h = new Date().getHours()
  return h < 12 ? 'Good morning' : h < 17 ? 'Good afternoon' : 'Good evening'
}

/** Build the UI's message list from a saved conversation (pairs of question + answer). */
function fromHistory(messages) {
  const items = []
  for (const m of messages) {
    if (m.role === 'user') {
      items.push({ key: `h${m.id}`, question: m.content, viewedAs: m.viewed_as, text: '', result: null })
    } else if (items.length) {
      const last = items[items.length - 1]
      last.result = { answer: m.content, citations: m.citations, abstained: m.abstained,
                      reason: m.reason, model: m.model, took_ms: m.took_ms }
    }
  }
  return items
}

/** Answer text with [S1] markers turned into clickable citation chips. */
function AnswerText({ text, onCite, streaming }) {
  const parts = []
  let last = 0
  for (const match of text.matchAll(MARKER)) {
    parts.push(text.slice(last, match.index))
    for (const ref of match[1].split(',').map((r) => r.trim())) {
      parts.push(
        <button key={`${match.index}-${ref}`} onClick={() => onCite?.(ref)}
                className="mx-0.5 inline-flex h-5 min-w-5 items-center justify-center rounded-md bg-brand-100 px-1 align-[1px] text-[11px] font-semibold text-brand-800 transition hover:bg-brand-200 dark:bg-brand-900 dark:text-brand-100 dark:hover:bg-brand-800">
          {ref.slice(1)}
        </button>,
      )
    }
    last = match.index + match[0].length
  }
  parts.push(text.slice(last))
  return <p className={`whitespace-pre-wrap leading-7 ${streaming ? 'caret' : ''}`}>{parts}</p>
}

function SourceCard({ source, active }) {
  const [title, heading] = source.title.split(' > ')
  const Icon = source.source_table === 'products' ? Package : FileText
  return (
    <div className={`rounded-xl border p-3 transition ${active
      ? 'border-brand-300 bg-brand-50 ring-2 ring-brand-100 dark:border-brand-700 dark:bg-brand-950 dark:ring-brand-900'
      : 'border-slate-200 bg-white dark:border-white/10 dark:bg-white/5'}`}>
      <div className="flex items-start gap-2.5">
        <span className="flex h-6 w-6 flex-none items-center justify-center rounded-md bg-brand-100 text-xs font-semibold text-brand-800 dark:bg-brand-900 dark:text-brand-100">
          {source.ref.slice(1)}
        </span>
        <div className="min-w-0">
          <p className="text-sm font-medium leading-snug">{title}</p>
          {heading && <p className="text-xs text-slate-500 dark:text-slate-400">{heading}</p>}
          <p className="mt-1.5 flex items-center gap-1 text-xs text-slate-500 dark:text-slate-400">
            <Icon size={12} /> {source.source_table === 'products' ? 'Product' : 'Document'}
          </p>
          <div className="mt-2 flex items-center gap-2">
            <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-slate-100 dark:bg-white/10">
              <div className="h-full rounded-full bg-brand-500" style={{ width: `${Math.round(source.score * 100)}%` }} />
            </div>
            <span className="text-[11px] tabular-nums text-slate-500">{Math.round(source.score * 100)}%</span>
          </div>
        </div>
      </div>
    </div>
  )
}

function CopyButton({ text }) {
  const [done, setDone] = useState(false)
  return (
    <button onClick={() => { navigator.clipboard?.writeText(text.replace(MARKER, '')); setDone(true); setTimeout(() => setDone(false), 1500) }}
            className="inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 hover:bg-slate-100 hover:text-slate-700 dark:hover:bg-white/5 dark:hover:text-slate-200">
      {done ? <Check size={12} /> : <Copy size={12} />}{done ? 'Copied' : 'Copy'}
    </button>
  )
}

function Message({ item, onCite }) {
  const { question, text, sources, result, error, viewedAs } = item
  const streaming = !result && !error
  return (
    <div className="space-y-4">
      <div className="flex flex-col items-end gap-1">
        {viewedAs && <span className="flex items-center gap-1 text-xs text-slate-500"><Eye size={12} /> asked as {viewedAs}</span>}
        <div className="max-w-[85%] rounded-2xl rounded-br-md bg-brand-600 px-4 py-2.5 text-[15px] text-white">{question}</div>
      </div>
      <div className="flex gap-3">
        <div className="flex h-8 w-8 flex-none items-center justify-center rounded-lg bg-brand-100 text-brand-700 dark:bg-brand-900 dark:text-brand-200">
          <Zap size={16} strokeWidth={2.4} />
        </div>
        <div className="min-w-0 flex-1 pt-1 text-[15px]">
          {error && <p className="text-red-600 dark:text-red-400">{error}</p>}
          {streaming && !text && (
            <p className="flex items-center gap-2 text-sm text-slate-500">
              <span className="flex gap-1"><span className="dot h-1.5 w-1.5 rounded-full bg-brand-400" /><span className="dot h-1.5 w-1.5 rounded-full bg-brand-400" /><span className="dot h-1.5 w-1.5 rounded-full bg-brand-400" /></span>
              {sources ? 'Writing an answer' : 'Searching documents you can access'}
            </p>
          )}
          {streaming && text && <AnswerText text={text} streaming />}
          {result?.abstained && (
            <div className="flex gap-3 rounded-xl border border-brand-200 bg-brand-50 p-3.5 text-brand-900 dark:border-brand-800 dark:bg-brand-950 dark:text-brand-100">
              <Lock size={18} className="mt-0.5 flex-none" />
              <div>
                <p className="font-medium">{result.answer}</p>
                <p className="mt-0.5 text-sm opacity-80">{REASONS[result.reason] ?? ''}</p>
              </div>
            </div>
          )}
          {result && !result.abstained && <AnswerText text={result.answer} onCite={(ref) => onCite(item, ref)} />}
          {result && !result.abstained && result.citations?.length > 0 && (
            <div className="mt-3 grid gap-2 sm:grid-cols-2 xl:hidden">
              {result.citations.map((c) => (
                <div key={c.ref} className="flex items-start gap-2 rounded-lg border border-slate-200 bg-white px-2.5 py-2 text-xs dark:border-white/10 dark:bg-white/5">
                  <span className="flex h-5 w-5 flex-none items-center justify-center rounded bg-brand-100 font-semibold text-brand-800 dark:bg-brand-900 dark:text-brand-100">{c.ref.slice(1)}</span>
                  <span className="min-w-0">
                    <span className="block font-medium text-slate-700 dark:text-slate-200">{c.title.split(' > ')[0]}</span>
                    <span className="text-slate-500 dark:text-slate-400">{c.title.split(' > ')[1] ?? (c.source_table === 'products' ? 'Product' : 'Document')} · {Math.round(c.score * 100)}%</span>
                  </span>
                </div>
              ))}
            </div>
          )}
          {result && (
            <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-slate-400">
              {!result.abstained && <CopyButton text={result.answer} />}
              <span>{result.model ? result.model.replace('ollama:', '') : 'no model call'}</span>
              {result.took_ms != null && <span>· {(result.took_ms / 1000).toFixed(1)} s</span>}
              {!result.abstained && result.citations?.length > 0 && (
                <button onClick={() => onCite(item, result.citations[0].ref)} className="text-brand-600 hover:underline dark:text-brand-300">
                  · {result.citations.length} source{result.citations.length > 1 ? 's' : ''}
                </button>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

/** The chat: saved conversation (if any) + streaming answers + a sources panel. */
export default function ChatPage() {
  const { id } = useParams()
  const { token, me, isAdmin, viewAs, setViewAs, signOut } = useSession()
  const { reloadConversations } = useOutletContext()
  const navigate = useNavigate()
  const [items, setItems] = useState([])
  const [question, setQuestion] = useState('')
  const [busy, setBusy] = useState(false)
  const [selected, setSelected] = useState(null) // {item key, ref} shown in the sources panel
  const [panelOpen, setPanelOpen] = useState(true)
  const createdHere = useRef(null) // id of a conversation this page just created while streaming
  const abortRef = useRef(null)
  const bottomRef = useRef(null)
  const inputRef = useRef(null)

  // Grow the question box with its content, up to 160 px, and shrink it back after sending.
  useEffect(() => {
    const el = inputRef.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${Math.min(el.scrollHeight, 160)}px`
  }, [question])

  // Opening another conversation (or "New chat") loads it, unless we just created it ourselves.
  useEffect(() => {
    if (id && id === createdHere.current) return
    abortRef.current?.abort()
    setSelected(null)
    if (!id) { setItems([]); return }
    let cancelled = false
    getConversation(token, id)
      .then((c) => {
        if (cancelled) return
        const loaded = fromHistory(c.messages)
        setItems(loaded)
        // Open the sources panel on the latest answer that has sources.
        const last = [...loaded].reverse().find((m) => m.result?.citations?.length)
        if (last) setSelected({ key: last.key, ref: last.result.citations[0].ref })
      })
      .catch((err) => { if (err instanceof AuthError) signOut(); else navigate('/chat') })
    return () => { cancelled = true }
  }, [id, token, navigate, signOut])

  useEffect(() => () => abortRef.current?.abort(), [])
  useEffect(() => { bottomRef.current?.scrollIntoView({ block: 'end', behavior: 'smooth' }) }, [items])

  function update(key, changes) {
    setItems((all) => all.map((m) => (m.key === key ? { ...m, ...changes(m) } : m)))
  }

  async function ask(text) {
    const q = text.trim()
    if (!q || busy) return
    const key = `n${Date.now()}`
    setItems((all) => [...all, { key, question: q, viewedAs: viewAs || null, text: '', sources: null, result: null, error: null }])
    setQuestion('')
    setBusy(true)
    abortRef.current = new AbortController()
    try {
      await streamChat(token, { question: q, conversationId: id, viewAs }, {
        onConversation: (newId) => {
          if (!id) { createdHere.current = newId; navigate(`/chat/${newId}`, { replace: true }) }
        },
        onSources: (sources) => update(key, () => ({ sources })),
        onToken: (piece) => update(key, (m) => ({ text: m.text + piece })),
        onDone: (result) => {
          update(key, () => ({ result }))
          if (result.citations?.length) setSelected({ key, ref: result.citations[0].ref })
        },
      }, abortRef.current.signal)
      reloadConversations()
    } catch (err) {
      if (err.name === 'AbortError') return
      if (err instanceof AuthError) { signOut(); return }
      update(key, () => ({ error: err.message }))
    } finally {
      setBusy(false)
    }
  }

  function handleKey(event) {
    if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); ask(question) }
  }

  const selectedItem = items.find((m) => m.key === selected?.key)
  const panelSources = selectedItem?.result?.citations ?? []

  return (
    <div className="flex h-full">
      <div className="flex min-w-0 flex-1 flex-col">
        {isAdmin && (
          <div className={`flex items-center gap-2 border-b px-4 py-2 text-sm ${viewAs ? 'border-brand-200 bg-brand-50 text-brand-900 dark:border-brand-800 dark:bg-brand-950 dark:text-brand-100' : 'border-slate-200 dark:border-white/10'}`}>
            <Eye size={15} className="text-brand-500" />
            <span className="text-slate-500 dark:text-slate-400">Answer as</span>
            <span className="font-medium">{viewAs || 'yourself'}</span>
            {viewAs && <button onClick={() => setViewAs('')} className="ml-1 text-xs text-brand-600 hover:underline dark:text-brand-300">Back to yourself</button>}
            <button onClick={() => navigate('/admin')} className="ml-auto text-xs text-slate-500 hover:text-brand-600">Change in admin console</button>
          </div>
        )}
        <div className="min-h-0 flex-1 overflow-y-auto">
          <div className="mx-auto max-w-3xl px-4 py-8">
            {items.length === 0 ? (
              <div className="pt-[8vh] text-center">
                <div className="mx-auto flex h-14 w-14 items-center justify-center rounded-2xl bg-brand-600 text-brand-50 shadow-lg shadow-brand-600/20">
                  <Sparkles size={26} />
                </div>
                <h1 className="mt-6 text-3xl font-semibold tracking-tight">
                  {greeting()}, {me?.display_name?.split(' ')[0] ?? 'there'}
                </h1>
                <p className="mt-2 text-slate-500 dark:text-slate-400">
                  Ask about Northwind Gear's products, orders and policies. You'll only get answers from documents you can access.
                </p>
                <div className="mx-auto mt-10 grid max-w-2xl gap-3 sm:grid-cols-2">
                  {SUGGESTIONS.map((s) => (
                    <button key={s.title} onClick={() => ask(s.question)}
                            className="group rounded-2xl border border-slate-200 bg-white p-4 text-left transition hover:-translate-y-0.5 hover:border-brand-300 hover:shadow-md dark:border-white/10 dark:bg-white/5 dark:hover:border-brand-700">
                      <p className="text-xs font-medium text-brand-600 dark:text-brand-300">{s.title}</p>
                      <p className="mt-1 text-sm text-slate-700 dark:text-slate-200">{s.question}</p>
                    </button>
                  ))}
                </div>
              </div>
            ) : (
              <div className="space-y-8">
                {items.map((item) => (
                  <Message key={item.key} item={item}
                           onCite={(it, ref) => { setSelected({ key: it.key, ref }); setPanelOpen(true) }} />
                ))}
                <div ref={bottomRef} />
              </div>
            )}
          </div>
        </div>
        <div className="flex-none px-4 pb-5">
          <form onSubmit={(e) => { e.preventDefault(); ask(question) }}
                className="mx-auto flex max-w-3xl items-end gap-2 rounded-2xl border border-slate-200 bg-white p-2 shadow-sm transition focus-within:border-brand-300 focus-within:ring-4 focus-within:ring-brand-100 dark:border-white/10 dark:bg-white/5 dark:focus-within:ring-brand-900">
            <textarea ref={inputRef} value={question} onChange={(e) => setQuestion(e.target.value)} onKeyDown={handleKey}
                      rows={1} maxLength={1000} placeholder="Ask a question…"
                      className="max-h-40 min-h-[40px] flex-1 resize-none bg-transparent px-2 py-2 text-[15px] outline-none placeholder:text-slate-400" />
            <button type="submit" disabled={busy || !question.trim()} aria-label="Send"
                    className="flex h-10 w-10 flex-none items-center justify-center rounded-xl bg-brand-600 text-white transition hover:bg-brand-700 disabled:bg-slate-200 disabled:text-slate-400 dark:disabled:bg-white/10">
              <ArrowUp size={18} />
            </button>
          </form>
          <p className="mt-2 text-center text-xs text-slate-400">Answers come only from documents you're allowed to read, and cite their sources.</p>
        </div>
      </div>

      {/* Sources panel */}
      {items.length > 0 && (
        panelOpen ? (
          <aside className="hidden w-80 flex-none flex-col border-l border-slate-200 bg-slate-50/60 xl:flex dark:border-white/10 dark:bg-[#12111c]">
            <div className="flex items-center justify-between px-4 py-3">
              <p className="text-sm font-semibold">Sources</p>
              <button onClick={() => setPanelOpen(false)} aria-label="Hide sources" className="rounded p-1 text-slate-400 hover:text-slate-600"><PanelRightClose size={16} /></button>
            </div>
            <div className="flex-1 space-y-2.5 overflow-y-auto px-4 pb-4">
              {panelSources.length === 0 ? (
                <p className="text-sm text-slate-500 dark:text-slate-400">Click a numbered chip in an answer to see where it came from.</p>
              ) : panelSources.map((s) => <SourceCard key={s.ref} source={s} active={s.ref === selected?.ref} />)}
            </div>
          </aside>
        ) : (
          <button onClick={() => setPanelOpen(true)} aria-label="Show sources"
                  className="hidden h-10 flex-none items-center gap-1 self-start px-3 pt-3 text-sm text-slate-500 hover:text-brand-600 xl:flex">
            <PanelRightOpen size={16} />
          </button>
        )
      )}
    </div>
  )
}
