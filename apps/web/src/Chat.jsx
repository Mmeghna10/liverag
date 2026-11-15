import { useEffect, useRef, useState } from 'react'
import { AuthError, streamChat } from './api.js'

// Same pattern as the API: [S1], or grouped like [S1, S2].
const MARKER = /\[(S\d+(?:\s*,\s*S\d+)*)\]/g

const EXAMPLES = [
  'How much does the Alpine 2 Tent cost?',
  'What discount can I give for a damaged item?',
  'How do I return a damaged item?',
  'Do you sell kayaks?',
]

/**
 * Answer text with [S1] markers turned into small citation badges.
 * @param {{text: string, messageId: number}} props
 */
function AnswerText({ text, messageId }) {
  const parts = []
  let last = 0
  for (const match of text.matchAll(MARKER)) {
    parts.push(text.slice(last, match.index))
    for (const ref of match[1].split(',').map((r) => r.trim())) {
      parts.push(<a key={`${match.index}-${ref}`} className="cite" href={`#src-${messageId}-${ref}`}>{ref}</a>)
    }
    last = match.index + match[0].length
  }
  parts.push(text.slice(last))
  return <p className="answer">{parts}</p>
}

/**
 * One question and its answer.
 * @param {{message: object}} props
 */
function Message({ message }) {
  const { id, question, text, sources, result, error } = message
  return (
    <article className="message">
      <p className="question">{question}</p>

      {error && <p className="error">{error}</p>}

      {/* While streaming, show the text as it arrives. When done, show the validated answer. */}
      {!error && !result && (text
        ? <AnswerText text={text} messageId={id} />
        : <p className="muted">{sources ? 'Writing an answer…' : 'Searching…'}</p>)}

      {result && result.abstained && (
        <p className="answer abstained">
          {result.answer}
          <span className="muted small"> (reason: {result.reason})</span>
        </p>
      )}
      {result && !result.abstained && <AnswerText text={result.answer} messageId={id} />}

      {result && result.citations.length > 0 && (
        <ol className="sources">
          {result.citations.map((c) => (
            <li key={c.ref} id={`src-${id}-${c.ref}`}>
              <span className="cite">{c.ref}</span> {c.title}
              <span className="muted small"> · {c.source_table} · relevance {c.score.toFixed(2)}</span>
            </li>
          ))}
        </ol>
      )}

      {result && (
        <p className="muted small">
          {result.model ?? 'no model call'} · {(result.took_ms / 1000).toFixed(1)} s
          {result.top_score != null && ` · best match ${result.top_score.toFixed(3)}`}
        </p>
      )}
    </article>
  )
}

/**
 * The chat page: a list of questions and streamed answers, and a box to ask the next one.
 * @param {{token: string, onAuthError: () => void}} props
 */
export default function Chat({ token, viewAs, onAuthError }) {
  const [messages, setMessages] = useState([])
  const [question, setQuestion] = useState('')
  const [busy, setBusy] = useState(false)
  const abortRef = useRef(null)
  const bottomRef = useRef(null)

  // Cancel a running answer if this component goes away (e.g. sign out).
  useEffect(() => () => abortRef.current?.abort(), [])

  // Keep the newest message in view while text streams in.
  useEffect(() => { bottomRef.current?.scrollIntoView({ block: 'end' }) }, [messages])

  /** Change one message (by id) without touching the others. */
  function update(id, changes) {
    setMessages((all) => all.map((m) => (m.id === id ? { ...m, ...changes(m) } : m)))
  }

  async function ask(text) {
    const q = text.trim()
    if (!q || busy) return
    const id = Date.now()
    setMessages((all) => [...all, { id, question: q, text: '', sources: null, result: null, error: null }])
    setQuestion('')
    setBusy(true)
    abortRef.current = new AbortController()
    try {
      await streamChat(token, q, {
        onSources: (sources) => update(id, () => ({ sources })),
        onToken: (piece) => update(id, (m) => ({ text: m.text + piece })),
        onDone: (result) => update(id, () => ({ result })),
        }, abortRef.current.signal, viewAs)
    } catch (err) {
      if (err.name === 'AbortError') return
      if (err instanceof AuthError) { onAuthError(); return }
      update(id, () => ({ error: err.message }))
    } finally {
      setBusy(false)
    }
  }

  function handleSubmit(event) {
    event.preventDefault()
    ask(question)
  }

  return (
    <div className="chat">
      <div className="messages">
        {messages.length === 0 && (
          <div className="empty">
            <p className="muted">Ask about products, orders, returns or company policies.</p>
            <div className="chips">
              {EXAMPLES.map((example) => (
                <button key={example} type="button" className="chip" onClick={() => ask(example)}>
                  {example}
                </button>
              ))}
            </div>
          </div>
        )}
        {messages.map((m) => <Message key={m.id} message={m} />)}
        <div ref={bottomRef} />
      </div>

      <form className="ask" onSubmit={handleSubmit}>
        <input value={question} onChange={(e) => setQuestion(e.target.value)}
               placeholder="Ask a question…" maxLength={1000} disabled={busy} />
        <button type="submit" disabled={busy || !question.trim()}>{busy ? '…' : 'Ask'}</button>
      </form>
    </div>
  )
}