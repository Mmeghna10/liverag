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