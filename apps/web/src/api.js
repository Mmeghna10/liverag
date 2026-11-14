// Every call to the LiveRAG API goes through this file, so the rest of the UI never builds URLs
// or headers itself. Paths start with /api: Vite's dev proxy forwards them to FastAPI.

/** Thrown when the API says 401: the token is missing, wrong or expired. */
export class AuthError extends Error {}

/**
 * Send a JSON request and return the parsed JSON response.
 * @param {string} path      e.g. "/api/v1/me"
 * @param {{token?: string, body?: object}} [options]
 */
async function request(path, { token, body } = {}) {
  const headers = {}
  if (body !== undefined) headers['Content-Type'] = 'application/json'
  if (token) headers.Authorization = `Bearer ${token}`

  const response = await fetch(path, {
    method: body === undefined ? 'GET' : 'POST',
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  if (response.status === 401) throw new AuthError('Please sign in again.')
  if (!response.ok) throw new Error(`Server error (${response.status}). Is the API running?`)
  return response.json()
}

/**
 * Log in; returns the JWT access token.
 * @param {string} email
 * @param {string} password
 * @returns {Promise<string>}
 */
export async function login(email, password) {
  try {
    const data = await request('/api/v1/auth/login', { body: { email, password } })
    return data.access_token
  } catch (err) {
    // On the login form, a 401 means bad credentials, not an expired session.
    if (err instanceof AuthError) throw new Error('Wrong email or password.')
    throw err
  }
}

/**
 * Who is signed in, and which groups they are in (read fresh from the index DB).
 * @param {string} token
 * @returns {Promise<{id: string, email: string, groups: number[]}>}
 */
export function getMe(token) {
  return request('/api/v1/me', { token })
}

/**
 * Ask a question and receive the answer as a stream of Server-Sent Events.
 *
 * We read the stream with fetch() because the browser's EventSource can only send GET requests
 * and cannot add an Authorization header.
 *
 * @param {string} token
 * @param {string} question
 * @param {{
 *   onSources?: (sources: object[]) => void,  // the chunks the model will read (S1, S2, ...)
 *   onToken?: (text: string) => void,         // a small piece of the answer
 *   onDone?: (result: object) => void,        // the validated final answer (always last)
 * }} handlers
 * @param {AbortSignal} [signal]  lets the caller cancel (e.g. on sign-out)
 */
export async function streamChat(token, question, handlers, signal) {
  const response = await fetch('/api/v1/chat/stream', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
    body: JSON.stringify({ question }),
    signal,
  })
  if (response.status === 401) throw new AuthError('Please sign in again.')
  if (!response.ok) throw new Error(`Server error (${response.status}). Is the API running?`)

  const reader = response.body.getReader()
  const decoder = new TextDecoder() // bytes -> text; handles ₹ split across two network packets
  let buffer = ''

  for (;;) {
    const { value, done } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })

    // Events are separated by a blank line. Keep any incomplete last event in the buffer.
    const events = buffer.split('\n\n')
    buffer = events.pop()
    for (const raw of events) {
      let name = 'message'
      let data = ''
      for (const line of raw.split('\n')) {
        if (line.startsWith('event: ')) name = line.slice(7)
        else if (line.startsWith('data: ')) data += line.slice(6)
      }
      if (!data) continue
      const payload = JSON.parse(data)
      if (name === 'sources') handlers.onSources?.(payload.sources)
      else if (name === 'token') handlers.onToken?.(payload.text)
      else if (name === 'done') handlers.onDone?.(payload)
      else if (name === 'error') throw new Error(payload.detail)
    }
  }
}