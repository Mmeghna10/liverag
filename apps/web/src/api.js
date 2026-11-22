// Every call to the LiveRAG API goes through this file, so the rest of the UI never builds URLs
// or headers itself. Paths start with /api: Vite's dev proxy forwards them to FastAPI.

/** Thrown when the API says 401: the token is missing, wrong or expired. */
export class AuthError extends Error {}

/** Turn FastAPI's error body into one readable sentence. */
async function errorMessage(response) {
  try {
    const body = await response.json()
    if (typeof body.detail === 'string') return body.detail
    if (Array.isArray(body.detail) && body.detail[0]?.msg) return body.detail[0].msg
  } catch { /* not JSON */ }
  return `Server error (${response.status}). Is the API running?`
}

/**
 * Send a JSON request and return the parsed JSON response (or null for 204 No Content).
 * @param {string} path
 * @param {{token?: string, method?: string, body?: object}} [options]
 */
async function request(path, { token, method, body } = {}) {
  const headers = {}
  if (body !== undefined) headers['Content-Type'] = 'application/json'
  if (token) headers.Authorization = `Bearer ${token}`
  const response = await fetch(path, {
    method: method || (body === undefined ? 'GET' : 'POST'),
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  if (response.status === 401 && token) throw new AuthError('Please sign in again.')
  if (!response.ok) throw new Error(await errorMessage(response))
  return response.status === 204 ? null : response.json()
}

// ---- account -------------------------------------------------------------------------------
export const login = (email, password) =>
  request('/api/v1/auth/login', { body: { email, password } }).then((d) => d.access_token)
export const signup = (displayName, email, password) =>
  request('/api/v1/auth/signup', { body: { display_name: displayName, email, password } })
    .then((d) => d.access_token)
export const getMe = (token) => request('/api/v1/me', { token })
export const updateMe = (token, displayName) =>
  request('/api/v1/me', { token, method: 'PATCH', body: { display_name: displayName } })
export const changePassword = (token, current, next) =>
  request('/api/v1/me/password', { token, body: { current_password: current, new_password: next } })

// ---- conversations ---------------------------------------------------------------------------
export const listConversations = (token) => request('/api/v1/conversations', { token })
export const getConversation = (token, id) => request(`/api/v1/conversations/${id}`, { token })
export const renameConversation = (token, id, title) =>
  request(`/api/v1/conversations/${id}`, { token, method: 'PATCH', body: { title } })
export const deleteConversation = (token, id) =>
  request(`/api/v1/conversations/${id}`, { token, method: 'DELETE' })

// ---- live status and admin -------------------------------------------------------------------
export const getStatus = (token) => request('/api/v1/status', { token })
export const getPipeline = (token) => request('/api/v1/admin/pipeline', { token })
export const getGroups = (token) => request('/api/v1/admin/groups', { token })
export const getUsers = (token) => request('/api/v1/admin/users', { token })
export const setMembership = (token, userId, groupId, member) =>
  request(`/api/v1/admin/users/${userId}/groups/${groupId}`, {
    token, method: member ? 'PUT' : 'DELETE',
  })

/**
 * Ask a question; the answer arrives as Server-Sent Events. Read with fetch() because the
 * browser's EventSource can only send GET requests and cannot add an Authorization header.
 *
 * @param {string} token
 * @param {{question: string, conversationId?: string, viewAs?: string}} ask
 * @param {{onConversation?: Function, onSources?: Function, onToken?: Function, onDone?: Function}} handlers
 * @param {AbortSignal} [signal]
 */
export async function streamChat(token, { question, conversationId, viewAs }, handlers, signal) {
  const response = await fetch('/api/v1/chat/stream', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
    body: JSON.stringify({
      question,
      conversation_id: conversationId || undefined,
      view_as: viewAs || undefined,
    }),
    signal,
  })
  if (response.status === 401) throw new AuthError('Please sign in again.')
  if (!response.ok) throw new Error(await errorMessage(response))

  const reader = response.body.getReader()
  const decoder = new TextDecoder() // bytes -> text; handles ₹ split across two network packets
  let buffer = ''
  for (;;) {
    const { value, done } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const events = buffer.split('\n\n') // events end with a blank line; keep the unfinished tail
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
      if (name === 'conversation') handlers.onConversation?.(payload.id)
      else if (name === 'sources') handlers.onSources?.(payload.sources)
      else if (name === 'token') handlers.onToken?.(payload.text)
      else if (name === 'done') handlers.onDone?.(payload)
      else if (name === 'error') throw new Error(payload.detail)
    }
  }
}
