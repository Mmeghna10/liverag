import process from 'node:process'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    // 127.0.0.1, not localhost: on Windows Node may listen on IPv6 (::1) only, and mixing the two
    // costs seconds per connection (we hit the same thing with Ollama in Step 12).
    host: '127.0.0.1',
    port: 5173,
    // The browser calls /api/... on this dev server, and Vite forwards it to FastAPI.
    // Same origin for the browser, so no CORS setup is needed in the API.
    proxy: {
      '/api': process.env.API_URL || 'http://127.0.0.1:8000',
    },
  },
})
