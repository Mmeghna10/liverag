// Light / dark mode: remembered in localStorage, otherwise follows Windows' setting.
import { useState } from 'react'

const KEY = 'liverag.theme'

export function useTheme() {
  const [dark, setDark] = useState(() => document.documentElement.classList.contains('dark'))
  function toggle() {
    const next = !dark
    document.documentElement.classList.toggle('dark', next)
    try { localStorage.setItem(KEY, next ? 'dark' : 'light') } catch { /* storage blocked */ }
    setDark(next)
  }
  return { dark, toggle }
}
