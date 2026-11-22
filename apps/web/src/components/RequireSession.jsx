import { Navigate } from 'react-router-dom'
import { useSession } from '../session.jsx'

/** Pages behind this need a signed-in user; everyone else goes to /login. */
export default function RequireSession({ children }) {
  const { token } = useSession()
  return token ? children : <Navigate to="/login" replace />
}
