import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import './index.css'
import AppShell from './components/AppShell.jsx'
import AccountPage from './pages/AccountPage.jsx'
import AdminPage from './pages/AdminPage.jsx'
import AuthPage from './pages/AuthPage.jsx'
import ChatPage from './pages/ChatPage.jsx'
import RequireSession from './components/RequireSession.jsx'
import { SessionProvider } from './session.jsx'

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <SessionProvider>
      <BrowserRouter>
        <Routes>
          <Route path="/login" element={<AuthPage key="login" mode="login" />} />
          <Route path="/signup" element={<AuthPage key="signup" mode="signup" />} />
          <Route element={<RequireSession><AppShell /></RequireSession>}>
            <Route path="/chat" element={<ChatPage />} />
            <Route path="/chat/:id" element={<ChatPage />} />
            <Route path="/account" element={<AccountPage />} />
            <Route path="/admin" element={<AdminPage />} />
          </Route>
          <Route path="*" element={<Navigate to="/chat" replace />} />
        </Routes>
      </BrowserRouter>
    </SessionProvider>
  </StrictMode>,
)
